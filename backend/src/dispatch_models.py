from datetime import date
from sqlalchemy import String,ForeignKey,JSON,CheckConstraint,Date,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from .db import Base
class PaperLot(Base):
    __tablename__='paper_lots'
    __table_args__=(CheckConstraint('gsm>0 AND available_sheets>=0'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(50),unique=True)
    gsm:Mapped[int]=mapped_column()
    available_sheets:Mapped[int]=mapped_column()
    state:Mapped[str]=mapped_column(String(24),default='active')
class PaperBatch(Base):
    # 领用批次：同一开机单连续使用同一纸批的区间。换纸即关闭旧批次并新开批次，
    # 关闭后不可恢复；批次号在工单内递增，lot_code/paper_snapshot 为建立时快照，永不回写。
    __tablename__='paper_batches'
    __table_args__=(UniqueConstraint('job_id','batch_no'),CheckConstraint('batch_no>0 AND baseline_printed>=0'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    job_id:Mapped[int]=mapped_column(ForeignKey('print_jobs.id'))
    lot_id:Mapped[int]=mapped_column(ForeignKey('paper_lots.id'))
    batch_no:Mapped[int]=mapped_column()
    state:Mapped[str]=mapped_column(String(24),default='active')
    lot_code:Mapped[str]=mapped_column(String(50))
    paper_snapshot:Mapped[str]=mapped_column(String(80))
    baseline_printed:Mapped[int]=mapped_column(default=0)
    opened_by:Mapped[str]=mapped_column(String(100))
    opened_at:Mapped[str]=mapped_column(String(40))
    closed_at:Mapped[str|None]=mapped_column(String(40),nullable=True)
class PaperIssue(Base):
    __tablename__='paper_issues'
    __table_args__=(CheckConstraint('sheets>0 AND returned_sheets>=0 AND returned_sheets<=sheets'),UniqueConstraint('job_id','request_id'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    job_id:Mapped[int]=mapped_column(ForeignKey('print_jobs.id'))
    lot_id:Mapped[int]=mapped_column(ForeignKey('paper_lots.id'))
    batch_id:Mapped[int|None]=mapped_column(ForeignKey('paper_batches.id'),nullable=True)
    sheets:Mapped[int]=mapped_column()
    returned_sheets:Mapped[int]=mapped_column(default=0)
    paper_snapshot:Mapped[str]=mapped_column(String(80))
    issued_by:Mapped[str]=mapped_column(String(100))
    returned_by:Mapped[str]=mapped_column(String(100),default='')
    return_note:Mapped[str]=mapped_column(String(300),default='')
    request_id:Mapped[str|None]=mapped_column(String(64),nullable=True)
class PressMachine(Base):
    __tablename__='press_machines'
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(40),unique=True)
    state:Mapped[str]=mapped_column(String(24),default='ready')
class PressInspection(Base):
    __tablename__='press_inspections'
    id:Mapped[int]=mapped_column(primary_key=True)
    job_id:Mapped[int]=mapped_column(ForeignKey('print_jobs.id'))
    machine_id:Mapped[int]=mapped_column(ForeignKey('press_machines.id'))
    inspection_date:Mapped[date]=mapped_column(Date)
    lubricated:Mapped[bool]=mapped_column()
    guards_checked:Mapped[bool]=mapped_column()
    state:Mapped[str]=mapped_column(String(24),default='draft')
    approved_by:Mapped[str]=mapped_column(String(100),default='')
class PrintPack(Base):
    __tablename__='print_packs'
    __table_args__=(CheckConstraint('sheets>0'),UniqueConstraint('job_id','request_id'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    job_id:Mapped[int]=mapped_column(ForeignKey('print_jobs.id'))
    batch_id:Mapped[int|None]=mapped_column(ForeignKey('paper_batches.id'),nullable=True)
    code:Mapped[str]=mapped_column(String(50),unique=True)
    sheets:Mapped[int]=mapped_column()
    state:Mapped[str]=mapped_column(String(24),default='open')
    sample_snapshot:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    request_id:Mapped[str|None]=mapped_column(String(64),nullable=True)
class PrintShipment(Base):
    __tablename__='print_shipments'
    __table_args__=(UniqueConstraint('job_id','request_id'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    job_id:Mapped[int]=mapped_column(ForeignKey('print_jobs.id'))
    tracking:Mapped[str]=mapped_column(String(80),unique=True)
    customer:Mapped[str]=mapped_column(String(120))
    state:Mapped[str]=mapped_column(String(24),default='draft')
    snapshot:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    dispatched_by:Mapped[str]=mapped_column(String(100),default='')
    received_by:Mapped[str]=mapped_column(String(100),default='')
    request_id:Mapped[str|None]=mapped_column(String(64),nullable=True)
class ShipmentPack(Base):
    __tablename__='shipment_packs'
    id:Mapped[int]=mapped_column(primary_key=True)
    shipment_id:Mapped[int]=mapped_column(ForeignKey('print_shipments.id'))
    pack_id:Mapped[int]=mapped_column(ForeignKey('print_packs.id'),unique=True)
