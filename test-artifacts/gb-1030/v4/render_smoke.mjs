// 前端渲染冒烟：三页在含领用批次的状态下渲染出批次与放行依据，且不抛异常
import * as paper from '/workspace/frontend/src/pages/paper.js';
import * as samples from '/workspace/frontend/src/pages/samples.js';
import * as shipments from '/workspace/frontend/src/pages/shipments.js';
const s={user:{role:'pressman',email:'p@qianye.demo'},
 jobs:[{id:1,code:'PJ-26091',title:'画册封套',paper:'157g 哑粉',status:'production'}],
 proofs:[{id:9,code:'PS-RUN-1',defect:'合格',classification_state:'classified',density_c:1.4,density_m:1.4,density_y:1.4,density_k:1.4,dot_gain:14}],
 adjustments:[],plates:[],holds:[],
 samples:[{id:1,job_id:1,proof_id:9,sequence:1,quantity:2000,state:'passed',plate_snapshot:{version:'V1',registration_error_mm:0.08,approved_by:'admin'},batch_snapshot:{batch_no:1,lot_code:'PAPER-157-A',paper:'157g 哑粉'}}],
 paperLots:[{id:1,code:'PAPER-157-A',gsm:157,available_sheets:17000,state:'active'}],
 paperIssues:[{id:1,job_id:1,lot_id:1,batch_id:1,sheets:3000,returned_sheets:0,paper_snapshot:'157g 哑粉',issued_by:'p',returned_by:'',return_note:''}],
 batches:[{id:1,job_id:1,lot_id:1,batch_no:1,state:'active',lot_code:'PAPER-157-A',paper_snapshot:'157g 哑粉',net_sheets:3000,printed_sheets:2000,sealed_sheets:1000,opened_by:'p',opened_at:'2026-11-18T01:00:00+00:00',closed_at:null}],
 machines:[],inspections:[],
 packs:[{id:1,job_id:1,batch_id:1,code:'BOX-1',sheets:1000,state:'sealed',sample_snapshot:{batch_no:1,lot_code:'PAPER-157-A',proof_code:'PS-RUN-1',quantity:2000,net_paper:3000}}],
 shipments:[{id:1,job_id:1,tracking:'SHIP-A',customer:'禾页文化',state:'dispatched',pack_ids:[1],snapshot:{batch:{batch_no:1,lot_code:'PAPER-157-A'},quality:{proof_code:'PS-RUN-1',net_paper:3000},total_sheets:1000},dispatched_by:'admin',received_by:''}],
 proofId:null,adjustmentId:null};
const p=paper.render(s),m=samples.render(s),h=shipments.render(s);
const need=[[p,'批次#1','paper批次台账'],[p,'PAPER-157-A','paper批号'],[p,'本批已印2000张','paper批内累计'],[p,'request_id','paper幂等键'],
 [m,'领用批次#1','samples批次行'],[m,'批内累计2000张','samples批内累计'],[m,'request_id','samples幂等键'],
 [h,'批次#1 · PAPER-157-A','shipments批次标签'],[h,'封签依据 PS-RUN-1','箱放行依据'],[h,'合格累计2000张','箱合格累计'],[h,'跨批次箱单拒绝混装','混装规则提示'],[h,'request_id','shipments幂等键']];
let bad=0;
for(const [html,frag,label] of need){if(!html.includes(frag)){console.error('FAIL',label,'缺少',frag);bad++}else console.log('PASS',label)}
if(bad){process.exit(1)}console.log('三页渲染冒烟全部通过');
