from sqlalchemy import select,func
from fastapi import HTTPException
from .models import PrintJob,ProofSheet
from .production_models import PressSample,PressHold
from .dispatch_models import PrintPack,PrintShipment,ShipmentPack,PaperBatch
from .paper_service import locked,net_paper,active_batch,batch_net,batch_sealed
from .services import audit

def job_gate(s,j):
    if j.status!='production' or s.scalar(select(PressHold.id).where(PressHold.job_id==j.id,PressHold.state=='active')):raise HTTPException(409,'须无活动停机单的量产工单')
    if s.scalar(select(PressSample.id).where(PressSample.job_id==j.id,PressSample.state=='classifying')):raise HTTPException(409,'抽检待判读，不能封箱或发货')

def gate(s,j):
    # 换批前旧箱（无领用批次）沿用工单级口径，历史证据保持原样
    job_gate(s,j)
    sample=s.scalar(select(PressSample).where(PressSample.job_id==j.id,PressSample.state=='passed').order_by(PressSample.sequence.desc()))
    if not sample:raise HTTPException(409,'须有合格量产抽检才可装箱发货')
    proof=s.get(ProofSheet,sample.proof_id)
    return {'sample_id':sample.id,'sequence':sample.sequence,'quantity':sample.quantity,'proof_code':proof.code,'plate':sample.plate_snapshot,'net_paper':net_paper(s,j.id)}

def batch_gate(s,j,batch):
    # 批次级放行依据：本批最后一张合格抽检 + 本批净领纸，随封箱/装运快照冻结
    job_gate(s,j)
    sample=s.scalar(select(PressSample).where(PressSample.batch_id==batch.id,PressSample.state=='passed').order_by(PressSample.sequence.desc()))
    if not sample:raise HTTPException(409,'本领用批次须有合格量产抽检才可装箱发货')
    proof=s.get(ProofSheet,sample.proof_id)
    return {'batch_id':batch.id,'batch_no':batch.batch_no,'lot_code':batch.lot_code,'paper':batch.paper_snapshot,'sample_id':sample.id,'sequence':sample.sequence,'quantity':sample.quantity,'proof_code':proof.code,'plate':sample.plate_snapshot,'net_paper':batch_net(s,batch.id)}

def sealed(s,jid):return int(s.scalar(select(func.coalesce(func.sum(PrintPack.sheets),0)).where(PrintPack.job_id==jid,PrintPack.state.in_(['sealed','shipped']))))

def create_pack(s,b,u):
    j=locked(s,PrintJob,b.job_id)
    if b.request_id:
        dup=s.scalar(select(PrintPack).where(PrintPack.job_id==j.id,PrintPack.request_id==b.request_id))
        if dup:return dup
    if b.batch_id:
        batch=s.scalar(select(PaperBatch).where(PaperBatch.id==b.batch_id).with_for_update().execution_options(populate_existing=True))
        if not batch or batch.job_id!=j.id:raise HTTPException(409,'指定领用批次不属于本工单')
    else:
        batch=active_batch(s,j.id)
        if not batch:raise HTTPException(409,'请先领纸建立领用批次，装箱须携带领用批次')
    e=batch_gate(s,j,batch)
    if b.sheets>min(e['quantity'],e['net_paper']):raise HTTPException(409,'单箱张数超本批最后合格抽检累计或本批净领纸')
    row=PrintPack(job_id=b.job_id,code=b.code,sheets=b.sheets,batch_id=batch.id,request_id=b.request_id or None);s.add(row);audit(s,u,f'登记待封箱 {b.code} {b.sheets}张 批次#{batch.batch_no}');s.commit();return row

def pack_action(s,key,u,action):
    ref=s.get(PrintPack,key)
    if not ref:raise HTTPException(404,'箱码不存在')
    j=locked(s,PrintJob,ref.job_id);row=locked(s,PrintPack,key)
    if row.state!='open':raise HTTPException(409,'仅未封箱可封签或取消；封签后数量冻结')
    if action=='cancel':row.state='cancelled'
    else:
        if row.batch_id:
            batch=locked(s,PaperBatch,row.batch_id);e=batch_gate(s,j,batch)
            if batch_sealed(s,batch.id)+row.sheets>min(e['quantity'],e['net_paper']):raise HTTPException(409,'封箱累计张数超本批最后合格抽检累计或本批净领纸')
        else:
            e=gate(s,j)
            if sealed(s,j.id)+row.sheets>min(e['quantity'],e['net_paper']):raise HTTPException(409,'封箱累计张数超最后合格抽检或净领纸')
        row.state='sealed';row.sample_snapshot=e
    audit(s,u,f'成品箱{key} {action}');s.commit();return row

