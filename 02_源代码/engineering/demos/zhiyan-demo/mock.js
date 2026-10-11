/* All names, materials, dates and judgments below are synthetic demonstration fixtures. */
(function (root) {
  'use strict';
  if(typeof location!=='undefined'&&new URLSearchParams(location.search).get('mode')==='real')return;
  const at = (day, time) => `2026-09-${day}T${time}:00+08:00`;
  const NOW = at('12', '14:30'), LAST_VISIT = at('12', '09:00');
  const events = [
    {id:'EVT-DEMO-001',company:'澜川储能',symbol:'DEMO-001',initial:'澜',color:'blue',title:'江北储能基地一期建设与投产',sector:'新能源',category:'产能建设',priority:'高',first:at('10','09:30'),updated:at('12','13:40'),keyPending:true,impact:'不确定',impactText:'投产与收入实现时点信息不足；须取得投产日期与交付材料后再研判。',next:'等待正式投产日期',reminder:'新增公司公告 / 关键结论变化'},
    {id:'EVT-DEMO-002',company:'远川精工',symbol:'DEMO-002',initial:'远',color:'purple',title:'智能装备项目签署交付补充协议',sector:'高端制造',category:'合同订单',priority:'高',first:at('12','10:15'),updated:at('12','10:15'),keyPending:false,impact:'潜在正面',impactText:'订单交付可能改善业务节奏，前提是按补充协议完成履约。',next:'核对交付窗口',reminder:'交付安排变化 / 新增公司公告'},
    {id:'EVT-DEMO-003',company:'启衡材料',symbol:'DEMO-003',initial:'启',color:'orange',title:'新材料生产线恢复生产进展',sector:'新材料',category:'生产经营',priority:'中',first:at('09','15:00'),updated:at('12','11:20'),keyPending:true,impact:'不确定',impactText:'恢复生产对产出可能有影响，但缺少实际复产与验收材料。',next:'核查恢复生产材料',reminder:'新增复产材料 / 核验结论变化'},
    {id:'EVT-DEMO-004',company:'澜川储能',symbol:'DEMO-001',initial:'澜',color:'blue',title:'西岭研发中心设备采购计划',sector:'新能源',category:'研发投入',priority:'中',first:at('08','14:00'),updated:at('11','16:20'),keyPending:false,impact:'潜在正面',impactText:'研发条件可能改善，仍取决于采购和设备验收；与江北基地为不同项目。',next:'关注设备采购进度',reminder:'计划发生实质变化'},
    {id:'EVT-DEMO-005',company:'云岫科技',symbol:'DEMO-005',initial:'云',color:'teal',title:'算力服务框架合作计划',sector:'信息技术',category:'战略合作',priority:'低',first:at('07','09:30'),updated:at('11','10:40'),keyPending:false,impact:'不确定',impactText:'框架合作暂不代表形成订单，需确认正式合同及履约安排。',next:'等待正式合作合同',reminder:'新增正式合同'},
    {id:'EVT-DEMO-006',company:'沐辰物流',symbol:'DEMO-006',initial:'沐',color:'indigo',title:'区域配送网络调整安排',sector:'交通运输',category:'生产经营',priority:'低',first:at('06','11:30'),updated:at('10','15:00'),keyPending:false,impact:'中性',impactText:'网络调整的成本与服务影响仍需运营数据检验。',next:'关注网点调整执行情况',reminder:'运营安排变化'}
  ];
  const claims = ['主体工程已完成','公司已明确9月底投产','现有材料已披露规划产能'];
  const versions = [
    {id:'EV1-v1',eventId:events[0].id,number:1,effectiveAt:at('10','09:30'),label:'首次媒体报道',short:'出现投产日期说法，尚缺原始出处',summary:'媒体称公司已明确9月底投产，但未提供原始出处。项目建设进展、正式投产日期与规划产能均待核查。',status:'证据不足',tone:'orange',verdicts:['证据不足','待核实','证据不足'],reason:['尚无公司材料说明工程进度。','D01提出日期说法，但未提供原始出处。','可见材料未披露规划产能。']},
    {id:'EV1-v2',eventId:events[0].id,number:2,effectiveAt:at('11','11:00'),label:'公司披露建设进展',short:'主体工程已完成，投产日期仍待核查',summary:'公司材料支持主体工程已完成；工程完成不等于正式投产。9月底投产说法尚缺公司确认，规划产能仍待披露。',status:'部分支持',tone:'orange',verdicts:['材料支持','待核实','证据不足'],reason:['D03披露主体工程已完成；不等于正式投产。','公司建设进展材料未确认9月底投产。','公司材料没有披露规划产能。']},
    {id:'EV1-v3',eventId:events[0].id,number:3,effectiveAt:at('12','13:40'),label:'公司澄清日期说法',short:'公司澄清：未明确9月底投产',summary:'项目建设获得公司材料支持；公司否认已明确9月底投产。实际投产日期与规划产能仍待披露。',status:'部分支持',tone:'orange',verdicts:['材料支持','公司否认','证据不足'],reason:['D03支持主体工程已完成，不代表已投产。','D04否认“公司已明确9月底投产”这一说法；并不意味着项目终止或9月底绝无可能投产。','现有材料未披露规划产能，不能补填数值。']},
    ...events.slice(1).flatMap((e,i) => {
      const current = {id:`EV${i+2}-v${i===1?2:1}`,eventId:e.id,number:i===1?2:1,effectiveAt:e.updated,label:'最新实质变化',short:['新增交付补充协议，履约窗口待核对','出现恢复生产说法，尚待原始材料','披露研发设备采购计划','框架合作计划披露，具体订单未明确','披露区域配送网络调整安排'][i],summary:['演示摘要：远川精工披露智能装备项目交付补充协议，交付窗口与执行情况需要跟进。','演示摘要：启衡材料出现恢复生产相关说法，尚缺复产证明与验收材料。','演示摘要：澜川储能拟为西岭研发中心采购设备。本事件与江北储能基地不属于同一项目。','演示摘要：云岫科技披露算力服务框架合作计划，尚未配置正式合同材料。','演示摘要：沐辰物流计划调整区域配送网络，后续执行情况待跟进。'][i],status:['摘要待核','待核实','摘要待核','证据不足','摘要待核'][i],tone:i===1?'red':'orange',verdicts:[],reason:[]};
      return i===1 ? [{...current,id:'EV3-v1',number:1,effectiveAt:e.first,short:'生产线停产事项进入关注范围',summary:'演示摘要：生产线处于待核查状态；尚未收到恢复生产说法。'},current] : [current];
    })
  ];
  const materials = [
    {id:'D01',eventId:events[0].id,title:'江北储能基地一期项目投产安排引发关注',source:'产业新视线（虚构媒体）',kind:'媒体报道',publishedAt:at('10','09:30'),availableAt:at('10','09:30'),cluster:'CC-01',family:'媒体 A，独立性未知',quality:'原始出处缺失',body:'产业新视线消息，澜川储能江北储能基地一期建设受到关注。有消息称，公司已明确该项目将于9月底投产。报道未提供上述日期说法的公司原始文件或明确出处，也未披露规划产能。',quote:'有消息称，公司已明确该项目将于9月底投产。',relations:{C02:'提出说法'}},
    {id:'D02',eventId:events[0].id,title:'江北储能基地投产消息获转载',source:'制造资讯汇（虚构媒体）',kind:'媒体转载',publishedAt:at('10','10:20'),availableAt:at('10','10:20'),cluster:'CC-01',family:'依赖 D01，非独立佐证',quality:'明确转载',body:'本文转载自产业新视线《江北储能基地一期项目投产安排引发关注》。原文称公司已明确该项目将于9月底投产。本文未开展独立采访核查。',quote:'本文转载自产业新视线《江北储能基地一期项目投产安排引发关注》。',relations:{C02:'转载说法'}},
    {id:'D03',eventId:events[0].id,title:'关于江北储能基地一期建设进展的公告',source:'澜川储能（虚构公司）',kind:'公司公告',publishedAt:at('11','11:00'),availableAt:at('11','11:00'),cluster:'CC-02',family:'澜川储能公司来源',quality:'公司披露',body:'公司就江北储能基地一期项目建设情况说明如下：截至本说明披露日，该项目主体工程已完成，目前正在推进配套建设与设备调试。主体工程完成不代表已正式投产。本次说明未披露正式投产日期及规划产能。',quote:'该项目主体工程已完成，目前正在推进配套建设与设备调试。',relations:{C01:'支持'}},
    {id:'D04',eventId:events[0].id,title:'关于江北储能基地投产日期相关说法的澄清说明',source:'澜川储能（虚构公司）',kind:'公司回应',publishedAt:at('12','13:40'),availableAt:at('12','13:40'),cluster:'CC-03',family:'澜川储能公司来源',quality:'公司澄清',body:'针对近期有关江北储能基地一期项目的报道，公司澄清如下：公司并未明确该项目将于9月底投产。项目建设与调试工作仍在推进，实际投产日期及规划产能以公司后续正式披露为准。本说明不涉及项目终止。',quote:'公司并未明确该项目将于9月底投产。',relations:{C02:'反驳'}},
    {id:'D05',eventId:events[0].id,title:'澜川储能澄清投产日期说法',source:'储能快讯（虚构媒体）',kind:'媒体转载',publishedAt:at('12','14:10'),availableAt:at('12','14:10'),cluster:'CC-03',family:'依赖 D04，非独立佐证',quality:'明确转载',body:'本文转载澜川储能《关于江北储能基地投产日期相关说法的澄清说明》。公司并未明确该项目将于9月底投产。本文仅转述公司说明，不提供新增事实或独立核验。',quote:'本文转载澜川储能《关于江北储能基地投产日期相关说法的澄清说明》。',relations:{C02:'转载反驳'}}
  ].map(m=>({...m,timeBasis:'synthetic_fixture_time'}));
  const edges = [{id:'R01',from:'D01',to:'D02',observedAt:at('10','10:20'),quote:materials[1].quote},{id:'R02',from:'D04',to:'D05',observedAt:at('12','14:10'),quote:materials[4].quote}];
  const alertVersions = [
    {id:'AL1-v1',alertId:'AL-DEMO-001',eventId:events[0].id,eventVersion:'EV1-v2',number:1,time:at('11','11:05'),priority:'高',title:'关键投产日期说法待核查',reason:'主体工程已完成，但媒体所述9月底投产日期缺少公司确认。建立日期核查提醒。',change:'首次建立核查提醒',rule:'关键日期未确认 · 演示规则 V1.0'},
    {id:'AL1-v2',alertId:'AL-DEMO-001',eventId:events[0].id,eventVersion:'EV1-v3',number:2,time:at('12','13:45'),priority:'中',title:'公司澄清，原预警降级并更正',reason:'D04否认公司已明确9月底投产的说法；原高优先级日期核查提醒更新为中优先级，继续跟进正式披露。',change:'高 → 中；对原接收人发出模拟更正通知',rule:'公司澄清更正 · 演示规则 V1.0'},
    {id:'AL2-v1',alertId:'AL-DEMO-002',eventId:events[1].id,eventVersion:'EV2-v1',number:1,time:at('12','10:20'),priority:'高',title:'交付窗口变化需跟进',reason:'交付补充协议进入跟踪范围，需核对执行窗口。仅为演示摘要，未配置材料核验链。',change:'首次建立交付跟进提醒',rule:'交付安排变化 · 演示规则 V1.0'},
    {id:'AL3-v1',alertId:'AL-DEMO-003',eventId:events[2].id,eventVersion:'EV3-v2',number:1,time:at('12','11:25'),priority:'中',title:'恢复生产说法缺少材料',reason:'出现恢复生产说法，尚未配置原始复产及验收材料，需要继续核查。',change:'等待恢复生产材料',rule:'关键材料缺失 · 演示规则 V1.0'}
  ];
  const alerts = [{id:'AL-DEMO-001',eventId:events[0].id,versionId:'AL1-v2'},{id:'AL-DEMO-002',eventId:events[1].id,versionId:'AL2-v1'},{id:'AL-DEMO-003',eventId:events[2].id,versionId:'AL3-v1'}];

  // Monitor associations are explicit. A stock's sector never creates an industry impact.
  const objects = [...new Map(events.map(e=>[e.symbol,{id:'STK-'+e.symbol,type:'stock',code:e.symbol,name:e.company,codeSource:'演示个股标识（非证券代码）',initial:e.initial,color:e.color}])).values(),
    {id:'IND-STORAGE',type:'industry',code:'DEMO-IND-01',name:'储能行业',codeSource:'Demo 自定义行业口径',initial:'储',color:'teal'},
    {id:'IND-LOGISTICS',type:'industry',code:null,name:'城市配送行业',codeSource:'演示材料未提供行业代码',initial:'配',color:'purple'}
  ];
  events.forEach(e=>{e.subject=e.company;e.targetIds=['STK-'+e.symbol];});
  events.push(
    {id:'EVT-DEMO-007',subject:'江澄市能源主管部门（虚构）',targetIds:['IND-STORAGE','STK-DEMO-001'],title:'储能辅助服务试点办法与适用范围说明',sector:'新能源',category:'行业政策',priority:'中',first:at('12','09:20'),updated:at('12','13:00'),keyPending:true,next:'核对项目准入与补偿细则',reminder:'适用范围变化 / 新增正式细则'},
    {id:'EVT-DEMO-008',subject:'江澄市交通主管部门（虚构）',targetIds:['IND-LOGISTICS'],title:'城市配送低排放试点公开征求意见',sector:'交通运输',category:'行业政策',priority:'低',first:at('12','11:45'),updated:at('12','11:45'),keyPending:true,next:'等待正式方案与实施范围',reminder:'正式方案发布 / 适用范围变化'}
  );
  versions.push(
    {id:'EV7-v1',eventId:'EVT-DEMO-007',number:1,effectiveAt:at('12','09:20'),label:'试点办法发布',short:'储能项目可申请参与辅助服务试点',summary:'虚构主管部门发布试点办法：符合条件的储能项目可申请参与辅助服务。个股及具体项目的资格、收益规模尚未明确。',status:'材料支持',tone:'green',verdicts:[],reason:[]},
    {id:'EV7-v2',eventId:'EVT-DEMO-007',number:2,effectiveAt:at('12','13:00'),label:'补充准入范围说明',short:'准入限于已投运项目，建设中项目需另行核查',summary:'补充说明明确试点限于已投运且通过并网验收的项目。行业潜在参与机会仍存在，但不能据此确认澜川储能江北基地符合条件。',status:'部分支持',tone:'orange',verdicts:[],reason:[]},
    {id:'EV8-v1',eventId:'EVT-DEMO-008',number:1,effectiveAt:at('12','11:45'),label:'征求意见稿发布',short:'调整方案仍在征求意见，范围与成本待明确',summary:'虚构主管部门就城市配送低排放试点征求意见，尚未确定实施区域、过渡期及成本安排，不能据此断言全行业受益或承压。',status:'征求意见',tone:'orange',verdicts:[],reason:[]}
  );
  materials.push(
    {id:'P01',eventId:'EVT-DEMO-007',title:'储能辅助服务试点办法（演示）',source:'江澄市能源主管部门（虚构）',kind:'政策文件',publishedAt:at('12','09:20'),availableAt:at('12','09:20'),cluster:'CC-P01',family:'虚构能源主管部门',quality:'演示政策原文',body:'本试点允许符合条件的储能项目申请参与辅助服务。具体项目须按准入程序审核，补偿细则另行公布。本文件不构成对任何企业收益的承诺。',quote:'具体项目须按准入程序审核，补偿细则另行公布。',relations:{},timeBasis:'synthetic_fixture_time'},
    {id:'P02',eventId:'EVT-DEMO-007',title:'关于试点项目准入范围的补充说明（演示）',source:'江澄市能源主管部门（虚构）',kind:'政策说明',publishedAt:at('12','13:00'),availableAt:at('12','13:00'),cluster:'CC-P02',family:'虚构能源主管部门',quality:'演示政策原文',body:'本期试点面向已正式投运且通过并网验收的储能项目。建设中或调试中的项目不能直接视为符合条件。澜川储能江北基地未列入本次示例准入清单；具体企业资格和收益需另行核查。',quote:'建设中或调试中的项目不能直接视为符合条件。',relations:{},timeBasis:'synthetic_fixture_time'},
    {id:'I01',eventId:'EVT-DEMO-008',title:'城市配送低排放试点征求意见稿（演示）',source:'江澄市交通主管部门（虚构）',kind:'征求意见稿',publishedAt:at('12','11:45'),availableAt:at('12','11:45'),cluster:'CC-I01',family:'虚构交通主管部门',quality:'尚非生效政策',body:'现就城市配送低排放试点征求意见。实施区域、车辆要求、过渡期限与补贴安排尚待讨论。本稿尚未生效，不代表已对全部配送企业施加相同要求。',quote:'实施区域、车辆要求、过渡期限与补贴安排尚待讨论。',relations:{},timeBasis:'synthetic_fixture_time'}
  );
  const directions=['偏正面','中性','偏负面','不确定'];
  const judgments=versions.flatMap(v=>{
    const e=events.find(e=>e.id===v.eventId);
    return e.targetIds.map(objectId=>{
      const stockIndex=events.slice(0,6).indexOf(e),policy=e.id==='EVT-DEMO-007',industry=objectId==='IND-STORAGE';
      const materialIds=materials.filter(m=>m.eventId===e.id&&Date.parse(m.availableAt)<=Date.parse(v.effectiveAt)&&m.kind!=='媒体转载').map(m=>m.id);
      return {id:v.id+':'+objectId,eventId:e.id,eventVersion:v.id,objectId,category:policy&&!industry?'经营条件':e.category,
        direction:policy?(industry?'偏正面':'不确定'):stockIndex>=0?['不确定','偏正面','不确定','偏正面','不确定','不确定'][stockIndex]:'不确定',
        basis:policy?(industry?(v.number===1?'P01 提供参与试点的潜在机会，但适用范围及补偿机制仍待明确。':'P01、P02 支持符合准入条件的项目参与试点，行业内项目存在分化。'):(v.number===1?'P01 未说明澜川储能或江北基地的具体资格，不能从行业机会推导个股收益。':'P02 限于已投运并验收项目，未确认江北基地资格；不能据此判断公司收益。')):stockIndex>=0?(stockIndex===0?v.summary:e.impactText):'I01 仍属征求意见，实施范围与成本未知，尚不能判定方向。',
        unknowns:policy?(industry?'参与比例、补偿细则与行业内项目差异。':'具体项目准入、投入成本及对公司收入的贡献。'):stockIndex===0?'正式投产日期、规划产能与收入实现条件。':stockIndex>=0?'缺少完整原始材料及实际履行、成本或收入数据。':'正式方案、适用区域、过渡期限与成本承担。',
        association:policy?(industry?'政策明确针对储能项目，作为行业观察对象。':v.number===1?'研究员选定该公司进行关联核查；P01 未点名该公司，不代表已确认政策适用。':'P02 示例项目涉及该公司；关联不代表已获准参与。'):stockIndex>=0?'材料中的行动主体为该公司。':'征求意见稿明确涉及城市配送活动。',
        materialIds,confidence:null,eventEmotion:null,source:'固定演示初判'};
    });
  });
  alertVersions.push(
    {id:'AL4-v1',alertId:'AL-DEMO-004',eventId:'EVT-DEMO-007',objectId:'IND-STORAGE',eventVersion:'EV7-v1',number:1,time:at('12','09:25'),priority:'中',title:'储能行业试点办法进入关注',reason:'试点可能带来行业参与机会，准入及补偿细则仍需跟进。',change:'首次建立行业跟进提醒',rule:'政策条件变化 · 演示规则 V1.0'},
    {id:'AL4-v2',alertId:'AL-DEMO-004',eventId:'EVT-DEMO-007',objectId:'IND-STORAGE',eventVersion:'EV7-v2',number:2,time:at('12','13:05'),priority:'中',title:'准入条件明确，行业判断补充限制',reason:'P02 限定已投运并验收项目，保留行业偏正面初判，同时记录项目内部分化与收益未知项。',change:'原行业预警补充适用边界，保留旧版依据',rule:'政策范围补充 · 演示规则 V1.0'},
    {id:'AL5-v1',alertId:'AL-DEMO-005',eventId:'EVT-DEMO-007',objectId:'STK-DEMO-001',eventVersion:'EV7-v2',number:1,time:at('12','13:06'),priority:'低',title:'澜川储能项目资格尚不确定',reason:'同一政策未确认公司项目准入或收益，记录对象差异并等待核查。',change:'个股对象单独跟进，不复制行业方向',rule:'对象资格未明确 · 演示规则 V1.0'}
  );
  alerts.push({id:'AL-DEMO-004',eventId:'EVT-DEMO-007',versionId:'AL4-v2'},{id:'AL-DEMO-005',eventId:'EVT-DEMO-007',versionId:'AL5-v1'});
  alertVersions.forEach(v=>{
    v.objectId ||= events.find(e=>e.id===v.eventId).targetIds[0];
    v.judgmentId=judgments.find(j=>j.eventVersion===v.eventVersion&&j.objectId===v.objectId).id;
    v.materialIds=materials.filter(m=>m.eventId===v.eventId&&Date.parse(m.availableAt)<=Date.parse(v.time)).map(m=>m.id);
  });
  const ingressIds=['D01','D02','D03','D04','D05'];

  const initialState = () => ({schema:1,watches:Object.fromEntries(events.slice(0,4).map((e,i)=>[e.id,{id:`W0${i+1}`,enabled:true,reminders:true,lastReadVersion:i===0?'EV1-v2':i===2?'EV3-v1':`EV${i+1}-v1`,condition:e.reminder}])),tasks:[{id:'T01',eventId:events[0].id,title:'等待正式投产日期',reviewAt:'2026-09-15',state:'pending'},{id:'T02',eventId:events[1].id,title:'核对交付窗口',reviewAt:'2026-09-14',state:'pending'},{id:'T03',eventId:events[2].id,title:'核查恢复生产材料',reviewAt:'2026-09-14',state:'pending'}],handling:Object.fromEntries(alerts.map((a,i)=>[a.id,{state:['pending','in_progress','waiting_material'][i]||'pending',owner:'张研究员',reason:'',note:''}])),subscriptions:{'IND-STORAGE':true},reviews:[],notificationRead:{},notes:[],logs:[],ui:{radar:{query:'',tab:'all',scope:'all',sector:'',category:'',impact:'',page:1,size:6,order:'priority',scroll:0},lastEvent:null}});
  const STORAGE_KEY='zhiyan.demo.v1';
  let state,storageWarning='';
  try {const saved=JSON.parse(root.localStorage?.getItem(STORAGE_KEY)||'null');state=saved?.schema===1&&saved.watches&&saved.tasks&&saved.handling&&saved.ui&&saved.logs&&saved.notes&&saved.notificationRead?saved:initialState();}catch{state=initialState();storageWarning='本地记录读取失败，已载入初始演示。';}
  state.subscriptions ||= {};state.reviews ||= [];state.ui.radar.scope ||= 'all';
  for(const a of alerts)state.handling[a.id] ||= {state:'pending',owner:'张研究员',reason:'',note:''};
  function persist(){try{root.localStorage?.setItem(STORAGE_KEY,JSON.stringify(state));return true;}catch{storageWarning='浏览器禁止本地保存，本次操作只在当前页面保留。';return false;}}
  function log(action,target,detail){state.logs.push({action,target,detail,operatedAt:new Date().toISOString()});persist();}
  function freeze(value){Object.values(value).forEach(v=>{if(v&&typeof v==='object'&&!Object.isFrozen(v))freeze(v);});return Object.freeze(value);}
  const fixture=freeze({events,versions,materials,edges,claims,alerts,alertVersions,objects,judgments,directions,NOW,LAST_VISIT});
  const requireEvent=id=>{const e=events.find(e=>e.id===id);if(!e)throw new Error('未找到该演示事件。');return e;};
  function validTime(asOf){if(typeof asOf!=='string'||!/^\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d)?(Z|[+-]\d\d:\d\d)$/.test(asOf)||!Number.isFinite(Date.parse(asOf))||Date.parse(asOf)>Date.parse(NOW))throw new Error('无法打开该时间组合：请使用带时区且不晚于演示时点的有效时间。');return Date.parse(asOf);}
  // ponytail: the ordered five-material fixture is the entire ingress queue; real uploads need a separate ingestion contract.
  function loadedMaterialCount(){return state.demo?.loaded??ingressIds.length;}
  function listEventVersions(eventId,asOf=NOW){requireEvent(eventId);const cutoff=validTime(asOf),required={'EV1-v1':1,'EV1-v2':3,'EV1-v3':4};return versions.filter(v=>v.eventId===eventId&&Date.parse(v.effectiveAt)<=cutoff&&(!required[v.id]||required[v.id]<=loadedMaterialCount()));}
  function listAlertVersions(alertId,asOf=NOW){const cutoff=validTime(asOf);return alertVersions.filter(v=>(!alertId||v.alertId===alertId)&&Date.parse(v.time)<=cutoff&&listEventVersions(v.eventId,asOf).some(e=>e.id===v.eventVersion));}
  function getEventSnapshot(eventId,asOf=NOW,versionId){const event=requireEvent(eventId),cutoff=validTime(asOf);const availableVersions=listEventVersions(eventId,asOf);const version=availableVersions.at(-1);if(!version)throw new Error('此时点尚未监测到该事件。');if(versionId&&version.id!==versionId)throw new Error('无法打开该组合：事件版本与事件、截至时点或已载入材料不一致。');const visible=visibleMaterials().filter(m=>m.eventId===eventId&&Date.parse(m.publishedAt)<=cutoff&&Date.parse(m.availableAt)<=cutoff);return {event,version,asOf,versions:availableVersions,materials:visible,edges:edges.filter(r=>Date.parse(r.observedAt)<=cutoff&&visible.some(m=>m.id===r.from)&&visible.some(m=>m.id===r.to))};}
  function visibleMaterials(){return materials.filter(m=>!ingressIds.includes(m.id)||ingressIds.indexOf(m.id)<loadedMaterialCount());}
  function getObject(id){const o=objects.find(o=>o.id===id);if(!o)throw new Error('未找到该监测对象。');return o;}
  function eventObjects(eventId,scope='all'){return requireEvent(eventId).targetIds.map(getObject).filter(o=>scope==='all'||o.type===scope);}
  function relatedIndustries(eventId){const e=requireEvent(eventId);return [...new Set([e.sector,...eventObjects(eventId,'industry').map(o=>o.name)])].filter(Boolean);}
  function getAssessment(eventId,objectId,asOf=NOW,versionId){
    const s=getEventSnapshot(eventId,asOf,versionId);if(!s.event.targetIds.includes(objectId))throw new Error('监测对象不属于当前事件。');
    const judgment=judgments.find(j=>j.eventId===eventId&&j.objectId===objectId&&j.eventVersion===s.version.id);
    if(!judgment)throw new Error('该对象在所选版本没有判断。');
    const history=state.reviews.filter(r=>r.eventId===eventId&&r.objectId===objectId&&s.versions.some(v=>v.id===r.eventVersion)),review=history.filter(r=>r.judgmentId===judgment.id).at(-1)||null;
    return {object:getObject(objectId),judgment,review,previousReviews:history.filter(r=>r.judgmentId!==judgment.id)};
  }
  function recordReview(eventId,objectId,eventVersion,result,reason,asOf=NOW){
    const a=getAssessment(eventId,objectId,asOf,eventVersion);
    if(!directions.includes(result)||typeof reason!=='string'||!reason.trim()||reason.length>1000)throw new Error('请选择有效方向，并填写不超过 1000 字的复核理由。');
    const record={id:'REVIEW-'+(state.reviews.length+1),eventId,eventVersion,objectId,judgmentId:a.judgment.id,systemOriginal:a.judgment.direction,result,reason:reason.trim(),operatedAt:new Date().toISOString(),operator:'张研究员',status:result===a.judgment.direction?'confirmed':'corrected'};
    state.reviews.push(record);log('对象方向复核',eventId,record);return record;
  }
  function setSubscription(objectId,enabled){getObject(objectId);state.subscriptions[objectId]=!!enabled;log('对象订阅',objectId,enabled?'已订阅':'已取消');}
  function listSubscriptions(){return objects.filter(o=>state.subscriptions[o.id]).map(o=>({...o,events:listEvents({objectId:o.id})}));}
  function listEvents(filters={},asOf=NOW){
    validTime(asOf);
    return events.filter(e=>Date.parse(e.first)<=Date.parse(asOf)).map(e=>({...e,version:getEventSnapshot(e.id,asOf).version})).filter(e=>{
      const targets=eventObjects(e.id,filters.scope||'all').filter(o=>!filters.objectId||o.id===filters.objectId);
      if(!targets.length)return false;
      const searchable=[e.title,e.id,e.subject,...targets.flatMap(o=>[o.name,o.code||'']),...relatedIndustries(e.id)].join(' ').toLowerCase();
      const matching=targets.map(o=>getAssessment(e.id,o.id,asOf).judgment).some(j=>(!filters.category||j.category===filters.category)&&(!filters.impact||j.direction===filters.impact));
      return (!filters.query||searchable.includes(filters.query.toLowerCase()))&&(!filters.sector||relatedIndustries(e.id).includes(filters.sector))&&matching&&(!filters.tab||filters.tab==='all'||filters.tab==='new'&&Date.parse(e.first)>Date.parse(LAST_VISIT)||filters.tab==='updated'&&Date.parse(e.version.effectiveAt)>Date.parse(LAST_VISIT)||filters.tab==='pending'&&e.keyPending||filters.tab==='followed'&&state.watches[e.id]?.enabled);
    });
  }
  function getVersionDiff(eventId,from,to){const available=listEventVersions(eventId);const current=available.find(v=>v.id===to);const previous=from?available.find(v=>v.id===from):null;if(!current||from&&!previous||previous&&Date.parse(previous.effectiveAt)>Date.parse(current.effectiveAt))throw new Error('无法比较：版本未载入、不属于同一事件或顺序无效。');return {previous,current};}
  function getMaterial(id,asOf=NOW){const cutoff=validTime(asOf);const m=visibleMaterials().find(m=>m.id===id&&Date.parse(m.publishedAt)<=cutoff&&Date.parse(m.availableAt)<=cutoff);if(!m)throw new Error('该材料尚未载入或在所选时点不可见。');return m;}
  function setFollow(id,enabled,options={}){requireEvent(id);const old=state.watches[id];state.watches[id]={id:old?.id||`W-${id}`,lastReadVersion:old?.lastReadVersion||getEventSnapshot(id).version.id,reminders:old?.reminders??true,condition:old?.condition||requireEvent(id).reminder,...options,enabled};log('关注设置',id,enabled?'已关注':'已取消关注');}
  function markWatchRead(id,versionId){const w=Object.values(state.watches).find(w=>w.id===id);const eventId=Object.keys(state.watches).find(k=>state.watches[k]===w);if(!w||getEventSnapshot(eventId).version.id!==versionId)throw new Error('关注记录与最新事件版本不匹配。');w.lastReadVersion=versionId;log('标为已读',id,versionId);}
  function updateFollowUpTask(id,next){const task=state.tasks.find(t=>t.id===id);if(!task||!['pending','completed'].includes(next))throw new Error('跟进任务无效。');task.state=next;log('更新跟进任务',id,next);}
  function getAlertVersion(id,versionId){const a=alerts.find(a=>a.id===id),available=listAlertVersions(id);const version=versionId?available.find(v=>v.id===versionId):available.at(-1);if(!a||!version)throw new Error('预警尚未生成，或指定版本尚未载入 / 不匹配。');return {...a,versionId:version.id,version,objectId:version.objectId,object:getObject(version.objectId),judgment:judgments.find(j=>j.id===version.judgmentId),handling:state.handling[id]};}
  function listAlerts(filters={}){return alerts.filter(a=>listAlertVersions(a.id).length).map(a=>getAlertVersion(a.id)).filter(a=>(!filters.scope||a.object.type===filters.scope)&&(!filters.state||a.handling.state===filters.state)&&(!filters.priority||a.version.priority===filters.priority)&&(!filters.query||`${requireEvent(a.eventId).title} ${a.object.name} ${a.object.code||''} ${a.id}`.includes(filters.query)));}
  function updateAlertHandling(id,next,reason,note,owner='张研究员'){const a=getAlertVersion(id);if(!['pending','in_progress','waiting_material','closed'].includes(next))throw new Error('请选择有效的处置状态。');if((next==='closed'||a.handling.state==='closed'&&next!=='closed')&&!reason?.trim())throw new Error(next==='closed'?'关闭预警需要填写原因。':'重新开启预警需要填写原因。');if(!owner.trim())throw new Error('请填写责任人。');state.handling[id]={state:next,reason:reason?.trim()||'',note:note.trim(),owner:owner.trim()};log('预警处置',id,{from:a.handling.state,to:next,reason,note});}
  function getNotifications(alertId){return listAlertVersions(alertId).map(v=>({id:`N-${v.id}`,alertId:v.alertId,alertVersion:v.id,title:v.alertId==='AL-DEMO-001'&&v.number===2?'预警更正：投产日期核查提醒降级':v.title,objectId:v.objectId,recipient:'张研究员',channel:'模拟站内送达',time:v.time,readAt:state.notificationRead[`N-${v.id}`]||null,number:v.number}));}
  function materialDemoSummary(){const s=getEventSnapshot(events[0].id),a=listAlertVersions('AL-DEMO-001'),w=state.watches[events[0].id];return {materials:s.materials.length,version:s.version.number,verdicts:s.version.verdicts,alerts:a.length?1:0,alertVersion:a.at(-1)?.number||0,priority:a.at(-1)?.priority||'无',notifications:getNotifications('AL-DEMO-001').length,unread:!!w?.enabled&&w.lastReadVersion!==s.version.id};}
  function loadInitialMaterialState(){state=initialState();state.demo={loaded:1,result:null};state.watches[events[0].id].lastReadVersion='EV1-v1';log('载入演示初始状态',events[0].id,'仅载入 D01；其余事件保持完整摘要样本');return materialDemoSummary();}
  function simulateNextMaterial(){if(!state.demo)throw new Error('请先载入初始状态。');if(state.demo.loaded>=ingressIds.length)return null;const before=materialDemoSummary(),material=materials.find(m=>m.id===ingressIds[state.demo.loaded]);state.demo.loaded++;const after=materialDemoSummary();const result={materialId:material.id,title:material.title,kind:material.kind,before,after};state.demo.result=result;log('模拟加入材料',material.id,result);return result;}
  function getMaterialDemo(){return {active:!!state.demo,loaded:loadedMaterialCount(),total:ingressIds.length,next:state.demo?materials.find(m=>m.id===ingressIds[state.demo.loaded])||null:null,result:state.demo?.result||null,summary:materialDemoSummary()};}
  function resetDemoState(){state=initialState();persist();}
  root.DemoRepo={fixture,get state(){return state;},get storageWarning(){return storageWarning;},persist,log,listEvents,listEventVersions,getEventSnapshot,getVersionDiff,getMaterial,setFollow,markWatchRead,updateFollowUpTask,listAlerts,listAlertVersions,getAlertVersion,updateAlertHandling,getNotifications,resetDemoState,loadInitialMaterialState,simulateNextMaterial,getMaterialDemo,getObject,eventObjects,relatedIndustries,getAssessment,recordReview,setSubscription,listSubscriptions};
  if(typeof module!=='undefined')module.exports=root.DemoRepo;
})(typeof window==='undefined'?globalThis:window);
