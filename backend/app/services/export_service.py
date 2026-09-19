"""问题批量异步导出。

- 导出任务落库（IssueExportJob），后台线程分批查询写 CSV；
- 先写临时文件（.part），全部成功后原子改名，中途失败删除临时文件，
  绝不留下半份文件；
- 进度（processed/total）实时更新，前端轮询；
- 幂等键复用既有任务，重复提交不产生第二个任务、不重复执行。
"""

import csv
import json
import os
import threading
import uuid
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func, select

from app.core.config import settings
from app.core.constants import OPEN_ISSUE_STATUSES
from app.core.database import SessionLocal
from app.core.exceptions import DomainError, NotFoundError
from app.models import IdempotentRequest, Issue, IssueExportJob, Restroom
from app.schemas.batch import IssueExportCreate
from app.services.issue_service import _apply_issue_filters

EXPORT_BATCH_SIZE = 500

CSV_HEADERS = [
    "问题编号",
    "标题",
    "问题描述",
    "所属公厕",
    "区域",
    "问题分类",
    "严重程度",
    "整改状态",
    "上报人",
    "整改责任人",
    "上报时间",
    "整改期限",
    "关闭时间",
]

# 导出任务幂等占位与结果共用 idempotent_requests 表
_IN_FLIGHT = 0
_DONE = 200


def _export_dir() -> Path:
    path = Path(settings.export_dir).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _parse_filters(payload: IssueExportCreate) -> dict:
    parsed: dict = {}
    date_keys = {"date_from", "date_to"}
    for key in (
        "district",
        "status",
        "category",
        "severity",
        "keyword",
        "overdue",
    ):
        value = getattr(payload, key)
        if value not in (None, ""):
            parsed[key] = value
    if payload.open_only:
        parsed["statuses"] = list(OPEN_ISSUE_STATUSES)
    for key in date_keys:
        value = getattr(payload, key)
        if value:
            try:
                parsed[key] = date.fromisoformat(value)
            except ValueError:
                raise DomainError(f"{key} 日期格式应为 YYYY-MM-DD")
    return parsed


def _reserve_idempotent(db, key: str | None) -> str | None:
    """复用幂等键对应的既有任务；无则占位。返回既有任务的 task_id。"""
    if not key:
        return None
    record = db.get(IdempotentRequest, key)
    if record is not None:
        if record.operation != "issue_export":
            raise DomainError("幂等键已用于其他类型的操作")
        if record.status_code == _IN_FLIGHT:
            raise DomainError("该导出任务正在创建中，请勿重复提交", status_code=409)
        return record.response_json  # 存放 task_id
    db.add(
        IdempotentRequest(
            key=key, operation="issue_export", status_code=_IN_FLIGHT, response_json=""
        )
    )
    try:
        db.commit()
    except Exception:
        # 并发请求抢先占位
        db.rollback()
        record = db.get(IdempotentRequest, key)
        if record is None:
            raise DomainError("提交冲突，请重试", status_code=409)
        if record.status_code == _IN_FLIGHT:
            raise DomainError("该导出任务正在创建中，请勿重复提交", status_code=409)
        return record.response_json
    return None


def _resolve_total(db, payload: IssueExportCreate, filters: dict, ids: list[int]) -> int:
    """建任务时确定总数（筛选后的实际匹配数），进度条立刻有分母。"""
    base = _apply_issue_filters(select(Issue.id), **filters)
    if payload.scope == "selected":
        if not ids:
            raise DomainError("勾选导出时至少选择一条问题")
        if len(ids) > 20000:
            raise DomainError("单次导出最多 20000 条")
        base = base.where(Issue.id.in_(ids))
    return db.scalar(select(func.count()).select_from(base.subquery())) or 0


def create_export_job(db, payload: IssueExportCreate) -> tuple[IssueExportJob, bool]:
    """创建导出任务。返回 (任务, 是否为幂等重放)。"""
    filters = _parse_filters(payload)
    ids = list(dict.fromkeys(payload.issue_ids or []))
    if payload.scope == "selected" and not ids:
        raise DomainError("勾选导出时至少选择一条问题")
    if len(ids) > 20000:
        raise DomainError("单次导出最多 20000 条")

    existing_task_id = _reserve_idempotent(db, payload.idempotency_key)
    if existing_task_id:
        existing = db.scalar(
            select(IssueExportJob).where(IssueExportJob.task_id == existing_task_id)
        )
        if existing is None:
            raise DomainError("原导出任务已不存在")
        return existing, True

    total = _resolve_total(db, payload, filters, ids)

    try:
        job = IssueExportJob(
            task_id=uuid.uuid4().hex,
            scope=payload.scope,
            status="pending",
            total=total,
            processed=0,
            params_json=json.dumps(
                {
                    "ids": ids,
                    "filters": {
                        key: (value.isoformat() if isinstance(value, date) else value)
                        for key, value in filters.items()
                    },
                },
                ensure_ascii=False,
            ),
        )
        db.add(job)
        if payload.idempotency_key:
            # 占位记录改写为 task_id，任务状态由 job 本身表达
            record = db.get(IdempotentRequest, payload.idempotency_key)
            record.response_json = job.task_id
            record.status_code = _DONE
        db.commit()
    except Exception:
        # 建任务失败：释放幂等占位，允许客户端用同一键重试
        db.rollback()
        _release_idempotent(db, payload.idempotency_key)
        raise
    db.refresh(job)

    thread = threading.Thread(target=_run_export, args=(job.id,), daemon=True)
    thread.start()
    return job, False


