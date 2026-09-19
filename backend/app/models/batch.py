"""批量操作与异步导出相关模型。"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IdempotentRequest(Base):
    """客户端幂等键 → 首次执行结果快照，防止同一批次重复提交产生重复结果。"""

    __tablename__ = "idempotent_requests"

    key: Mapped[str] = mapped_column(String(64), primary_key=True, comment="客户端幂等键")
    operation: Mapped[str] = mapped_column(String(40), index=True, comment="操作类型")
    status_code: Mapped[int] = mapped_column(Integer, default=200, comment="首次执行的 HTTP 状态码")
    response_json: Mapped[str] = mapped_column(Text, comment="首次执行结果快照")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class IssueExportJob(Base):
    """问题批量导出异步任务：行数多时后台分批写文件，进度可查。"""

    __tablename__ = "issue_export_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, comment="任务标识")
    scope: Mapped[str] = mapped_column(String(20), default="selected", comment="selected / all_filtered")
    status: Mapped[str] = mapped_column(
        String(20), default="pending", index=True, comment="pending/running/succeeded/failed"
    )
    total: Mapped[int] = mapped_column(Integer, default=0, comment="符合条件的总行数")
    processed: Mapped[int] = mapped_column(Integer, default=0, comment="已导出行数")
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="成品文件路径")
    params_json: Mapped[str] = mapped_column(Text, default="{}", comment="导出范围与筛选参数")
    error: Mapped[str | None] = mapped_column(Text, nullable=True, comment="失败原因")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
