"""问题批量处理：批量派单、批量关闭。

约束：
- 条目先去重，succeeded + failed 严格等于去重后的请求数；
- strict 模式先整体校验，任一不允许则整批回滚，不留半成品；
- partial 模式逐条独立提交（SAVEPOINT），返回成功/失败清单；
- 批量关闭必须提供统一理由，并为每条问题追加一条整改流水留痕；
- 通过客户端幂等键保证同一批次重复提交不产生重复结果：
  先占位（IN_FLIGHT），处理完写入结果快照；并发重复提交会得到
  「正在处理中」提示而不是再执行一遍。
"""

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.constants import TRANSITION_ACTIONS, IssueStatus
from app.core.exceptions import DomainError
from app.models import IdempotentRequest, Issue, RectificationRecord
from app.schemas.batch import (
    BatchCloseRequest,
    BatchDispatchRequest,
    BatchItemFailure,
    BatchResult,
)

# 各批量动作允许的源状态
DISPATCHABLE_STATUSES = {IssueStatus.PENDING.value}
CLOSABLE_STATUSES = {
    IssueStatus.PENDING.value,
    IssueStatus.PROCESSING.value,
    IssueStatus.DONE.value,
}

MAX_BATCH = 500

# 幂等记录状态码：0 表示已占位、尚未完成
_IN_FLIGHT = 0


