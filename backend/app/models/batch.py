"""批量操作与异步导出任务模型。"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BatchOperation(Base):
    """一次批量派单/关闭的留痕记录，同时充当幂等键的载体。

    client_batch_id 由前端在每次发起批次时生成，重复提交（双击、重试、
    网络重发）会命中唯一约束，直接返回首次的执行结果，不会重复生效。
    """

    __tablename__ = "batch_operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        String(40), unique=True, index=True, comment="服务端批次号"
    )
    client_batch_id: Mapped[str] = mapped_column(
        String(64), unique=True, comment="客户端幂等键"
    )
    action: Mapped[str] = mapped_column(String(20), comment="batch_dispatch / batch_close")
    mode: Mapped[str] = mapped_column(String(10), comment="atomic 整批 / partial 部分执行")
    operator: Mapped[str] = mapped_column(String(60), default="", comment="操作人")
    reason: Mapped[str] = mapped_column(Text, default="", comment="统一理由/说明")
    requested_count: Mapped[int] = mapped_column(Integer, default=0, comment="客户端提交的条目数")
    unique_count: Mapped[int] = mapped_column(Integer, default=0, comment="去重后的条目数")
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(
        Integer, default=0, comment="整批未生效时被跳过的条目数"
    )
    applied: Mapped[bool] = mapped_column(default=False, comment="是否实际写入了问题状态")
    result: Mapped[dict] = mapped_column(
        JSON, default=dict, comment="成功/失败/跳过清单：{succeeded, failed, skipped}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, index=True, comment="创建时间"
    )


class ExportJob(Base):
    """异步导出任务：进度落库，文件先写临时名再原子改名，失败不留半份。"""

    __tablename__ = "export_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(String(40), unique=True, index=True, comment="任务号")
    client_job_id: Mapped[str] = mapped_column(
        String(64), unique=True, comment="客户端幂等键，重复创建返回同一任务"
    )
    status: Mapped[str] = mapped_column(
        String(10), default="pending", index=True,
        comment="pending / running / success / failed",
    )
    filters: Mapped[dict] = mapped_column(JSON, default=dict, comment="导出时的筛选条件快照")
    total_rows: Mapped[int] = mapped_column(Integer, default=0, comment="待导出总行数")
    processed_rows: Mapped[int] = mapped_column(Integer, default=0, comment="已写入行数")
    file_path: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="最终文件路径（仅成功后落盘）"
    )
    file_name: Mapped[str] = mapped_column(String(120), default="", comment="下载文件名")
    error: Mapped[str | None] = mapped_column(Text, nullable=True, comment="失败原因")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