def create_shipment(s,b,u):
    j=locked(s,PrintJob,b.job_id)
    if b.request_id:
        dup=s.scalar(select(PrintShipment).where(PrintShipment.job_id==j.id,PrintShipment.request_id==b.request_id))
        if dup:return dup
    job_gate(s,j)
    if len(set(b.pack_ids))!=len(b.pack_ids):raise HTTPException(409,'箱单包含重复箱码')
    boxes=[locked(s,PrintPack,k) for k in sorted(b.pack_ids)]
    for box in boxes:
        if box.job_id!=j.id or box.state!='sealed' or s.scalar(select(ShipmentPack.id).where(ShipmentPack.pack_id==box.id)):raise HTTPException(409,'箱码须同工单、已封且未被其他发货单占用')
    if len({box.batch_id for box in boxes})>1:raise HTTPException(409,'跨批次箱单拒绝混装：发货箱须来自同一领用批次')
    row=PrintShipment(job_id=j.id,tracking=b.tracking,customer=b.customer,request_id=b.request_id or None);s.add(row);s.flush()
    for box in boxes:s.add(ShipmentPack(shipment_id=row.id,pack_id=box.id))
    audit(s,u,f'编制发货单{b.tracking} {len(boxes)}箱');s.commit();return row

def dispatch(s,key,u):
    ref=s.get(PrintShipment,key)
    if not ref:raise HTTPException(404,'发货单不存在')
    j=locked(s,PrintJob,ref.job_id);row=locked(s,PrintShipment,key)
    if row.state!='draft':raise HTTPException(409,'发货已出库，不得重复装运')
    job_gate(s,j)
    ids=s.scalars(select(ShipmentPack.pack_id).where(ShipmentPack.shipment_id==row.id)).all();boxes=[locked(s,PrintPack,k) for k in sorted(ids)]
    if not boxes or any(x.job_id!=j.id or x.state!='sealed' for x in boxes):raise HTTPException(409,'箱单不完整或未封箱')
    batches={x.batch_id for x in boxes}
    if len(batches)>1:raise HTTPException(409,'跨批次箱单拒绝混装：发货箱须来自同一领用批次')
    bid=batches.pop()
    if bid:
        batch=locked(s,PaperBatch,bid);e=batch_gate(s,j,batch)
        if batch_sealed(s,batch.id)>min(e['quantity'],e['net_paper']):raise HTTPException(409,'本批已封总量超过合格数量/净领纸，禁止发货')
        batch_info={'batch_id':batch.id,'batch_no':batch.batch_no,'lot_code':batch.lot_code,'paper':batch.paper_snapshot}
    else:
        e=gate(s,j)
        if sealed(s,j.id)>min(e['quantity'],e['net_paper']):raise HTTPException(409,'已封总量超过合格数量/净领纸，禁止发货')
        batch_info=None
    row.snapshot={'job_code':j.code,'paper':j.paper,'customer':row.customer,'tracking':row.tracking,'batch':batch_info,'quality':e,'boxes':[{'id':x.id,'code':x.code,'sheets':x.sheets,'batch_no':x.sample_snapshot.get('batch_no') if x.sample_snapshot else None,'sample_snapshot':x.sample_snapshot} for x in boxes],'total_sheets':sum(x.sheets for x in boxes)}
    for box in boxes:box.state='shipped'
    row.state='dispatched';row.dispatched_by=u.email;audit(s,u,f'主管装运{row.tracking} 冻结{len(boxes)}箱');s.commit();return row

def receive(s,key,b,u):
    ref=s.get(PrintShipment,key)
    if not ref:raise HTTPException(404,'发货单不存在')
    locked(s,PrintJob,ref.job_id);row=locked(s,PrintShipment,key)
    if row.state!='dispatched':raise HTTPException(409,'须主管装运后客户签收，不得重复')
    if len(b.received_by.strip())<2:raise HTTPException(409,'签收人不能为空')
    row.state='received';row.received_by=b.received_by.strip();audit(s,u,f'客户签收{row.tracking} {row.received_by}');s.commit();return row
