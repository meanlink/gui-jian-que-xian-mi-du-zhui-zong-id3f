from sqlalchemy import String, JSON, Double, ForeignKey, Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class BlindReview(Base):
    __tablename__ = 'blind_reviews'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'))
    # 封存内容：逐点分组（tuning/holdout）、验收集参考标签、参考参数、指纹
    seal: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default='sealed')  # sealed/revealing/revealed
    # 揭榜评估状态：pending/running/done/failed，逐候选独立，支持中断恢复
    eval_state: Mapped[str] = mapped_column(String(20), default='pending')
    revealed_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)


class BlindCandidate(Base):
    __tablename__ = 'blind_candidates'
    id: Mapped[int] = mapped_column(primary_key=True)
    review_id: Mapped[int] = mapped_column(ForeignKey('blind_reviews.id'))
    alias: Mapped[str] = mapped_column(String(80))
    # 发起时冻结的候选参数，之后不可换参
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default='frozen')  # frozen/queued/running/done/failed
    # 调参集上的试算结果（非敏感，供分析员调参参考的聚合信息由业务任务承担）
    tuning_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    # 验收集逐点评估结果：揭榜前对非负责人隐藏
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(String(1000), default='')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    finished_at: Mapped[float | None] = mapped_column(Double, nullable=True)
