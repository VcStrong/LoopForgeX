// ==== 阶段二接口的模拟数据层（按 openapi.v1.yaml 的结构造假数据；一键切换，默认关闭走真接口）
// 开：网址加 ?mock=1（记在本机浏览器）；关：?mock=0。只拦截下面列出的阶段二接口，其余接口照常请求服务器。
const KBMock=(()=>{
  try{const m=new URLSearchParams(location.search).get('mock');if(m==='1')localStorage.setItem('kbMock','1');else if(m==='0')localStorage.removeItem('kbMock')}catch(_){}
  const on=()=>{try{return localStorage.getItem('kbMock')==='1'}catch(_){return false}};
  const now=Math.floor(Date.now()/1000),ts=o=>new Date((now+o)*1000).toLocaleString('sv').slice(0,16);
  const S={
    quota:{每人:3,每项目:8,队列每项目:50},
    runners:[
      {id:'r-mac1',名称:'mac-mini-01',状态:'在线',自动标签:['mac','android-sdk','adb','jdk17'],自定义标签:['内网'],容量:4,
        运行中:[{id:'T-1021',类型:'开发',期:'登录改版',发起人:'zhangsan',状态:'运行'},{id:'T-1023',类型:'构建安装',期:'登录改版',发起人:'lisi',状态:'已派'}],
        副本:[{仓库:'app-android',序号:1,路径:'/data/wc/app-android/1',分支:'feature-login',马甲线:'',占用会话:'dev-1021',可能有改动:true},{仓库:'app-android',序号:2,路径:'/data/wc/app-android/2',分支:'main',马甲线:'',占用会话:null,可能有改动:false}],
        手机:[{序列号:'R5CT1234ABC',型号:'Pixel 7',占用会话:'dev-1021'},{序列号:'9A221FFAZ0012',型号:'Redmi K60',占用会话:null}],
        磁盘:{可用GB:312,总GB:512},版本:'1.4.2',最后心跳:ts(-5),CPU:0.42,内存可用比:0.38,磁盘使用比:0.39,自检:[{项:'adb',通过:true,原因:''},{项:'git',通过:true,原因:''}]},
      {id:'r-lin2',名称:'build-linux-02',状态:'在线',自动标签:['linux','android-sdk','jdk17'],自定义标签:[],容量:6,运行中:[],副本:[],手机:[],磁盘:{可用GB:21,总GB:500},版本:'1.4.2',最后心跳:ts(-8),CPU:0.12,内存可用比:0.71,磁盘使用比:0.96,自检:[{项:'adb',通过:false,原因:'没找到 adb 命令'}]},
      {id:'r-mac3',名称:'mac-old-03',状态:'离线',自动标签:['mac'],自定义标签:['备用'],容量:2,运行中:[],副本:[],手机:[],磁盘:{可用GB:90,总GB:256},版本:'1.3.9',最后心跳:ts(-7200),CPU:0,内存可用比:0,磁盘使用比:0.65,自检:[]}],
    alerts:[{id:'A1',执行机:'r-lin2',类型:'磁盘',级别:'严重',开始:ts(-3600),恢复:'',确认人:''},{id:'A2',执行机:'r-mac3',类型:'离线',级别:'警告',开始:ts(-7200),恢复:'',确认人:'admin'},{id:'A3',执行机:'r-mac1',类型:'CPU',级别:'警告',开始:ts(-86400),恢复:ts(-80000),确认人:''}],
    tasks:[{id:'T-1021',类型:'开发',期:'登录改版',项目:'app',发起人:'zhangsan',状态:'运行',排队位置:null,等待原因:'',执行机:'mac-mini-01',重试次数:0,创建时间:ts(-3000)},
      {id:'T-1023',类型:'构建安装',期:'登录改版',项目:'app',发起人:'lisi',状态:'已派',排队位置:null,等待原因:'',执行机:'mac-mini-01',重试次数:0,创建时间:ts(-600)},
      {id:'T-1024',类型:'Bug修复',期:'支付优化',项目:'app',发起人:'zhangsan',状态:'排队',排队位置:1,等待原因:'你已有 3 个在跑',执行机:null,重试次数:0,创建时间:ts(-300)},
      {id:'T-1025',类型:'运行到手机',期:'支付优化',项目:'app',发起人:'wangwu',状态:'排队',排队位置:2,等待原因:'没有连着手机的 Mac 在线',执行机:null,重试次数:0,创建时间:ts(-200)},
      {id:'T-1019',类型:'Bug分析',期:'',项目:'app',发起人:'lisi',状态:'失败重试中',排队位置:null,等待原因:'',执行机:'build-linux-02',重试次数:1,创建时间:ts(-5000)},
      {id:'T-1018',类型:'开发',期:'设置页',项目:'app',发起人:'wangwu',状态:'完成',排队位置:null,等待原因:'',执行机:'mac-mini-01',重试次数:0,创建时间:ts(-9000)}],
    accts:[{id:'E1',引擎:'claude',名称:'团队 Claude 主号',类型:'项目共享',项目:'app',所属人:'',并发上限:3,RPM上限:30,运行中:2,状态:'可用',冷却到:'',原因:'',模型不可用:[],备用顺序:1,允许换引擎:false},
      {id:'E2',引擎:'claude',名称:'团队 Claude 备号',类型:'项目共享',项目:'app',所属人:'',并发上限:2,RPM上限:20,运行中:0,状态:'冷却',冷却到:ts(1500),原因:'用量限制，约 25 分钟后恢复',模型不可用:['opus'],备用顺序:2,允许换引擎:true},
      {id:'E3',引擎:'codex',名称:'Codex 号',类型:'项目共享',项目:'app',所属人:'',并发上限:2,RPM上限:20,运行中:0,状态:'不可用',冷却到:'',原因:'登录态失效，请重新登录',模型不可用:[],备用顺序:3,允许换引擎:true},
      {id:'E4',引擎:'claude',名称:'我的 Claude',类型:'个人',项目:'',所属人:'me',并发上限:2,RPM上限:30,运行中:0,状态:'未检测',冷却到:'',原因:'',模型不可用:[],备用顺序:0,允许换引擎:false,优先用我的:true}],
    res:[{id:'ph-R5CT1234ABC',类型:'手机',提供方:'runner',执行机:'mac-mini-01',属性:{系统:'Android',版本:'14',品牌:'Google',机型:'Pixel 7'},允许同时使用数:1,
        占用:[{资源:'ph-R5CT1234ABC',类型:'手机',会话:'dev-1021',期:'登录改版',人:'zhangsan',方式:'自动',开始:ts(-1800),到期:ts(1800)}],
        队列:[{位置:1,人:'lisi',期:'支付优化',排队时间:ts(-300),预计分钟:30}]},
      {id:'ph-9A221FFAZ0012',类型:'手机',提供方:'runner',执行机:'mac-mini-01',属性:{系统:'Android',版本:'13',品牌:'Redmi',机型:'K60'},允许同时使用数:1,占用:[],队列:[]},
      {id:'ta-1',类型:'测试账号',提供方:'local',执行机:'',属性:{项目:'app',环境:'测试',标签:['VIP']},允许同时使用数:2,
        占用:[{资源:'ta-1',类型:'测试账号',会话:'dev-1021',期:'登录改版',人:'zhangsan',方式:'申请',开始:ts(-900),到期:ts(2700)}],队列:[]},
      {id:'eng-claude',类型:'引擎名额',提供方:'local',执行机:'',属性:{引擎:'claude'},允许同时使用数:3,
        占用:[{资源:'eng-claude',类型:'引擎名额',会话:'dev-1021',期:'登录改版',人:'zhangsan',方式:'自动',开始:ts(-3000),到期:ts(600)},{资源:'eng-claude',类型:'引擎名额',会话:'dev-1030',期:'设置页',人:'wangwu',方式:'自动',开始:ts(-1000),到期:ts(2000)}],
        队列:[{位置:1,人:'lisi',期:'支付优化',排队时间:ts(-100),预计分钟:12}]}],
    tacc:[{id:'ta-1',项目:'app',环境:'测试',名称:'VIP 用户',账号:'13800000001',密码:'******',标签:['VIP'],允许同时使用数:2,允许AI:true,占用:[]},
      {id:'ta-2',项目:'app',环境:'线上',名称:'线上只读号',账号:'readonly@example.com',密码:'******',标签:[],允许同时使用数:1,允许AI:false,占用:[]}],
    prov:[{id:'local',类型:'local',名称:'本机 adb',启用:true,状态:'正常',模拟:false,设备数:1},{id:'runner',类型:'runner',名称:'执行机上的手机',启用:true,状态:'正常',模拟:false,设备数:2},{id:'remote',类型:'remote',名称:'远程真机平台',启用:false,状态:'未启用',模拟:true,设备数:0,模拟行为:'正常'}],
    art:[{id:'AF1',版本:'3.8.0-rc1',分支:'feature-login',提交:'a1b2c3d',sha256:'9f2c1a7e33d0aa51b7c5e2f4a0b19e6d4c8a77f0e2b3d5a6c1f90e8d7b6a5c4f',大小:48234496,执行机:'mac-mini-01',时间:ts(-1200)}],
  };
  const metric=h=>{const pts=[],n=Math.min(h*12,288);for(let i=0;i<n;i++){const t=now-(n-i)*300,k=i/n;
    pts.push({时间:t,CPU:+(0.3+0.25*Math.sin(i/9)+0.1*Math.random()).toFixed(2),内存可用比:+(0.45-0.1*Math.sin(i/20)+0.03*Math.random()).toFixed(2),磁盘使用比:+(0.35+0.2*k).toFixed(3),
      网络:{'知识库':{延迟ms:20+Math.round(15*Math.random())},'Git 远端':{延迟ms:60+Math.round(80*Math.random()*(i>200?3:1))},'Maven 仓库':{延迟ms:35+Math.round(30*Math.random())}}})}return pts};
  const find=(a,id)=>a.find(x=>x.id===id);
  const H={
    'GET /api/queue':q=>({items:S.tasks.filter(t=>(!q.status||t.状态===q.status)&&(q.mine!=='1'||t.发起人==='zhangsan')&&(!q.period||t.期===q.period))}),
    'POST /api/queue/cancel':(q,b)=>{const t=find(S.tasks,b.id);if(t)t.状态='取消';return{msg:'已取消 '+b.id}},
    'POST /api/quota':(q,b)=>{Object.assign(S.quota,b);return{msg:'已保存'}},
    'GET /api/quota':()=>S.quota,
    'GET /api/runners':()=>({items:S.runners,installHelp:'curl -fsSL http://KB_HOST:11555/runner/install.sh | bash -s -- --token <注册令牌>'}),
    'POST /api/runners/token':(q,b)=>({token:'kbreg_mock_'+Math.random().toString(36).slice(2,12),expires:ts(86400),command:'curl -fsSL http://KB_HOST:11555/runner/install.sh | bash -s -- --token kbreg_mock_xxxx --name '+(b.name||'新执行机')}),
    'POST /api/runners/update':(q,b)=>{const r=find(S.runners,b.id);if(!r)return{msg:'无此执行机'};
      if(b.动作==='删除')S.runners=S.runners.filter(x=>x!==r);else if(b.动作){r.状态={启用:'在线',排空:'排空中',停用:'停用'}[b.动作]||r.状态}
      if(b.自定义标签)r.自定义标签=b.自定义标签;if(b.容量!=null)r.容量=b.容量;return{msg:'已更新（模拟）'}},
    'GET /api/runners/metrics':q=>({点:metric(+q.hours||24)}),
    'GET /api/alerts':q=>({告警:S.alerts.filter(a=>q.state==='全部'||(q.state==='已恢复'?a.恢复:!a.恢复))}),
    'POST /api/alerts/ack':(q,b)=>{const a=find(S.alerts,b.id);if(a)a.确认人='admin';return{msg:b.静默分钟?`已静默 ${b.静默分钟} 分钟`:'已确认'}},
    'GET /api/task/score':q=>({选中:'mac-mini-01',候选:[{执行机:'mac-mini-01',总分:86,明细:{标签匹配:40,空闲容量:20,副本命中:18,手机:8},排除原因:''},{执行机:'build-linux-02',总分:41,明细:{标签匹配:30,空闲容量:11},排除原因:''},{执行机:'mac-old-03',总分:0,明细:{},排除原因:'离线'}]}),
    'GET /api/engine-accounts':q=>({账号:S.accts.filter(a=>!q.project||a.项目===q.project||a.类型==='个人')}),
    'POST /api/engine-account':(q,b)=>{if(b.删除){S.accts=S.accts.filter(a=>a.id!==b.id);return{msg:'已删除',id:b.id}}
      let a=b.id&&find(S.accts,b.id);if(!a){a={id:'E'+(S.accts.length+9),运行中:0,状态:'未检测',冷却到:'',原因:'',模型不可用:[],所属人:b.类型==='个人'?'me':''};S.accts.push(a)}
      Object.assign(a,b);delete a.登录态;delete a.删除;return{msg:'已保存（模拟）',id:a.id}},
    'POST /api/engine-account/probe':(q,b)=>{const a=find(S.accts,b.id);if(a){a.状态='可用';a.原因='';a.冷却到=''}return a||{}},
    'GET /api/usage':q=>({行:{人:[['zhangsan',41,52000,310000,98000,false],['lisi',22,31000,150000,52000,true]],项目:[['app',63,83000,460000,150000,false]],账号:[['团队 Claude 主号',40,50000,300000,90000,false],['团队 Claude 备号',23,33000,160000,60000,true]]}[q.by||'项目'].map(r=>({对象:r[0],会话数:r[1],时长秒:r[2],输入token:r[3],输出token:r[4],估算:r[5],预算日:20,预算月:400,已用比:0.37}))}),
    'POST /api/budget':()=>({msg:'预算已保存（模拟）'}),
    'POST /api/budget/grant':()=>({msg:'已临时加额，24 小时有效（模拟）'}),
    'GET /api/resources':q=>({资源:S.res.filter(r=>!q.type||r.类型===q.type)}),
    'POST /api/resource/release':(q,b)=>{const r=find(S.res,b.资源);if(r)r.占用=r.占用.filter(h=>b.会话&&h.会话!==b.会话);return{msg:(b.强制?'已强制释放':'已释放')+'（模拟）'}},
    'GET /api/test-accounts':q=>({账号:S.tacc.filter(a=>(!q.project||a.项目===q.project)&&(!q.env||a.环境===q.env))}),
    'POST /api/test-account':(q,b)=>{if(b.删除){S.tacc=S.tacc.filter(a=>a.id!==b.id);return{msg:'已删除',id:b.id}}
      let a=b.id&&find(S.tacc,b.id);if(!a){a={id:'ta-'+(S.tacc.length+5),占用:[],密码:'******'};S.tacc.push(a)}
      const p=b.密码;delete b.密码;Object.assign(a,b);if(p)a.密码='******';return{msg:'已保存（模拟）',id:a.id}},
    'GET /api/device-providers':()=>({提供方:S.prov}),
    'POST /api/device-provider':(q,b)=>{const p=find(S.prov,b.id);if(p){if(b.启用!=null){p.启用=b.启用;p.状态=b.启用?'正常':'未启用'}if(b.模拟行为){p.模拟行为=b.模拟行为;if(p.启用)p.状态=b.模拟行为==='故意失败'?'不可用':'正常'}}return{msg:'已保存（模拟）'}},
    'GET /api/artifacts':()=>({产物:S.art}),
  };
  function handle(u,opt){
    if(!on())return;
    const m=((opt&&opt.method)||'GET').toUpperCase(),i=u.indexOf('?'),path=i<0?u:u.slice(0,i),q=Object.fromEntries(new URLSearchParams(i<0?'':u.slice(i+1)));
    const h=H[m+' '+path];if(!h)return;
    let b={};try{b=opt&&opt.body?JSON.parse(opt.body):{}}catch(_){}
    return new Promise(ok=>setTimeout(()=>ok(JSON.parse(JSON.stringify(h(q,b)))),120));
  }
  return{on,handle,
    banner(){return on()?'<div class="pgmock" title="接口还没接通时的演示数据；网址加 ?mock=0 关闭">⚠ 模拟数据：阶段二接口用的是假数据（?mock=0 关闭）</div>':''}};
})();