def _dedupe(ids: list[int]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for issue_id in ids:
        if issue_id not in seen:
            seen.add(issue_id)
            result.append(issue_id)
    return result


def begin_idempotent(db: Session, operation: str, key: str | None) -> dict | None:
    """占住幂等键。

    返回 None 表示可以继续执行（调用方随后必须 finish/release）；
    返回 dict 表示命中已完成的快照，应直接回放给客户端。
    """
    if not key:
        return None
    record = db.get(IdempotentRequest, key)
    if record is not None:
        if record.operation != operation:
            raise DomainError("幂等键已用于其他类型的操作")
        if record.status_code == _IN_FLIGHT:
            raise DomainError("该批次正在处理中，请勿重复提交", status_code=409)
        return json.loads(record.response_json)

    db.add(
        IdempotentRequest(
            key=key, operation=operation, status_code=_IN_FLIGHT, response_json=""
        )
    )
    try:
        db.commit()
    except IntegrityError:
        # 并发请求抢先占位
        db.rollback()
        record = db.get(IdempotentRequest, key)
        if record is None:
            raise DomainError("提交冲突，请重试", status_code=409)
        if record.status_code == _IN_FLIGHT:
            raise DomainError("该批次正在处理中，请勿重复提交", status_code=409)
        return json.loads(record.response_json)
    return None


def finish_idempotent(
    db: Session, operation: str, key: str | None, result: BatchResult
) -> None:
    """写入首次执行结果快照（strict 模式与业务数据同一事务提交）。"""
    if not key:
        return
    record = db.get(IdempotentRequest, key)
    if record is None:
        record = IdempotentRequest(key=key, operation=operation)
        db.add(record)
    record.status_code = 200
    record.response_json = json.dumps(result.model_dump(), ensure_ascii=False)


def release_idempotent(db: Session, key: str | None) -> None:
    """执行失败时释放占位，允许客户端修正后用同一键重试。"""
    if not key:
        return
    record = db.get(IdempotentRequest, key)
    if record is not None:
        db.delete(record)
    db.commit()


def _load_issues(db: Session, ids: list[int]) -> dict[int, Issue]:
    if not ids:
        return {}
    # with_for_update 在 PostgreSQL 上行锁，避免并发重复处理；SQLite 上为空操作
    rows = list(db.scalars(select(Issue).where(Issue.id.in_(ids)).with_for_update()))
    return {row.id: row for row in rows}


def _validate(
    issues: dict[int, Issue], ids: list[int], allowed: set[str], action_name: str
) -> list[BatchItemFailure]:
    """整体校验：返回逐条失败原因；全部通过时返回空列表。"""
    failures: list[BatchItemFailure] = []
    for issue_id in ids:
        issue = issues.get(issue_id)
        if issue is None:
            failures.append(BatchItemFailure(id=issue_id, reason="问题不存在或已被删除"))
        elif issue.status not in allowed:
            failures.append(
                BatchItemFailure(
                    id=issue_id,
                    code=issue.code,
                    reason=f"当前状态「{issue.status}」不允许{action_name}",
                )
            )
    return failures


def _reject_strict(db: Session, key: str | None, action: str, failures: list[BatchItemFailure]):
    detail = "；".join(f"#{item.id} {item.reason}" for item in failures[:10])
    # 整批不生效：回滚业务数据并释放幂等占位
    db.rollback()
    release_idempotent(db, key)
    raise DomainError(
        f"{action}未执行（整批不生效），共 {len(failures)} 条不满足条件：{detail}"
    )


def batch_dispatch(db: Session, payload: BatchDispatchRequest) -> BatchResult:
    ids = _dedupe(payload.issue_ids)
    if len(ids) > MAX_BATCH:
        raise DomainError(f"单次最多处理 {MAX_BATCH} 条")

    operation = "issue_batch_dispatch"
    replayed = begin_idempotent(db, operation, payload.idempotency_key)
    if replayed is not None:
        return BatchResult.model_validate(replayed)

    issues = _load_issues(db, ids)
    failures = _validate(issues, ids, DISPATCHABLE_STATUSES, "批量派单")
    if payload.mode == "strict" and failures:
        _reject_strict(db, payload.idempotency_key, "批量派单", failures)

    result = BatchResult(requested=len(ids))
    for issue_id in ids:
        issue = issues.get(issue_id)
        failure = next((item for item in failures if item.id == issue_id), None)
        if failure is not None:
            result.failed.append(failure)
            continue

        if payload.mode == "partial":
            try:
                with db.begin_nested():  # SAVEPOINT：单条失败不影响其他条目
                    from_status = issue.status
                    issue.status = IssueStatus.PROCESSING.value
                    issue.assignee = payload.assignee
                    remark = f"批量派单给：{payload.assignee}"
                    if payload.remark:
                        remark += f"。{payload.remark}"
                    issue.records.append(
                        RectificationRecord(
                            action="批量派单",
                            from_status=from_status,
                            to_status=IssueStatus.PROCESSING.value,
                            operator=payload.operator,
                            remark=remark,
                        )
                    )
                db.commit()
                result.succeeded.append(issue_id)
            except Exception:
                db.rollback()
                result.failed.append(
                    BatchItemFailure(id=issue_id, code=issue.code, reason="处理失败，已跳过")
                )
            continue

        from_status = issue.status
        issue.status = IssueStatus.PROCESSING.value
        issue.assignee = payload.assignee
        remark = f"批量派单给：{payload.assignee}"
        if payload.remark:
            remark += f"。{payload.remark}"
        issue.records.append(
            RectificationRecord(
                action="批量派单",
                from_status=from_status,
                to_status=IssueStatus.PROCESSING.value,
                operator=payload.operator,
                remark=remark,
            )
        )
        result.succeeded.append(issue_id)

    result.message = f"批量派单完成：成功 {len(result.succeeded)} 条"
    if result.failed:
        result.message += f"，失败 {len(result.failed)} 条"

    if payload.mode == "strict":
        finish_idempotent(db, operation, payload.idempotency_key, result)
        db.commit()
    else:
        db.commit()
        finish_idempotent(db, operation, payload.idempotency_key, result)
        db.commit()
    return result


def batch_close(db: Session, payload: BatchCloseRequest) -> BatchResult:
    ids = _dedupe(payload.issue_ids)
    if len(ids) > MAX_BATCH:
        raise DomainError(f"单次最多处理 {MAX_BATCH} 条")
    reason = payload.reason.strip()
    if not reason:
        raise DomainError("批量关闭必须填写统一关闭理由")

    operation = "issue_batch_close"
    replayed = begin_idempotent(db, operation, payload.idempotency_key)
    if replayed is not None:
        return BatchResult.model_validate(replayed)

    issues = _load_issues(db, ids)
    failures = _validate(issues, ids, CLOSABLE_STATUSES, "批量关闭")
    if payload.mode == "strict" and failures:
        _reject_strict(db, payload.idempotency_key, "批量关闭", failures)

    result = BatchResult(requested=len(ids))
    for issue_id in ids:
        issue = issues.get(issue_id)
        failure = next((item for item in failures if item.id == issue_id), None)
        if failure is not None:
            result.failed.append(failure)
            continue

        if payload.mode == "partial":
            try:
                with db.begin_nested():
                    from_status = issue.status
                    action = TRANSITION_ACTIONS.get(
                        (from_status, IssueStatus.CLOSED.value), "关闭"
                    )
                    issue.status = IssueStatus.CLOSED.value
                    issue.closed_at = datetime.now()
                    # 逐条留痕：统一关闭理由写入每条问题的整改流水
                    issue.records.append(
                        RectificationRecord(
                            action=f"批量{action}",
                            from_status=from_status,
                            to_status=IssueStatus.CLOSED.value,
                            operator=payload.operator,
                            remark=f"关闭理由：{reason}",
                        )
                    )
                db.commit()
                result.succeeded.append(issue_id)
            except Exception:
                db.rollback()
                result.failed.append(
                    BatchItemFailure(id=issue_id, code=issue.code, reason="处理失败，已跳过")
                )
            continue

        from_status = issue.status
        action = TRANSITION_ACTIONS.get(
            (from_status, IssueStatus.CLOSED.value), "关闭"
        )
        issue.status = IssueStatus.CLOSED.value
        issue.closed_at = datetime.now()
        # 逐条留痕：统一关闭理由写入每条问题的整改流水
        issue.records.append(
            RectificationRecord(
                action=f"批量{action}",
                from_status=from_status,
                to_status=IssueStatus.CLOSED.value,
                operator=payload.operator,
                remark=f"关闭理由：{reason}",
            )
        )
        result.succeeded.append(issue_id)

    result.message = f"批量关闭完成：成功 {len(result.succeeded)} 条"
    if result.failed:
        result.message += f"，失败 {len(result.failed)} 条"

    if payload.mode == "strict":
        finish_idempotent(db, operation, payload.idempotency_key, result)
        db.commit()
    else:
        db.commit()
        finish_idempotent(db, operation, payload.idempotency_key, result)
        db.commit()
    return result
