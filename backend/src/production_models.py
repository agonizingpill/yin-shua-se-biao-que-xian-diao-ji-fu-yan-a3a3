from sqlalchemy import String,Float,ForeignKey,JSON,UniqueConstraint,CheckConstraint
from sqlalchemy.orm import Mapped,mapped_column
from .db import Base
from . import dispatch_models as _paper_batches  # noqa:F401 注册paper_batches外键表
class PlateSet(Base):
    __tablename__='plate_sets'
    __table_args__=(UniqueConstraint('job_id','version'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    job_id:Mapped[int]=mapped_column(ForeignKey('print_jobs.id'))
    version:Mapped[str]=mapped_column(String(40))
    source_note:Mapped[str]=mapped_column(String(300))
    registration_error_mm:Mapped[float|None]=mapped_column(Float,nullable=True)
    state:Mapped[str]=mapped_column(String(24),default='registered')
    approved_by:Mapped[str]=mapped_column(String(100),default='')
class PressSample(Base):
    # quantity 为“当前领用批次内截至本次的累计已印张数”，换批后重新累计；
    # batch_snapshot 在创建时冻结领用批次依据，后续换批/退纸不回写。
    __tablename__='press_samples'
    __table_args__=(UniqueConstraint('job_id','sequence'),UniqueConstraint('job_id','request_id'),CheckConstraint('quantity>0 AND sequence>0'))
    id:Mapped[int]=mapped_column(primary_key=True)
    job_id:Mapped[int]=mapped_column(ForeignKey('print_jobs.id'))
    proof_id:Mapped[int]=mapped_column(ForeignKey('proof_sheets.id'),unique=True)
    plate_id:Mapped[int]=mapped_column(ForeignKey('plate_sets.id'))
    batch_id:Mapped[int|None]=mapped_column(ForeignKey('paper_batches.id'),nullable=True)
    sequence:Mapped[int]=mapped_column()
    quantity:Mapped[int]=mapped_column()
    state:Mapped[str]=mapped_column(String(24),default='classifying')
    plate_snapshot:Mapped[dict]=mapped_column(JSON)
    batch_snapshot:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    request_id:Mapped[str|None]=mapped_column(String(64),nullable=True)
class PressHold(Base):
    __tablename__='press_holds'
    id:Mapped[int]=mapped_column(primary_key=True)
    job_id:Mapped[int]=mapped_column(ForeignKey('print_jobs.id'))
    sample_id:Mapped[int]=mapped_column(ForeignKey('press_samples.id'),unique=True)
    reason:Mapped[str]=mapped_column(String(300))
    state:Mapped[str]=mapped_column(String(24),default='active')
    created_at:Mapped[str]=mapped_column(String(40))
    reproof_id:Mapped[int|None]=mapped_column(ForeignKey('proof_sheets.id'),nullable=True)
    reproof_ids:Mapped[list]=mapped_column(JSON,default=list)
    resolution:Mapped[str]=mapped_column(String(500),default='')
    closed_by:Mapped[str]=mapped_column(String(100),default='')
    closed_at:Mapped[str|None]=mapped_column(String(40),nullable=True)