def _release_idempotent(db, key: str | None) -> None:
    if not key:
        return
    record = db.get(IdempotentRequest, key)
    if record is not None:
        db.delete(record)
        db.commit()


def get_job(db, task_id: str) -> IssueExportJob:
    job = db.scalar(select(IssueExportJob).where(IssueExportJob.task_id == task_id))
    if job is None:
        raise NotFoundError("导出任务不存在")
    return job


def _row_values(issue: Issue, restrooms: dict[int, Restroom]) -> list[str]:
    restroom = restrooms.get(issue.restroom_id)

    def fmt(value: datetime | None) -> str:
        return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""

    return [
        issue.code,
        issue.title,
        issue.description or "",
        restroom.name if restroom else f"#{issue.restroom_id}",
        restroom.district if restroom else "",
        issue.category,
        issue.severity,
        issue.status,
        issue.reporter or "",
        issue.assignee or "",
        fmt(issue.report_time),
        fmt(issue.deadline),
        fmt(issue.closed_at),
    ]


def _run_export(job_id: int) -> None:
    """后台执行：分批读取 → 写临时文件 → 原子改名；任何失败都清理半成品。"""
    db = SessionLocal()
    tmp_path: Path | None = None
    final_path: Path | None = None
    try:
        job = db.get(IssueExportJob, job_id)
        params = json.loads(job.params_json or "{}")
        filters_raw = params.get("filters", {})
        filters = {}
        for key, value in filters_raw.items():
            if key in ("date_from", "date_to") and value:
                filters[key] = date.fromisoformat(value)
            elif key == "statuses":
                filters[key] = value
            elif value not in (None, ""):
                filters[key] = value

        directory = _export_dir()
        tmp_path = directory / f"issues_{job.task_id}.csv.part"
        final_path = directory / f"issues_{job.task_id}.csv"

        job.status = "running"
        db.commit()

        if job.scope == "selected":
            ids = list(dict.fromkeys(params.get("ids", [])))
            base_stmt = _apply_issue_filters(select(Issue), **filters).where(Issue.id.in_(ids))
        else:
            base_stmt = _apply_issue_filters(select(Issue), **filters)
        # 按 id 键集翻页：并发插入/删除时不会像 offset 那样漏行或重复
        base_stmt = base_stmt.order_by(Issue.id.asc())

        processed = 0
        last_id = 0
        # utf-8-sig 让 Excel 直接打开不乱码
        with open(tmp_path, "w", encoding="utf-8-sig", newline="") as fp:
            writer = csv.writer(fp)
            writer.writerow(CSV_HEADERS)
            restrooms: dict[int, Restroom] = {}
            while True:
                batch = list(
                    db.scalars(
                        base_stmt.where(Issue.id > last_id).limit(EXPORT_BATCH_SIZE)
                    )
                )
                if not batch:
                    break
                missing = {
                    issue.restroom_id
                    for issue in batch
                    if issue.restroom_id not in restrooms
                }
                if missing:
                    for restroom in db.scalars(
                        select(Restroom).where(Restroom.id.in_(missing))
                    ):
                        restrooms[restroom.id] = restroom
                for issue in batch:
                    writer.writerow(_row_values(issue, restrooms))
                processed += len(batch)
                last_id = batch[-1].id

                job = db.get(IssueExportJob, job_id)
                job.processed = processed
                if not job.total:
                    job.total = processed
                db.commit()

                if len(batch) < EXPORT_BATCH_SIZE:
                    break

        # 全部写完后原子改名，成品文件只可能是完整的
        os.replace(tmp_path, final_path)

        job = db.get(IssueExportJob, job_id)
        job.status = "succeeded"
        job.processed = processed
        job.total = processed if not job.total else job.total
        job.file_path = str(final_path)
        job.finished_at = datetime.now()
        db.commit()
    except Exception as exc:  # noqa: BLE001 - 后台线程需兜住所有异常并落库
        db.rollback()
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        job = db.get(IssueExportJob, job_id)
        if job is not None:
            job.status = "failed"
            job.error = str(exc)[:500] or "导出失败"
            job.finished_at = datetime.now()
            db.commit()
    finally:
        db.close()


def get_download_path(db, task_id: str) -> tuple[Path, str]:
    job = get_job(db, task_id)
    if job.status != "succeeded":
        raise DomainError("导出尚未完成或已失败", status_code=409)
    path = Path(job.file_path)
    if not path.exists():
        # 成品文件丢失（如被运维清理），标记失败而不是给用户半个/空文件
        job.status = "failed"
        job.error = "导出文件已失效，请重新导出"
        db.commit()
        raise DomainError("导出文件已失效，请重新导出", status_code=409)
    return path, f"问题列表导出_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv"
