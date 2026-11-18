"""迁移验证：老库（无批次列/有历史领纸与抽检）经 migrate() 后——
1) 历史领纸按连续同纸批分段回填为领用批次，最后一段 active 并继承历史累计基线；
2) 历史样张/箱保持原样（batch_id 留空）；3) 迁移幂等可重复执行；
4) 回填批次参与批内退纸/抽检守卫；5) 再换纸新开批次不重开旧批。"""
import os,sys
os.environ['DATABASE_URL']='sqlite:////tmp/gb-v4-check/v4-legacy.db'
os.environ['REDIS_URL']='redis://127.0.0.1:9/0'
os.environ['JWT_SECRET']='v4-test-secret'
os.environ['SEED_DEMO']='false'
sys.path.insert(0,'/workspace/backend')
from sqlalchemy import select
import src.db as db
from src.models import PrintJob,ProofSheet
from src.dispatch_models import PaperLot,PaperIssue,PaperBatch
from src.production_models import PlateSet,PressSample
from src.paper_service import return_unused,sample_guard,issue,active_batch
from src.dispatch_schemas import PaperReturn,PaperIssueInput
from fastapi import HTTPException

FAILED=[]
def check(name,cond,info=''):
    print(('PASS  ' if cond else 'FAIL  ')+name+('' if cond else '  -> '+str(info)))
    if not cond:FAILED.append(name)
class U:email='pressman@qianye.demo'

db.Base.metadata.create_all(db.engine)
with db.Session() as s:
    j=PrintJob(code='PJ-LEG',title='旧工单',paper='157g 哑粉',target_density=1.4,status='production');s.add(j);s.flush()
    lotA=PaperLot(code='LEG-A',gsm=157,available_sheets=900);lotB=PaperLot(code='LEG-B',gsm=157,available_sheets=100);s.add_all([lotA,lotB]);s.flush()
    i1=PaperIssue(job_id=j.id,lot_id=lotA.id,sheets=2000,paper_snapshot=j.paper,issued_by='old')
    i2=PaperIssue(job_id=j.id,lot_id=lotA.id,sheets=1000,paper_snapshot=j.paper,issued_by='old')
    i3=PaperIssue(job_id=j.id,lot_id=lotB.id,sheets=5000,paper_snapshot=j.paper,issued_by='old')
    s.add_all([i1,i2,i3]);s.flush()
    plate=PlateSet(job_id=j.id,version='V1',source_note='历史印版来源说明',state='ready',registration_error_mm=0.1,approved_by='old');s.add(plate);s.flush()
    proof=ProofSheet(job_id=j.id,code='LEG-PROOF',density_c=1.4,density_m=1.4,density_y=1.4,density_k=1.4,dot_gain=14,classification_state='classified',pass_flag=True,defect='合格');s.add(proof);s.flush()
    s.add(PressSample(job_id=j.id,proof_id=proof.id,plate_id=plate.id,sequence=1,quantity=1500,state='passed',plate_snapshot={}))
    s.commit();ids=(j.id,lotA.id,lotB.id,i1.id,i2.id,i3.id)
jid,lotA_id,lotB_id,i1_id,i2_id,i3_id=ids

db.migrate()
with db.Session() as s:
    batches=s.scalars(select(PaperBatch).where(PaperBatch.job_id==jid).order_by(PaperBatch.batch_no)).all()
    check('回填生成两个批次',len(batches)==2,[ (b.batch_no,b.state) for b in batches])
    b1,b2=batches
    check('早段批次#1关闭且不可恢复',b1.state=='closed' and b1.closed_at and b1.lot_code=='LEG-A')
    check('末段批次#2为当前批次',b2.state=='active' and b2.lot_code=='LEG-B')
    check('当前批次继承历史累计基线1500',b2.baseline_printed==1500,b2.baseline_printed)
    check('关闭批次基线为0',b1.baseline_printed==0)
    got={i.id:i.batch_id for i in s.scalars(select(PaperIssue)).all()}
    check('历史领纸全部挂入批次',got[i1_id]==b1.id and got[i2_id]==b1.id and got[i3_id]==b2.id,got)
    legacy=s.scalars(select(PressSample)).one()
    check('历史样张保持原样不回写批次',legacy.batch_id is None and legacy.batch_snapshot is None)
    # 批内守卫使用基线:批次#2 净5000 已印基线1500
    try:sample_guard(s,b2,1400);check('批内读数低于基线被拒',False)
    except HTTPException as e:check('批内读数低于基线被拒',e.status_code==409)
    sample_guard(s,b2,1600);check('批内读数1600通过(>=基线1500,<=净5000)',True)
    try:return_unused(s,i3_id,PaperReturn(sheets=3600,note='退纸测试超过已印基线'),U());check('退纸低于基线被拒',False)
    except HTTPException as e:check('退纸低于基线被拒',e.status_code==409 and '本批' in e.detail,e.detail)
    r=return_unused(s,i3_id,PaperReturn(sheets=3500,note='退回未用余纸保留已印'),U())
    check('合规退纸(净1500=基线1500)',r.returned_sheets==3500)
    # 迁移后再换纸:开批次#3,旧批#1/#2保持关闭
    row=issue(s,PaperIssueInput(job_id=jid,lot_id=lotA_id,sheets=100),U())
    b1x=s.get(PaperBatch,b1.id);b2x=s.get(PaperBatch,b2.id);b3=s.get(PaperBatch,row.batch_id)
    check('迁移后再换纸开新批次#3',b3.batch_no==3 and b3.state=='active')
    check('迁移批次#1/#2关闭不可恢复',b1x.state=='closed' and b2x.state=='closed')

db.migrate()
with db.Session() as s:
    n=len(s.scalars(select(PaperBatch).where(PaperBatch.job_id==jid)).all())
    check('迁移幂等可重复执行',n==3,n)

print()
if FAILED:print(f'{len(FAILED)} 项失败:',*FAILED,sep='\n  ');sys.exit(1)
print('迁移回填检查全部通过')
