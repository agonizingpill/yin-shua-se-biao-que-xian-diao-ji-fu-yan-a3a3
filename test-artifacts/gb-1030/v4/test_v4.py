"""V4 领用批次端到端验证：真实 FastAPI 应用 + SQLite，stub redis 与 worker 分类结果。
覆盖：换批原子切换、批内重新累计、旧箱证据保持、跨批拒混装、失败保留旧批次、
幂等重试不重复扣库、退纸批内守卫、批次台账可追溯。"""
import os,sys,json,uuid
os.environ['DATABASE_URL']='sqlite:////tmp/gb-v4-check/v4.db'
os.environ['REDIS_URL']='redis://127.0.0.1:9/0'
os.environ['JWT_SECRET']='v4-test-secret'
os.environ['SEED_DEMO']='true'
sys.path.insert(0,'/workspace/backend')
from fastapi.testclient import TestClient
import src.main as main
import src.production_routes as pr
from src.db import Session
from src.models import ProofSheet
from src.services import now
from src.sampling_service import finish

class FakeRedis:
    def __init__(self):self.items=[]
    def rpush(self,k,v):self.items.append((k,v));return len(self.items)
    def ping(self):return True
main.r=FakeRedis();pr.queue=FakeRedis()

def work_off(pid,defect='合格'):
    with Session() as s:
        p=s.get(ProofSheet,pid)
        assert p and p.classification_state=='classifying',f'proof {pid} not classifying'
        p.defect=defect;p.pass_flag=defect=='合格';p.classification_state='classified';p.classified_at=now();p.evidence={'stub':'v4-test'}
        finish(s,p);s.commit()

FAILED=[]
def check(name,cond,info=''):
    print(('PASS  ' if cond else 'FAIL  ')+name+('' if cond else '  -> '+str(info)))
    if not cond:FAILED.append(name)

