import os
import logging
from sqlalchemy import create_engine,select,func,text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
_kwargs={'pool_pre_ping':True}
if os.environ.get('DATABASE_URL','').startswith('mysql'):_kwargs['isolation_level']='READ COMMITTED'
engine = create_engine(os.environ['DATABASE_URL'], **_kwargs)
Session = sessionmaker(engine, expire_on_commit=False)
class Base(DeclarativeBase): pass
def db_session():
    with Session() as s:
        yield s

# V4 领用批次：老库补列（create_all 不改已有表），再把历史领纸回填为批次。
# 新列全部可空，历史样张/箱/发货保持原样不回写。
NEW_COLUMNS={
    'paper_issues':{'batch_id':'INT NULL','request_id':'VARCHAR(64) NULL'},
    'press_samples':{'batch_id':'INT NULL','batch_snapshot':'JSON NULL','request_id':'VARCHAR(64) NULL'},
    'print_packs':{'batch_id':'INT NULL','request_id':'VARCHAR(64) NULL'},
    'print_shipments':{'request_id':'VARCHAR(64) NULL'},
}
REQUEST_INDEXES={'paper_issues':'uq_issue_job_req','press_samples':'uq_sample_job_req','print_packs':'uq_pack_job_req','print_shipments':'uq_ship_job_req'}
def _migrate_columns():
    if engine.dialect.name!='mysql':return
    with engine.begin() as c:
        for table,cols in NEW_COLUMNS.items():
            have={r[0] for r in c.execute(text('SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:t'),{'t':table})}
            for name,ddl in cols.items():
                if name not in have:c.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
        for table,index in REQUEST_INDEXES.items():
            have={r[0] for r in c.execute(text('SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:t'),{'t':table})}
            if index not in have:c.execute(text(f'ALTER TABLE {table} ADD UNIQUE KEY {index} (job_id, request_id)'))
def _backfill_batches():
    # 历史领纸按“同工单连续同纸批”分段回填为领用批次：最后一段为当前批次，
    # 继承换批前历史最高累计读数作为基线；更早段落关闭且不可恢复。
    from .models import PrintJob
    from .production_models import PressSample
    from .dispatch_models import PaperLot,PaperIssue,PaperBatch
    from .services import now
    with Session() as s:
        job_ids=s.scalars(select(PaperIssue.job_id).where(PaperIssue.batch_id.is_(None)).group_by(PaperIssue.job_id)).all()
        for jid in job_ids:
            issues=s.scalars(select(PaperIssue).where(PaperIssue.job_id==jid).order_by(PaperIssue.id)).all()
            runs=[]
            for issue in issues:
                if runs and runs[-1][0]==issue.lot_id:runs[-1][1].append(issue)
                else:runs.append((issue.lot_id,[issue]))
            no=int(s.scalar(select(func.coalesce(func.max(PaperBatch.batch_no),0)).where(PaperBatch.job_id==jid)))
            has_active=bool(s.scalar(select(PaperBatch.id).where(PaperBatch.job_id==jid,PaperBatch.state=='active')))
            legacy=int(s.scalar(select(func.coalesce(func.max(PressSample.quantity),0)).where(PressSample.job_id==jid,PressSample.batch_id.is_(None))))
            job=s.get(PrintJob,jid);stamp=now()
            for lot_id,group in runs:
                no+=1;active=group is runs[-1][1] and not has_active
                lot=s.get(PaperLot,lot_id)
                batch=PaperBatch(job_id=jid,lot_id=lot_id,batch_no=no,state='active' if active else 'closed',lot_code=lot.code if lot else str(lot_id),paper_snapshot=job.paper if job else '',baseline_printed=legacy if active else 0,opened_by='数据迁移',opened_at=stamp,closed_at=None if active else stamp)
                s.add(batch);s.flush()
                for issue in group:issue.batch_id=batch.id
        s.commit()
def migrate():
    try:
        _migrate_columns();_backfill_batches()
    except Exception:logging.exception('paper batch migration failed')
