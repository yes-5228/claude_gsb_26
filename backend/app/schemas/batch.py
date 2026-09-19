"""批量操作与异步导出的数据结构。"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class BatchMode(StrEnum):
    """批量执行模式：atomic 任一条目不满足则整批不生效；partial 执行可用条目并返回失败清单。"""

    ATOMIC = "atomic"
    PARTIAL = "partial"


class BatchBase(BaseModel):
    client_batch_id: str = Field(
        min_length=8, max_length=64,
        description="客户端幂等键：同一次批量意图使用同一个键，重复提交不会产生重复结果",
    )
    issue_ids: list[int] = Field(min_length=1, max_length=500, description="勾选的问题 ID（可跨页）")
    operator: str = Field(min_length=1, max_length=60, description="操作人")
    mode: BatchMode = Field(default=BatchMode.ATOMIC, description="atomic 整批 / partial 部分执行")


class BatchDispatchIn(BatchBase):
    assignee: str = Field(min_length=1, max_length=60, description="统一指派的整改责任人")
    remark: str | None = Field(default=None, max_length=500, description="派单说明")


class BatchCloseIn(BatchBase):
    reason: str = Field(min_length=1, max_length=500, description="统一关闭理由（必填，逐条留痕）")


class BatchItemSuccess(BaseModel):
    issue_id: int
    code: str


class BatchItemFailure(BaseModel):
    issue_id: int
    code: str | None = None
    reason: str


class BatchResultOut(BaseModel):
    """批量执行结果：success_count + fail_count + skipped_count 恒等于 unique_count，供前端对账。"""

    batch_id: str
    action: str
    mode: str
    applied: bool = Field(description="是否实际写入了问题状态（atomic 校验失败时为 False）")
    replayed: bool = Field(description="是否命中幂等键返回的历史结果")
    requested_count: int = Field(description="客户端提交的条目数（含重复）")
    unique_count: int = Field(description="去重后的条目数")
    success_count: int
    fail_count: int = Field(description="不满足条件的条目数")
    skipped_count: int = Field(description="atomic 整批未生效时被跳过的条目数")
    succeeded: list[BatchItemSuccess]
    failed: list[BatchItemFailure]
    skipped: list[BatchItemSuccess] = Field(description="因整批未生效而未处理的条目")
    created_at: datetime


class IssueExportIn(BaseModel):
    client_job_id: str = Field(
        min_length=8, max_length=64,
        description="客户端幂等键：重复点击/重试返回同一个导出任务",
    )
    issue_ids: list[int] | None = Field(
        default=None, max_length=2000, description="勾选导出时的问题 ID；为空则按筛选条件导出"
    )
    restroom_id: int | None = None
    district: str | None = None
    status: str | None = None
    open_only: bool = False
    category: str | None = None
    severity: str | None = None
    keyword: str | None = None
    overdue: bool | None = None
    date_from: str | None = Field(default=None, description="YYYY-MM-DD")
    date_to: str | None = Field(default=None, description="YYYY-MM-DD")


class ExportJobOut(BaseModel):
    job_id: str
    status: str
    total_rows: int
    processed_rows: int
    progress: float = Field(description="0-1 的完成比例")
    file_name: str | None = None
    download_url: str | None = None
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