with TestClient(main.app) as client:
    def req(method,path,token=None,body=None):
        r=client.request(method,path,json=body,headers={'Authorization':'Bearer '+token} if token else {})
        return r
    def ok(method,path,token=None,body=None,code=200):
        r=req(method,path,token,body)
        assert r.status_code==code,f'{method} {path} -> {r.status_code}: {r.text}'
        return r.json() if r.text else {}
    def blocked(method,path,token=None,body=None):
        r=req(method,path,token,body)
        assert r.status_code==409,f'{method} {path} expected 409 got {r.status_code}: {r.text}'
        return r.json()['detail']

    tp=ok('POST','/auth/login',body={'email':'pressman@qianye.demo','password':'Press2026!'})['token']
    ta=ok('POST','/auth/login',body={'email':'admin@qianye.demo','password':'Press2026!'})['token']
    tv=ok('POST','/auth/login',body={'email':'viewer@qianye.demo','password':'Press2026!'})['token']

    # ---- 既有开机流程走到 production ----
    proofs=ok('GET','/proofs',tp)
    first=next(p for p in proofs if p['code']=='PS-FIRST');recheck=next(p for p in proofs if p['code']=='PS-RECHECK')
    ok('POST',f"/proofs/{first['id']}/classify",tp);work_off(first['id'],'乳化')
    adj=ok('POST','/adjustments',tp,{'proof_id':first['id']})
    plate=next(p for p in ok('GET','/plates',tp) if p['job_id']==1)
    ok('POST',f"/plates/{plate['id']}/measure",tp,{'registration_error_mm':0.08})
    ok('POST',f"/plates/{plate['id']}/approve",ta)
    machine=ok('GET','/press-machines',tp)[0]
    import datetime
    insp=ok('POST','/press-inspections',tp,{'job_id':1,'machine_id':machine['id'],'inspection_date':datetime.date.today().isoformat(),'lubricated':True,'guards_checked':True})
    ok('POST',f"/press-inspections/{insp['id']}/approve",ta)
    ok('POST',f"/adjustments/{adj['id']}/execute",tp)
    ok('POST',f"/proofs/{recheck['id']}/classify",tp);work_off(recheck['id'],'合格')
    ok('POST',f"/adjustments/{adj['id']}/sign",ta,{'reproof_id':recheck['id']})
    check('开机流程进入production',ok('GET','/jobs',tp)[0]['status']=='production')

    # ---- 无领用批次时抽检/装箱被拒 ----
    d=blocked('POST','/press-samples',tp,{'job_id':1,'code':'PS-RUN-0','sequence':1,'quantity':100,'density_c':1.4,'density_m':1.4,'density_y':1.4,'density_k':1.4,'dot_gain':14})
    check('未领纸无批次抽检被拒',d=='请先领纸建立领用批次，抽检须携带领用批次',d)
    d=blocked('POST','/print-packs',tp,{'job_id':1,'code':'BOX-0','sheets':100})
    check('未领纸无批次装箱被拒',d=='请先领纸建立领用批次，装箱须携带领用批次',d)

    # ---- R1/R6 领纸建立批次 + 幂等重放 ----
    lotA=next(l for l in ok('GET','/paper-lots',tp) if l['code']=='PAPER-157-A')
    i1=ok('POST','/paper-issues',tp,{'job_id':1,'lot_id':lotA['id'],'sheets':3000,'request_id':'req-issue-1'})
    check('领纸建立领用批次#1',i1['batch_id']==1)
    b=ok('GET','/paper-batches',tv)
    check('批次台账:批次#1 active 净3000',len(b)==1 and b[0]['batch_no']==1 and b[0]['state']=='active' and b[0]['net_sheets']==3000,b)
    i1b=ok('POST','/paper-issues',tp,{'job_id':1,'lot_id':lotA['id'],'sheets':3000,'request_id':'req-issue-1'})
    check('重试同request_id返回原领料不重复扣库',i1b['id']==i1['id'])
    lotA2=next(l for l in ok('GET','/paper-lots',tp) if l['code']=='PAPER-157-A')
    check('库存只扣一次(20000-3000)',lotA2['available_sheets']==17000,lotA2['available_sheets'])
    check('领料账只有一笔',len([i for i in ok('GET','/paper-issues',tp) if i['job_id']==1])==1)

    # ---- R1 抽检携带批次与批内累计 ----
    s1=ok('POST','/press-samples',tp,{'job_id':1,'code':'PS-RUN-1','sequence':1,'quantity':2000,'density_c':1.4,'density_m':1.4,'density_y':1.4,'density_k':1.4,'dot_gain':14,'request_id':'req-sample-1'})
    check('抽检携带领用批次快照',s1['batch_id']==1 and s1['batch_snapshot']['batch_no']==1 and s1['batch_snapshot']['lot_code']=='PAPER-157-A',s1.get('batch_snapshot'))
    s1b=ok('POST','/press-samples',tp,{'job_id':1,'code':'PS-RUN-1','sequence':1,'quantity':2000,'density_c':1.4,'density_m':1.4,'density_y':1.4,'density_k':1.4,'dot_gain':14,'request_id':'req-sample-1'})
    check('抽检重试幂等返回原样',s1b['id']==s1['id'])
    work_off(s1['proof_id'],'合格')
    check('抽检判读合格',next(x for x in ok('GET','/press-samples',tp) if x['id']==s1['id'])['state']=='passed')

    # ---- R1 封箱携带批次放行依据 ----
    p1=ok('POST','/print-packs',tp,{'job_id':1,'code':'BOX-1','sheets':1000,'request_id':'req-pack-1'})
    check('箱携带领用批次',p1['batch_id']==1)
    p1=ok('POST',f"/print-packs/{p1['id']}/seal",tp)
    snap=p1['sample_snapshot']
    check('封箱快照含批次/合格累计/批净纸',snap['batch_no']==1 and snap['lot_code']=='PAPER-157-A' and snap['quantity']==2000 and snap['net_paper']==3000,snap)

    # ---- R2/R5 换纸换批:原子切换 ----
    lotB=ok('POST','/paper-lots',tp,{'code':'PAPER-157-B','gsm':157,'available_sheets':5000})
    i2=ok('POST','/paper-issues',tp,{'job_id':1,'lot_id':lotB['id'],'sheets':2000,'request_id':'req-issue-2'})
    bs={x['batch_no']:x for x in ok('GET','/paper-batches',tv)}
    check('换纸关闭批次#1开启批次#2',i2['batch_id']==2 and bs[1]['state']=='closed' and bs[1]['closed_at'] and bs[2]['state']=='active',bs)
    check('旧批次依据原样保留',bs[1]['lot_code']=='PAPER-157-A' and bs[1]['net_sheets']==3000 and bs[1]['printed_sheets']==2000)

    # ---- R5 换批失败保留旧批次 ----
    lotC=ok('POST','/paper-lots',tp,{'code':'PAPER-157-C','gsm':157,'available_sheets':100})
    ok('PUT',f"/paper-lots/{lotC['id']}/state",ta,{'state':'quarantine'})
    d=blocked('POST','/paper-issues',tp,{'job_id':1,'lot_id':lotC['id'],'sheets':50})
    check('隔离纸批换批被拒',d=='纸张隔离或库存不足',d)
    d=blocked('POST','/paper-issues',tp,{'job_id':1,'lot_id':lotB['id'],'sheets':99999})
    check('库存不足换批被拒',d=='纸张隔离或库存不足',d)
    bs={x['batch_no']:x for x in ok('GET','/paper-batches',tv)}
    check('失败换批后旧批次仍是当前批次',len(bs)==2 and bs[2]['state']=='active' and bs[2]['net_sheets']==2000,bs)
    check('失败换批未扣库存',next(l for l in ok('GET','/paper-lots',tp) if l['code']=='PAPER-157-B')['available_sheets']==3000)

    # ---- R2 换批后新抽检重新累计 ----
    s2=ok('POST','/press-samples',tp,{'job_id':1,'code':'PS-RUN-2','sequence':2,'quantity':400,'density_c':1.4,'density_m':1.4,'density_y':1.4,'density_k':1.4,'dot_gain':14})
    check('换批后累计重新起算(400<旧批2000)',s2['batch_id']==2 and s2['quantity']==400)
    work_off(s2['proof_id'],'合格')
    d=blocked('POST','/press-samples',tp,{'job_id':1,'code':'PS-RUN-3','sequence':3,'quantity':350,'density_c':1.4,'density_m':1.4,'density_y':1.4,'density_k':1.4,'dot_gain':14})
    check('批内读数不得回退',d=='已印数量为当前领用批次累计读数，不得低于本批历史累计',d)
    d=blocked('POST','/press-samples',tp,{'job_id':1,'code':'PS-RUN-3','sequence':3,'quantity':5000,'density_c':1.4,'density_m':1.4,'density_y':1.4,'density_k':1.4,'dot_gain':14})
    check('批内读数不得超本批净领纸',d=='累计已印张数超过本批次净领纸，请先补领纸',d)
    s3=ok('POST','/press-samples',tp,{'job_id':1,'code':'PS-RUN-3','sequence':3,'quantity':800,'density_c':1.4,'density_m':1.4,'density_y':1.4,'density_k':1.4,'dot_gain':14})
    work_off(s3['proof_id'],'合格')

    # ---- R3 旧箱证据保持原样 ----
    p1now=next(x for x in ok('GET','/print-packs',tv) if x['code']=='BOX-1')
    check('换批后旧箱封签快照原样',p1now['sample_snapshot']['batch_no']==1 and p1now['sample_snapshot']['quantity']==2000 and p1now['sample_snapshot']['net_paper']==3000,p1now['sample_snapshot'])

    # ---- R3 旧批余纸可装箱,额度按旧批核算不被新纸覆盖 ----
    p1b=ok('POST','/print-packs',tp,{'job_id':1,'code':'BOX-1B','sheets':800,'batch_id':1})
    p1b=ok('POST',f"/print-packs/{p1b['id']}/seal",tp)
    check('旧批余纸箱封于旧批依据',p1b['sample_snapshot']['batch_no']==1 and p1b['sample_snapshot']['net_paper']==3000)
    p1c=ok('POST','/print-packs',tp,{'job_id':1,'code':'BOX-1C','sheets':300,'batch_id':1})
    d=blocked('POST',f"/print-packs/{p1c['id']}/seal",tp)
    check('旧批封箱累计不得超旧批合格累计(1000+800+300>2000)',d=='封箱累计张数超本批最后合格抽检累计或本批净领纸',d)

    # ---- R4 跨批次箱单拒绝混装 ----
    p2=ok('POST','/print-packs',tp,{'job_id':1,'code':'BOX-2','sheets':400})
    check('默认装箱到当前批次#2',p2['batch_id']==2)
    p2=ok('POST',f"/print-packs/{p2['id']}/seal",tp)
    check('新批箱封于新批依据',p2['sample_snapshot']['batch_no']==2 and p2['sample_snapshot']['quantity']==800 and p2['sample_snapshot']['net_paper']==2000)
    d=blocked('POST','/print-shipments',tp,{'job_id':1,'tracking':'SHIP-MIX','customer':'某客户','pack_ids':[p1['id'],p2['id']]})
    check('跨批次箱单拒绝混装',d=='跨批次箱单拒绝混装：发货箱须来自同一领用批次',d)

    # ---- R1 同批发货携带领用批次 ----
    sh=ok('POST','/print-shipments',tp,{'job_id':1,'tracking':'SHIP-A','customer':'禾页文化','pack_ids':[p1['id'],p1b['id']],'request_id':'req-ship-1'})
    sh2=ok('POST','/print-shipments',tp,{'job_id':1,'tracking':'SHIP-A','customer':'禾页文化','pack_ids':[p1['id'],p1b['id']],'request_id':'req-ship-1'})
    check('发货单重试幂等',sh2['id']==sh['id'])
    sh=ok('POST',f"/print-shipments/{sh['id']}/dispatch",ta)
    check('发货快照携带领用批次',sh['snapshot']['batch']['batch_no']==1 and sh['snapshot']['batch']['lot_code']=='PAPER-157-A',sh['snapshot']['batch'])
    check('发货箱单逐箱带批次号',all(b['batch_no']==1 for b in sh['snapshot']['boxes']))
    check('发货质量依据为本批合格累计',sh['snapshot']['quality']['proof_code']=='PS-RUN-1' and sh['snapshot']['quality']['quantity']==2000)
    d=blocked('POST',f"/print-shipments/{sh['id']}/dispatch",ta)
    check('重复装运拒绝不回写快照',d=='发货已出库，不得重复装运',d)

    # ---- 退纸批内守卫:旧箱额度不被新纸覆盖 ----
    d=blocked('POST',f"/paper-issues/{i1['id']}/return",tp,{'sheets':1500,'note':'退纸超出本批已印量测试'})
    check('退纸不得使本批净领纸低于本批已印(3000-1500<2000)',d=='只能退未使用余纸，本批次净领纸不得低于本批累计已印张数',d)
    r1=ok('POST',f"/paper-issues/{i1['id']}/return",tp,{'sheets':1000,'note':'核对后退回未用余纸'})
    check('批内合规退纸(净2000=已印2000)',r1['returned_sheets']==1000)
    d=blocked('POST',f"/paper-issues/{i1['id']}/return",tp,{'sheets':100,'note':'重复退纸应被拒绝'})
    check('重复退纸拒绝不重复还库',d=='该领料已办理一次余纸退回',d)
    d=blocked('POST',f"/paper-issues/{i2['id']}/return",tp,{'sheets':1700,'note':'退纸超出本批已印量测试'})
    check('新批退纸同样按本批已印守卫(2000-1700<800)',d=='只能退未使用余纸，本批次净领纸不得低于本批累计已印张数',d)
    r2=ok('POST',f"/paper-issues/{i2['id']}/return",tp,{'sheets':1200,'note':'退回本批未用余纸'})
    check('新批合规退纸(净800=已印800)',r2['returned_sheets']==1200)

    # ---- 补领并入当前批次;再换纸覆盖后无法恢复 ----
    i3=ok('POST','/paper-issues',tp,{'job_id':1,'lot_id':lotB['id'],'sheets':500,'request_id':'req-issue-3'})
    bs={x['batch_no']:x for x in ok('GET','/paper-batches',tv)}
    check('同纸批补领并入当前批次不新开',i3['batch_id']==2 and len(bs)==2 and bs[2]['net_sheets']==1300,bs)
    i4=ok('POST','/paper-issues',tp,{'job_id':1,'lot_id':lotA['id'],'sheets':100,'request_id':'req-issue-4'})
    bs={x['batch_no']:x for x in ok('GET','/paper-batches',tv)}
    check('再换纸开新批次#3,旧批#1/#2关闭不可恢复',i4['batch_id']==3 and bs[1]['state']=='closed' and bs[2]['state']=='closed' and bs[3]['state']=='active',bs)
    check('批次#1证据始终原样',bs[1]['lot_code']=='PAPER-157-A' and bs[1]['printed_sheets']==2000 and bs[1]['sealed_sheets']==1800)
    s4=ok('POST','/press-samples',tp,{'job_id':1,'code':'PS-RUN-4','sequence':4,'quantity':50,'density_c':1.4,'density_m':1.4,'density_y':1.4,'density_k':1.4,'dot_gain':14})
    check('批次#3再次重新累计(50<前批800)',s4['batch_id']==3)
    work_off(s4['proof_id'],'合格')

    # ---- 重试/并发安全:状态守卫 ----
    d=blocked('POST',f"/print-packs/{p2['id']}/seal",tp)
    check('重复封箱拒绝',d=='仅未封箱可封签或取消；封签后数量冻结',d)
    lotA3=next(l for l in ok('GET','/paper-lots',tp) if l['code']=='PAPER-157-A')
    check('全部领退后库存账一致',lotA3['available_sheets']==20000-3000+1000-100,lotA3['available_sheets'])

    # ---- R7 页面追溯数据齐备 ----
    batches=ok('GET','/paper-batches',tv)
    b1=next(x for x in batches if x['batch_no']==1)
    check('批次台账可追溯净领/已印/已封',b1['net_sheets']==2000 and b1['printed_sheets']==2000 and b1['sealed_sheets']==1800,b1)
    ships=ok('GET','/print-shipments',tv)
    check('发货单可追溯批次与放行依据',ships[0]['snapshot']['batch']['batch_no']==1 and ships[0]['snapshot']['quality']['proof_code']=='PS-RUN-1')
    samples=ok('GET','/press-samples',tv)
    check('抽检台账逐张带批次快照',all(x['batch_snapshot'] and x['batch_snapshot']['lot_code'] for x in samples))

print()
if FAILED:print(f'{len(FAILED)} 项失败:',*FAILED,sep='\n  ');sys.exit(1)
print('全部 V4 端到端检查通过')
