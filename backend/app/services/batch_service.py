"""批量派单 / 批量关闭：幂等、原子或部分执行、逐条留痕。

关键约束：
- client_batch_id 是幂等键，重复提交（含并发）返回首次结果，不重复生效；
- atomic 模式任一条目不满足则整批不生效；partial 模式执行可用条目并给出失败清单；
- 无论哪种模式，success_count + fail_count + skipped_count 恒等于去重后的条目数，便于对账；
- 所有问题状态变更、整改流水、批次记录在同一个事务里提交，不存在"改了一半"。
"""

import uuid
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.constants import ISSUE_TRANSITIONS, IssueStatus
from app.models import BatchOperation, Issue, RectificationRecord
from app.schemas.batch import (
    BatchCloseIn,
    BatchDispatchIn,
    BatchMode,
    BatchResultOut,
)
from app.services import restroom_service

ACTION_DISPATCH = "batch_dispatch"
ACTION_CLOSE = "batch_close"

ACTION_LABELS = {
    ACTION_DISPATCH: "批量派单",
    ACTION_CLOSE: "批量关闭",
}


def _new_batch_id() -> str:
    return f"PL-{datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}"


def _find_replay(db: Session, client_batch_id: str) -> BatchOperation | None:
    return db.scalar(
        select(BatchOperation).where(BatchOperation.client_batch_id == client_batch_id)
    )


def to_out(operation: BatchOperation, *, replayed: bool) -> BatchResultOut:
    result = operation.result or {}
    return BatchResultOut(
        batch_id=operation.batch_id,
        action=operation.action,
        mode=operation.mode,
        applied=operation.applied,
        replayed=replayed,
        requested_count=operation.requested_count,
        unique_count=operation.unique_count,
        success_count=operation.success_count,
        fail_count=operation.fail_count,
        skipped_count=operation.skipped_count,
        succeeded=result.get("succeeded", []),
        failed=result.get("failed", []),
        skipped=result.get("skipped", []),
        created_at=operation.created_at,
    )


def _execute_batch(
    db: Session,
    payload: BatchDispatchIn | BatchCloseIn,
    *,
    action: str,
    reason: str,
    validate: Callable[[Issue], str | None],
    apply_change: Callable[[Issue, str], None],
) -> tuple[BatchOperation, bool]:
    """批量执行骨架，返回 (批次记录, 是否幂等重放)。

    validate 返回 None 表示可执行，否则为失败原因。
    """
    replay = _find_replay(db, payload.client_batch_id)
    if replay is not None:
        return replay, True

    requested_count = len(payload.issue_ids)
    # 去重并保持客户端提交顺序，跨页勾选可能混入重复项
    unique_ids = list(dict.fromkeys(payload.issue_ids))

    issues = db.scalars(select(Issue).where(Issue.id.in_(unique_ids))).all()
    issue_map = {issue.id: issue for issue in issues}

    candidates: list[Issue] = []
    failed: list[dict] = []
    for issue_id in unique_ids:
        issue = issue_map.get(issue_id)
        if issue is None:
            failed.append({"issue_id": issue_id, "code": None, "reason": "问题不存在或已被删除"})
            continue
        error = validate(issue)
        if error is not None:
            failed.append({"issue_id": issue_id, "code": issue.code, "reason": error})
        else:
            candidates.append(issue)

    mode = payload.mode.value if isinstance(payload.mode, BatchMode) else payload.mode
    applied = bool(candidates) and (mode == BatchMode.PARTIAL.value or not failed)
    effective = candidates if applied else []
    # atomic 整批未生效时，满足条件的条目计入 skipped，与真正的失败项区分开
    skipped = [] if applied else candidates

    operation = BatchOperation(
        batch_id=_new_batch_id(),
        client_batch_id=payload.client_batch_id,
        action=action,
        mode=mode,
        operator=payload.operator,
        reason=reason,
        requested_count=requested_count,
        unique_count=len(unique_ids),
        success_count=len(effective),
        fail_count=len(failed),
        skipped_count=len(skipped),
        applied=applied,
        result={
            "succeeded": [{"issue_id": issue.id, "code": issue.code} for issue in effective],
            "failed": failed,
            "skipped": [{"issue_id": issue.id, "code": issue.code} for issue in skipped],
        },
    )

    # 状态变更、逐条整改流水、批次记录同事务提交，任何一步失败整体回滚
    for issue in effective:
        apply_change(issue, operation.batch_id)
    db.add(operation)
    try:
        db.commit()
    except IntegrityError:
        # 并发下同一 client_batch_id 已被其他请求写入：回滚后返回首次结果
        db.rollback()
        replay = _find_replay(db, payload.client_batch_id)
        if replay is not None:
            return replay, True
        raise

    for restroom_id in {issue.restroom_id for issue in effective}:
        restroom_service.touch(db, restroom_id)
    return operation, False


def _validate_dispatch(issue: Issue) -> str | None:
    if issue.status != IssueStatus.PENDING.value:
        return f"当前状态「{issue.status}」不允许派单，仅「{IssueStatus.PENDING.value}」可派单"
    return None


def _validate_close(issue: Issue) -> str | None:
    if IssueStatus.CLOSED.value not in ISSUE_TRANSITIONS.get(issue.status, []):
        return f"当前状态「{issue.status}」不允许关闭"
    return None


def batch_dispatch(db: Session, payload: BatchDispatchIn) -> tuple[BatchOperation, bool]:
    """批量派单：待整改 -> 整改中，并统一指派责任人。"""

    def apply(issue: Issue, batch_id: str) -> None:
        from_status = issue.status
        issue.status = IssueStatus.PROCESSING.value
        issue.assignee = payload.assignee
        remark = f"[批次 {batch_id}] 派单给 {payload.assignee}"
        if payload.remark:
            remark += f"；{payload.remark}"
        issue.records.append(
            RectificationRecord(
                action=ACTION_LABELS[ACTION_DISPATCH],
                from_status=from_status,
                to_status=IssueStatus.PROCESSING.value,
                operator=payload.operator,
                remark=remark,
            )
        )

    return _execute_batch(
        db,
        payload,
        action=ACTION_DISPATCH,
        reason=payload.remark or f"派单给 {payload.assignee}",
        validate=_validate_dispatch,
        apply_change=apply,
    )


def batch_close(db: Session, payload: BatchCloseIn) -> tuple[BatchOperation, bool]:
    """批量关闭：统一理由必填，每条问题各写一条整改流水。"""

    def apply(issue: Issue, batch_id: str) -> None:
        from_status = issue.status
        issue.status = IssueStatus.CLOSED.value
        issue.closed_at = datetime.now()
        issue.records.append(
            RectificationRecord(
                action=ACTION_LABELS[ACTION_CLOSE],
                from_status=from_status,
                to_status=IssueStatus.CLOSED.value,
                operator=payload.operator,
                remark=f"[批次 {batch_id}] {payload.reason}",
            )
        )

    return _execute_batch(
        db,
        payload,
        action=ACTION_CLOSE,
        reason=payload.reason,
        validate=_validate_close,
        apply_change=apply,
    )
