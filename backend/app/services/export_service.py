"""问题列表异步导出：后台线程分块写 CSV，进度落库，原子产出文件。

不产生半份文件的约定：
- 全程写入与成品同目录的 `.part` 临时文件；
- 仅在全部行写完后 `os.replace` 原子改名为正式文件；
- 任何中途失败都会删除临时文件，正式路径要么不存在、要么是完整文件。
"""

import csv
import os
import threading
import uuid
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.constants import OPEN_ISSUE_STATUSES
from app.core.database import SessionLocal
from app.core.exceptions import DomainError, NotFoundError
from app.models import ExportJob, Issue
from app.schemas.batch import ExportJobOut, IssueExportIn
from app.services import issue_service

# 每块行数：决定进度更新粒度
EXPORT_CHUNK_SIZE = 500

CSV_HEADER = [
    "问题编号",
    "标题",
    "分类",
    "严重程度",
    "状态",
    "所属公厕",
    "区域",
    "责任人",
    "上报人",
    "上报时间",
    "整改期限",
    "关闭时间",
    "是否超期",
    "问题描述",
]

STATUS_RUNNING = {"pending", "running"}


def _export_dir() -> Path:
    directory = Path(settings.export_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _get_by_job_id(db: Session, job_id: str) -> ExportJob | None:
    return db.scalar(select(ExportJob).where(ExportJob.job_id == job_id))


def get_export_job(db: Session, job_id: str) -> ExportJob:
    job = _get_by_job_id(db, job_id)
    if job is None:
        raise NotFoundError(f"导出任务 {job_id} 不存在")
    return job


def to_job_out(job: ExportJob) -> ExportJobOut:
    if job.status == "success":
        progress = 1.0
    elif job.total_rows:
        progress = round(min(job.processed_rows / job.total_rows, 1.0), 4)
    else:
        progress = 0.0
    return ExportJobOut(
        job_id=job.job_id,
        status=job.status,
        total_rows=job.total_rows,
        processed_rows=job.processed_rows,
        progress=progress,
        file_name=job.file_name or None,
        download_url=(
            f"{settings.api_prefix}/issues/exports/{job.job_id}/download"
            if job.status == "success"
            else None
        ),
        error=job.error,
        created_at=job.created_at,
        finished_at=job.finished_at,
    )


def create_export_job(db: Session, payload: IssueExportIn) -> ExportJob:
    """创建导出任务并立即后台执行；client_job_id 幂等，重复创建返回同一任务。"""
    replay = db.scalar(
        select(ExportJob).where(ExportJob.client_job_id == payload.client_job_id)
    )
    if replay is not None:
        return replay

    job = ExportJob(
        job_id=f"EX-{datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}",
        client_job_id=payload.client_job_id,
        status="pending",
        filters=payload.model_dump(exclude={"client_job_id"}),
        file_name=f"issues-export-{uuid.uuid4().hex[:12]}.csv",
    )
    db.add(job)
    db.commit()

    thread = threading.Thread(target=run_export, args=(job.job_id,), daemon=True)
    thread.start()
    return job


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DomainError(f"日期格式不正确：{value}，应为 YYYY-MM-DD") from exc


def _build_stmt(filters: dict):
    """根据任务快照构造导出查询：勾选导出按 ID，否则按筛选条件。"""
    stmt = select(Issue).options(selectinload(Issue.restroom))
    issue_ids = filters.get("issue_ids")
    if issue_ids:
        unique_ids = list(dict.fromkeys(issue_ids))
        return stmt.where(Issue.id.in_(unique_ids))
    statuses = list(OPEN_ISSUE_STATUSES) if filters.get("open_only") else None
    filtered = issue_service.build_issue_stmt(
        restroom_id=filters.get("restroom_id"),
        district=filters.get("district"),
        status=filters.get("status"),
        statuses=statuses,
        category=filters.get("category"),
        severity=filters.get("severity"),
        keyword=filters.get("keyword"),
        overdue=filters.get("overdue"),
        date_from=_parse_date(filters.get("date_from")),
        date_to=_parse_date(filters.get("date_to")),
    )
    # 复用列表过滤条件，并补上 restroom 的预加载
    return filtered.options(selectinload(Issue.restroom))


def _iter_chunks(db: Session, stmt, chunk_size: int = EXPORT_CHUNK_SIZE):
    """按主键游标分块读取，避免大 OFFSET，也避免导出中途翻页错位。"""
    last_id = 0
    while True:
        chunk = list(
            db.scalars(stmt.where(Issue.id > last_id).order_by(Issue.id).limit(chunk_size))
        )
        if not chunk:
            return
        yield chunk
        last_id = chunk[-1].id


def _fmt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _row(issue: Issue) -> list[str]:
    restroom = issue.restroom
    return [
        issue.code,
        issue.title,
        issue.category,
        issue.severity,
        issue.status,
        restroom.name if restroom else "",
        restroom.district if restroom else "",
        issue.assignee,
        issue.reporter,
        _fmt(issue.report_time),
        _fmt(issue.deadline),
        _fmt(issue.closed_at),
        "是" if issue_service.is_overdue(issue) else "否",
        issue.description,
    ]


def run_export(job_id: str) -> None:
    """后台执行体：独立会话，分块写临时文件，成功后原子改名，失败清理现场。"""
    tmp_path: Path | None = None
    with SessionLocal() as db:
        job = _get_by_job_id(db, job_id)
        if job is None:
            return
        try:
            stmt = _build_stmt(job.filters or {})
            total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
            job.status = "running"
            job.total_rows = total
            db.commit()

            final_path = _export_dir() / job.file_name
            tmp_path = final_path.with_suffix(".part")
            with tmp_path.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(CSV_HEADER)
                for chunk in _iter_chunks(db, stmt):
                    for issue in chunk:
                        writer.writerow(_row(issue))
                    job.processed_rows += len(chunk)
                    db.commit()

            # 全部写完才允许正式文件出现：同目录原子改名
            os.replace(tmp_path, final_path)
            tmp_path = None
            job.status = "success"
            job.file_path = str(final_path)
            job.finished_at = datetime.now()
            db.commit()
        except Exception as exc:  # noqa: BLE001 - 任何失败都必须收口为 failed 状态
            db.rollback()
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            job.status = "failed"
            job.error = str(exc)[:500]
            job.finished_at = datetime.now()
            db.commit()


def get_download_file(db: Session, job_id: str) -> tuple[Path, str]:
    """返回 (文件路径, 下载文件名)；任务未完成或文件缺失时拒绝。"""
    job = get_export_job(db, job_id)
    if job.status in STATUS_RUNNING:
        raise DomainError("导出任务尚未完成，请稍后再下载", status_code=409)
    if job.status != "success" or not job.file_path:
        raise DomainError(f"导出任务未完成：{job.error or job.status}", status_code=409)
    path = Path(job.file_path)
    if not path.exists():
        raise NotFoundError("导出文件已被清理，请重新导出")
    display_name = f"问题导出-{job.created_at:%Y%m%d%H%M%S}.csv"
    return path, display_name
