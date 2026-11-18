import re
from sqlalchemy import select,func
from fastapi import HTTPException
from .models import PrintJob
from .production_models import PressSample
from .dispatch_models import PaperLot,PaperIssue,PaperBatch,PrintPack
from .services import audit,now

def locked(s,cls,key):
    row=s.scalar(select(cls).where(cls.id==key).with_for_update().execution_options(populate_existing=True))
    if not row:raise HTTPException(404,'记录不存在')
    return row

def net_paper(s,jid):return int(s.scalar(select(func.coalesce(func.sum(PaperIssue.sheets-PaperIssue.returned_sheets),0)).where(PaperIssue.job_id==jid)))
def printed(s,jid):return int(s.scalar(select(func.coalesce(func.max(PressSample.quantity),0)).where(PressSample.job_id==jid)))

def active_batch(s,jid):
    # 在工单锁之后调用；populate_existing 保证读到已提交的最新批次状态，不回写旧快照
    return s.scalar(select(PaperBatch).where(PaperBatch.job_id==jid,PaperBatch.state=='active').with_for_update().execution_options(populate_existing=True))
def batch_net(s,bid):return int(s.scalar(select(func.coalesce(func.sum(PaperIssue.sheets-PaperIssue.returned_sheets),0)).where(PaperIssue.batch_id==bid)))
def batch_printed(s,b):return max(b.baseline_printed or 0,int(s.scalar(select(func.coalesce(func.max(PressSample.quantity),0)).where(PressSample.batch_id==b.id))))
def batch_sealed(s,bid):return int(s.scalar(select(func.coalesce(func.sum(PrintPack.sheets),0)).where(PrintPack.batch_id==bid,PrintPack.state.in_(['sealed','shipped']))))

def issue(s,b,u):
    j=locked(s,PrintJob,b.job_id)
    if b.request_id:
        dup=s.scalar(select(PaperIssue).where(PaperIssue.job_id==j.id,PaperIssue.request_id==b.request_id))
        if dup:return dup
    if j.status not in ['setup','adjusting','production']:raise HTTPException(409,'仅计划、调机或量产工单可领纸')
    lot=locked(s,PaperLot,b.lot_id);match=re.search(r'(\d+)\s*g',j.paper,re.I)
    if not match or int(match.group(1))!=lot.gsm:raise HTTPException(409,'纸张克重须匹配工单paper中的g数')
    if lot.state!='active' or lot.available_sheets<b.sheets:raise HTTPException(409,'纸张隔离或库存不足')
    # 校验全部通过后才切换批次：任一失败回滚，旧批次依据原样保留；关闭的批次永不重开
    batch=active_batch(s,j.id)
    if batch and batch.lot_id!=lot.id:
        batch.state='closed';batch.closed_at=now()
        audit(s,u,f'换纸换批 工单{j.code} 批次#{batch.batch_no} {batch.lot_code} 关闭不可恢复')
        batch=None
    if not batch:
        no=int(s.scalar(select(func.coalesce(func.max(PaperBatch.batch_no),0)).where(PaperBatch.job_id==j.id)))+1
        batch=PaperBatch(job_id=j.id,lot_id=lot.id,batch_no=no,state='active',lot_code=lot.code,paper_snapshot=j.paper,opened_by=u.email,opened_at=now())
        s.add(batch);s.flush();audit(s,u,f'启用领用批次 工单{j.code} 批次#{no} {lot.code}')
    lot.available_sheets-=b.sheets
    row=PaperIssue(job_id=b.job_id,lot_id=b.lot_id,batch_id=batch.id,sheets=b.sheets,paper_snapshot=j.paper,issued_by=u.email,request_id=b.request_id or None)
    s.add(row);audit(s,u,f'上机领纸 {j.code} {b.sheets}张 批次#{batch.batch_no}');s.commit();return row

def return_unused(s,key,b,u):
    ref=s.get(PaperIssue,key)
    if not ref:raise HTTPException(404,'领料不存在')
    j=locked(s,PrintJob,ref.job_id);row=locked(s,PaperIssue,key)
    if row.returned_by:raise HTTPException(409,'该领料已办理一次余纸退回')
    if len(b.note.strip())<5:raise HTTPException(409,'退纸依据至少5字')
    if b.sheets>row.sheets:raise HTTPException(409,'只能退未使用余纸，退回不得超过领用数')
    if row.batch_id:
        batch=locked(s,PaperBatch,row.batch_id)
        if batch_net(s,batch.id)-b.sheets<batch_printed(s,batch):raise HTTPException(409,'只能退未使用余纸，本批次净领纸不得低于本批累计已印张数')
    elif net_paper(s,j.id)-b.sheets<printed(s,j.id):raise HTTPException(409,'只能退未使用余纸，净领纸不得低于累计已印张数')
    lot=locked(s,PaperLot,row.lot_id);lot.available_sheets+=b.sheets;row.returned_sheets=b.sheets;row.returned_by=u.email;row.return_note=b.note.strip();audit(s,u,f'余纸退回 {j.code} {b.sheets}张');s.commit();return row

def sample_guard(s,batch,quantity):
    # 批内累计口径：换批后新抽检重新累计，不与旧批读数比较；旧批历史读数不回写
    last=batch_printed(s,batch)
    if quantity<last:raise HTTPException(409,'已印数量为当前领用批次累计读数，不得低于本批历史累计')
    if quantity>batch_net(s,batch.id):raise HTTPException(409,'累计已印张数超过本批次净领纸，请先补领纸')
