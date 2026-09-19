"""批量操作（派单 / 关闭 / 导出）出入参结构。"""

from typing import Literal

from pydantic import BaseModel, Field


class BatchItemFailure(BaseModel):
    """单条失败项：前端据此渲染失败清单。"""

    id: int
    code: str | None = None
    reason: str


class BatchResult(BaseModel):
    """批量操作结果：succeeded + failed 严格等于请求去重后的条目数。"""

    requested: int = Field(description="请求条目数（去重后）")
    succeeded: list[int] = Field(default_factory=list, description="成功的问题 ID")
    failed: list[BatchItemFailure] = Field(default_factory=list, description="逐条失败原因")
    message: str = ""

    @property
    def success_count(self) -> int:
        return len(self.succeeded)

    @property
    def fail_count(self) -> int:
        return len(self.failed)


class BatchDispatchRequest(BaseModel):
    issue_ids: list[int] = Field(min_length=1, max_length=500, description="要派单的问题 ID 列表")
    assignee: str = Field(min_length=1, max_length=60, description="统一指派的整改责任人")
    operator: str = Field(min_length=1, max_length=60, description="操作人")
    remark: str | None = Field(default=None, max_length=500, description="统一派单说明")
    mode: Literal["strict", "partial"] = Field(
        default="strict",
        description="strict=任一状态不允许则整批不生效；partial=逐条处理并返回成败清单",
    )
    idempotency_key: str | None = Field(
        default=None, min_length=8, max_length=64, description="客户端生成的幂等键"
    )


class BatchCloseRequest(BaseModel):
    issue_ids: list[int] = Field(min_length=1, max_length=500, description="要关闭的问题 ID 列表")
    reason: str = Field(min_length=1, max_length=500, description="统一关闭理由（必填）")
    operator: str = Field(min_length=1, max_length=60, description="操作人")
    mode: Literal["strict", "partial"] = Field(
        default="strict",
        description="strict=任一状态不允许则整批不生效；partial=逐条处理并返回成败清单",
    )
    idempotency_key: str | None = Field(
        default=None, min_length=8, max_length=64, description="客户端生成的幂等键"
    )


class IssueExportCreate(BaseModel):
    scope: Literal["selected", "all_filtered"] = Field(
        default="selected", description="selected=按勾选 ID；all_filtered=按当前筛选条件"
    )
    issue_ids: list[int] | None = Field(default=None, max_length=20000, description="勾选的问题 ID")
    # all_filtered 时使用的筛选参数
    district: str | None = None
    status: str | None = None
    open_only: bool = False
    category: str | None = None
    severity: str | None = None
    keyword: str | None = None
    overdue: bool | None = None
    date_from: str | None = None
    date_to: str | None = None
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=64)


class IssueExportOut(BaseModel):
    task_id: str
    status: Literal["pending", "running", "succeeded", "failed"]
    scope: str
    total: int
    processed: int
    error: str | None = None
    download_url: str | None = None
    replayed: bool = Field(default=False, description="是否命中同一幂等键的既有任务")
