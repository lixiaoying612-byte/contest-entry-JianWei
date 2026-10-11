/* Real-data adapter for the existing four-page shell; no mock facts or model calls. */
window.RealWorkbench = (() => {
  'use strict';
  const $=s=>document.querySelector(s), esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const shapes={radar:'<path d="m3 10 9-7 9 7v10H4V10"/><path d="M9 20v-7h6v7"/>',file:'<path d="M6 3h9l4 4v14H6Z"/><path d="M14 3v5h5M9 12h7M9 16h5"/>',target:'<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',alert:'<path d="m12 3 10 18H2Z"/><path d="M12 9v5M12 17h.01"/>',database:'<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',close:'<path d="m6 6 12 12M6 18 18 6"/>',settings:'<path d="m12 2 9 5v10l-9 5-9-5V7Z"/><circle cx="12" cy="12" r="3"/>',bell:'<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4M12 2V1"/>',chevron:'<path d="m7 9 5 5 5-5"/>',users:'<circle cx="9" cy="7" r="4"/><path d="M2 21v-3a7 7 0 0 1 14 0v3ZM16 3a4 4 0 0 1 0 8M18 14c3 0 4 3 4 7"/>',chat:'<path d="M21 11a9 9 0 1 0-15 7l-1 4 5-2a9 9 0 0 0 11-9Z"/><path d="M7 10h.1M12 10h.1M17 10h.1"/>',shield:'<path d="m12 2 9 4v6c0 5-6 9-9 10-3-1-9-5-9-10V6Z"/><path d="m8 12 3 3 5-6"/>'};
  shapes.network='<circle cx="5" cy="6" r="3"/><circle cx="19" cy="6" r="3"/><circle cx="12" cy="19" r="3"/><path d="m6.5 8.5 4 8m7-8-4 8M8 6h8"/>';
  const icon=n=>`<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${shapes[n]||shapes.file}</svg>`;
  const btn=(label,action,id='',tone='')=>`<button class="button ${tone}" data-real="${action}" data-id="${esc(id)}">${esc(label)}</button>`;
  const badge=(s,tone='orange')=>`<span class="badge ${tone}">${esc(s)}</span>`;
  const empty=(title,description)=>`<div class="empty-state"><div class="empty-icon">${icon('file')}</div><h3>${esc(title)}</h3><p>${esc(description)}</p></div>`;
  const panel=(title,body)=>`<section class="panel"><div class="panel-heading"><h2>${esc(title)}</h2></div><div class="panel-body">${body}</div></section>`;
  const readerText=v=>String(v??'').replace(/\b[a-f0-9]{64}(?::[^:\s，。；、]+:\d+)?/g,'原文').replace(/publication_time|fact_time/g,'披露日期').replace(/allowed_objects/g,'影响对象').replace(/classification_basis/g,'分类依据').replace(/definition_id\/rule_families/g,'官方分类及规则').replace(/source_rows/g,'关联材料').replace(/\bnull\b/g,'未提供');
  const explanationText=v=>readerText(v && typeof v==='object' && typeof v.reason==='string'?v.reason:v);
  const bullets=items=>`<ul>${items.map(v=>`<li>${esc(explanationText(v))}</li>`).join('')}</ul>`;
  const date=v=>v?String(v).replace(/^(\d{4})\/(\d{1,2})\/(\d{1,2})/,(_,y,m,d)=>`${y}-${m.padStart(2,'0')}-${d.padStart(2,'0')}`).replace('T',' ').replace(/\.\d+/, '').replace(/Z$/, ' UTC'):'未提供';
  const localCutoff=v=>new Date(new Date(v).getTime()+8*3600000).toISOString().slice(0,16);
  const sourceDate=m=>m.publication_time?day(m.publication_time):m.available_at?'收录 '+day(m.available_at)+'（披露日期未提供）':'披露日期未提供';
  const materialDate=m=>['day','day_end_proxy'].includes(m.precision)?date(m.publication_time).slice(0,10)+'（仅日期）':date(m.publication_time);
  let run=new URLSearchParams(location.search).get('run')||'may-replay',status,events=[],actions=[],busy=false,generation=0,lastFocus,timer;
  let materialQuery='',materialPage=1,followTab='events',pendingMaterials=[],openedDocument=null;
  let savingNews=false,pollTimer,newsDraft={},upload=null,uploadResult=null,uploadBusy=false,inputPage=1,inputMessage='',uploadError='';
  function restoreInput(){try{newsDraft=JSON.parse(localStorage.getItem('zhiyan-draft:'+run)||'{}');const saved=JSON.parse(sessionStorage.getItem('zhiyan-upload:'+run)||'null');upload=saved?.upload||null;uploadResult=saved?.result||null;}catch{newsDraft={};upload=null;uploadResult=null;}}
  function rememberInput(){try{localStorage.setItem('zhiyan-draft:'+run,JSON.stringify(newsDraft));sessionStorage.setItem('zhiyan-upload:'+run,JSON.stringify({upload,result:uploadResult}));}catch{toast('浏览器无法保存草稿，请保留原始文本或文件。');}}
  let research={items:[],history:[]},currentDossier=null,renderToken=0,researchForm=null,followDetails=new Map(),followDetailsRevision='';
  const value=(kind,target)=>actions.find(a=>a.kind===kind&&a.target===target)?.value;
  const showcase=()=>!!status?.metadata?.showcase_version;
  const eventBy=id=>events.find(e=>e.member_id===id);
  const defaultEvent=()=>events.filter(e=>e.record_kind!=='news').sort((a,b)=>String(updateTime(b)||'').localeCompare(String(updateTime(a)||'')))[0];
  const typeTone=name=>/医药|研发|临床/.test(name)?'purple':/收购|增资|股权|融资/.test(name)?'orange':/产能|建设|项目/.test(name)?'cyan':/政策|监管|规则/.test(name)?'teal':'blue';
  const levelLabel=v=>({高:'高优先级',中:'中等优先级',低:'低优先级'})[v]||'优先级待定';
  function displayedAffected(e){
    const saved=currentDossier?.affected_objects||[];
    if(saved.length||e.record_kind!=='news'||currentDossier?.historical)return saved;
    return (e.annotations||[]).map(a=>({code:a.object_type==='industry'?a.industry_code:a.trading_code,name:a.object_type==='industry'?a.industry_name:a.secu_abbr,direction:({正面:'positive',负面:'negative',中性:'neutral'})[a.event_emotion]||'unknown',reference:true,pathways:['原数据标注的影响对象与方向；尚未作为本次审阅结论。'],level:'待复核'}));
  }
  const eventTitle=e=>{
    const title=original(e).title;
    const shared=e.record_kind==='event'&&title&&events.some(other=>other.member_id!==e.member_id&&other.record_kind==='event'&&original(other).title===title);
    return (shared?e.event_summary?.text||e.event_identity:null)||title||e.event_summary?.text||'未填写新闻标题';
  };
  const original=e=>e.evidence_sources?.original||{};
  const notices=()=>events.flatMap(e=>(e.warning_reasons?.decisions||[]).filter(d=>d.id&&Number.isInteger(d.version)&&d.version>0&&typeof d.active==='boolean').map(d=>({...d,event:e})));
  const stages={extract:'识别事项',mentions:'判断监测范围',retrieval:'查找证据',verification:'比较具体说法',relation_candidates:'查找关联事件',relations:'判断事件关系',impact:'评估影响'};
  const stateText={waiting:'待处理',running:'处理中',ready:'已完成初判',partial:'部分完成',cached:'复用已保存结果',failed:'处理失败',unknown_no_retry:'发送状态未知',not_applicable:'本步不适用'};
  const orderedStages=rows=>[...rows].sort((a,b)=>Object.keys(stages).indexOf(a.stage)-Object.keys(stages).indexOf(b.stage));
  const progress=id=>orderedStages((status.processing||[]).filter(p=>p.source_row===id));
  const route=()=>decodeURIComponent(location.hash.slice(1)||'/radar');
  function toast(text){$('#toast').textContent=text;$('#toast').hidden=false;clearTimeout(timer);timer=setTimeout(()=>$('#toast').hidden=true,3500);}
  function drawer(title,html,material=false){if(!material){openedDocument=null;const u=new URL(location.href);u.searchParams.delete('material');history.replaceState(null,'',u);}lastFocus=document.activeElement;$('#drawer-title').textContent=title;$('#drawer-content').innerHTML=html;if(!$('#drawer').open)$('#drawer').showModal();}
  function close(){if($('#drawer').open)$('#drawer').close();openedDocument=null;const u=new URL(location.href);u.searchParams.delete('material');history.replaceState(null,'',u);lastFocus?.isConnected&&lastFocus.focus();}
  async function api(path,params={},body){
    const response=await fetch('/api/replay/'+path+(body?'':'?'+new URLSearchParams({run,...params})),body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
    const data=await response.json();if(!response.ok){const error=Error(data.error||'本地服务请求失败');error.retryable=!body&&response.status===503;throw error;}return data;
  }
  async function load(){
    const token=++generation;busy=true;
    try{let results;
      for(let attempt=0;attempt<3;attempt++){
        try{results=await Promise.all([api('status',{events_only:1}),api('events',{view:'summary',events_only:1}),api('state'),api('materials'),api('research')]);
          if(results.slice(1).some(x=>x.revision!==results[0].run.revision)){const error=Error('分析结果正在更新，请稍后重新连接');error.retryable=true;throw error;}break;
        }catch(error){if(!error.retryable||attempt===2)throw error;await new Promise(resolve=>setTimeout(resolve,500));if(token!==generation)return;}
      }
      if(token!==generation)return;const [s,e,a,m,w]=results;if(!status)Object.assign(filters,displayPreferences(s));status=s;events=e.items;actions=a.items;pendingMaterials=m.rows;research=w;revealSubmissionResults();await render();const id=new URLSearchParams(location.search).get('material');if(id)await documentView(id);}
    catch(error){if(token!==generation)return;if(status){toast('暂未刷新，保留上次结果：'+error.message);return;}close();status=null;events=[];actions=[];pendingMaterials=[];$('#main').removeAttribute('data-run');$('#main').innerHTML=empty('真实运行读取失败',error.message)+btn('重新连接','reload');$('.footer').textContent='真实运行连接失败，当前没有可用结果';}
    finally{if(token===generation){busy=false;refreshNewsStatus();schedulePoll();}}
  }
  function materialOutcome(id){
    const active=(status.tasks||[]).find(t=>t.source_row===id&&['queued','running'].includes(t.state));
    if(active)return active.state==='queued'?'等待分析':'正在分析';
    const task=[...(status.tasks||[])].reverse().find(t=>t.source_row===id);
    if(task&&['partial','failed','unknown_no_retry','not_sent'].includes(task.state)){const linked=events.filter(e=>e.record_kind!=='news'&&e.evidence_sources?.source_rows?.includes(id));return linked.length?`已入档 · ${linked.length} 个事件`:'材料已保存';}
    const saved=status.material_outcomes?.[id];if(saved)return saved.label;
    const linked=events.filter(e=>e.record_kind!=='news'&&(e.evidence_sources?.source_rows?.includes(id)||(e.evidence_sources?.members||[]).some(m=>m.source_row===id)));
    if(task?.state==='completed')return linked.length?`处理完成 · 已关联 ${linked.length} 个监测事件`:'处理完成 · 未生成监测事件';
    if(task)return task.state;
    if(linked.length)return `已关联 ${linked.length} 个监测事件`;
    return '材料已保存';
  }
  const latestTask=id=>[...(status?.tasks||[])].reverse().find(t=>t.source_row===id);
  function analysisButton(id){const task=latestTask(id);return !status.model_send_enabled?'':task&&['queued','running'].includes(task.state)?'<span class="small muted">正在分析</span>':task&&['failed','partial','unknown_no_retry','not_sent'].includes(task.state)?btn('再次分析','news-retry',task.id):btn('分析','news-process',id);}
  function partialNews(){
    return (status?.tasks||[]).filter(t=>t.show_partial&&t.preview&&latestTask(t.source_row)?.id===t.id&&!events.some(e=>e.record_kind!=='news'&&e.evidence_sources?.source_rows?.includes(t.source_row))).map(t=>`<section class="panel" aria-label="新闻材料"><div class="panel-body"><h3>${esc(t.preview.document.title||'新闻分析')}</h3><p role="status">${['queued','running'].includes(t.state)?'正在分析':'材料已保存'}</p>${t.preview.summaries.length?bullets(t.preview.summaries):'<p>原文已保存，可查看材料。</p>'}<details><summary>已保存原文</summary><p style="white-space:pre-wrap">${esc(t.preview.document.text)}</p></details>${btn('查看原文与处理结果','document',t.source_row)} ${analysisButton(t.source_row)}</div></section>`).join('');
  }
  function taskRows(){return (status?.tasks||[]).map(t=>`<p>${esc(materialOutcome(t.source_row))} · 调用 ${t.model_calls_used??'—'}/${t.model_call_limit??2}${t.model_calls_uncertain?'（含发送状态待核）':''} ${btn('查看原文与处理结果','document',t.source_row)}</p>`).join('');}
  function refreshNewsStatus(){const progressNode=$('#news-progress');if(progressNode)progressNode.innerHTML=importProgress();const previewNode=$('#news-partial');if(previewNode){const markup=partialNews();if(previewNode._renderedMarkup!==markup){previewNode.innerHTML=markup;previewNode._renderedMarkup=markup;}}const inbox=$('#news-inbox');if(inbox){const markup=importedNews();if(inbox._renderedMarkup!==markup){inbox.outerHTML=markup;$('#news-inbox')._renderedMarkup=markup;}}const node=$('#news-task-status');if(node)node.innerHTML=taskRows();const spent=$('#news-budget-spent');if(spent)spent.textContent=Number(status?.budget?.committed_rmb||0).toFixed(6);const remaining=$('#news-remaining-calls');if(remaining)remaining.textContent=status?.budget?.purposes?.[status.budget_purpose]?.remaining_calls??'未提供';}
  function schedulePoll(){
    clearTimeout(pollTimer);
    if(!(status?.tasks||[]).some(t=>['queued','running'].includes(t.state)))return;
    pollTimer=setTimeout(async()=>{try{const s=await api('status',{progress:1});if(s.run.revision!==status.run.revision)await load();else{status={...status,...s};refreshNewsStatus();schedulePoll();}}catch(error){toast(error.message);schedulePoll();}},700);
  }
  function newsForm(){
    const fields=[['title','新闻标题'],['source','来源名称'],['source_url','原文链接（可选）']];
    const eventList=events.filter(e=>e.record_kind!=='news');
    return `<section class="stack" aria-label="添加新闻"><h3>粘贴新闻</h3>${fields.map(([key,label])=>`<label class="stack">${label}<input class="select" data-news-field="${key}" value="${esc(newsDraft[key]||'')}" ${key==='source_url'?'type="url"':'type="text"'}></label>`).join('')}<label class="stack">披露时间（可选，北京时间）<input type="text" class="select" data-news-field="publication_time" aria-label="披露时间" placeholder="直接粘贴，例如 2026/5/28 20:46:00" value="${esc(newsDraft.publication_time||'')}" autocomplete="off"><span class="small muted">支持表格中的日期时间，也可只填日期；未知可留空。</span></label><label class="stack">关联事件线索（可选）<select class="select" data-news-field="related_event_id"><option value="">由后续分析判断</option>${eventList.map(e=>`<option value="${esc(e.member_id)}" ${newsDraft.related_event_id===e.member_id?'selected':''}>${esc(eventTitle(e))}</option>`).join('')}</select></label><label class="stack">新闻正文（必填）<textarea class="select" data-news-field="text" rows="8" style="min-height:160px;resize:vertical" required>${esc(newsDraft.text||'')}</textarea></label><p class="small muted">来源名称和关联线索供核查使用；保存时不会直接合并事件。原文链接不自动抓取。</p><div class="inline">${status.model_send_enabled?btn('导入并分析','news-save-process','','primary')+btn('仅保存','news-save'):btn('保存新闻','news-save','','primary')}</div><p id="news-message" role="status" aria-live="polite">${esc(inputMessage)}</p>${!status.model_send_enabled?'<p class="small muted">当前先保存新闻，自动分析尚未开启。材料会留在本工作区，已有事件和历史保留。</p>':''}</section>`;
  }
  function importedNews(){
    const all=status.input_materials||[],pages=Math.max(1,Math.ceil(all.length/10));inputPage=Math.max(1,Math.min(inputPage,pages));
    return `<section class="space-top" id="news-inbox"><h3>我的导入 <small>${all.length} 篇</small></h3>${all.slice((inputPage-1)*10,inputPage*10).map(m=>{const linked=events.filter(e=>e.record_kind!=='news'&&e.evidence_sources?.source_rows?.includes(m.id));return `<article class="input-record"><strong>${esc(m.title||'未填写标题')}</strong><p class="small muted">${esc(materialOutcome(m.id))} · 收录 ${esc(day(m.received_at))}</p>${btn('查看原文','document',m.id)}${linked.map(e=>`<a class="button compact" href="#/events/${encodeURIComponent(e.member_id)}">查看所属事件</a>`).join('')}${analysisButton(m.id)}</article>`;}).join('')||'<p class="small muted">还没有自行导入的新闻。上方保存后会显示在这里。</p>'}${pages>1?`<div class="pagination">${btn('上一页','input-page',inputPage-1)} ${inputPage} / ${pages} ${btn('下一页','input-page',inputPage+1)}</div>`:''}</section>`;
  }
  function uploadPanel(){
    return `<details class="space-top input-upload" ${upload||uploadError?'open':''}><summary>批量导入 CSV / Excel</summary><p class="small muted">每批最多30篇，文件不超过1MB；Excel仅一个工作表。先预览再导入；${status.model_send_enabled?'导入后分析可用新闻':'当前仅保存，不调用模型'}。</p>${btn('下载导入模板','input-template')}<label class="stack space-top">选择新闻文件<input id="news-file" type="file" accept=".csv,.xlsx" ${uploadBusy?'disabled':''}></label><div id="news-file-message" role="status" aria-live="polite">${esc(uploadError)}</div>${upload?`<p>${esc(upload.filename)} · 可导入 ${upload.valid} 篇 · 需修改 ${upload.invalid} 篇</p><div class="table-container"><table class="data-table input-preview"><thead><tr><th>原文件行号</th><th>新闻标题 / 正文开头</th><th>检查结果</th></tr></thead><tbody>${upload.rows.map(r=>{const result=uploadResult?.rows.find(x=>x.row===r.row);return `<tr><td>${r.row}</td><td>${esc(r.fields.title||r.fields.text?.slice(0,50)||'正文缺失')}<small>${esc(r.fields.publication_time||'日期未提供')}</small></td><td>${esc(result?.reason||r.reason)}</td></tr>`;}).join('')}</tbody></table></div>${uploadResult?`<p role="status">已新增 ${uploadResult.saved} 篇，重复 ${uploadResult.duplicates} 篇，失败 ${uploadResult.failed} 篇；未通过的行仍保留在预览中。</p>`:''}<button class="button primary" data-real="upload-save" ${uploadBusy||!upload.valid?'disabled':''}>${status.model_send_enabled?'导入并分析可用新闻':'保存可导入新闻'}</button> ${btn('清除文件预览','upload-clear')}`:''}</details>`;
  }
  async function previewFile(file){
    if(!file||uploadBusy)return;uploadBusy=true;upload=null;uploadResult=null;uploadError='';rememberInput();
    try{if(file.size>1024*1024)throw Error('文件最大1MB，请拆分后导入。');const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(Error('文件读取失败'));reader.readAsDataURL(file);});upload=await api('news-preview',{}, {run,filename:file.name,data});uploadResult=null;rememberInput();}
    catch(e){uploadError=e.message;toast(e.message);}
    finally{uploadBusy=false;config();}
  }
  async function saveUpload(){
    if(!upload||uploadBusy)return;uploadBusy=true;config();
    try{const rejected=upload.rows.filter(r=>r.status!=='valid').map(r=>({row:r.row,status:'failed',reason:r.reason}));uploadResult=await api('news-batch',{}, {run,revision:status.run.revision,rows:upload.rows.filter(r=>r.status==='valid').map(r=>({row:r.row,fields:r.fields}))});uploadResult.rows.push(...rejected);uploadResult.failed+=rejected.length;
      if(status.model_send_enabled)for(const row of uploadResult.rows.filter(r=>r.material_id)){
        try{const latest=await api('status');rememberSubmission(await api('process',{}, {run,revision:latest.run.revision,material_id:row.material_id}));row.reason='已导入，分析进度见我的导入';}
        catch(error){row.reason='原文已保存，分析未提交：'+error.message;}
      }
      rememberInput();if(status.model_send_enabled)returnToImportRadar();await load();toast('导入结果已保存，可在雷达查看分析进度。');}
    catch(e){toast(e.message);}
    finally{uploadBusy=false;if(!status.model_send_enabled)config();}
  }
  async function saveNews(process){
    if(savingNews)return;savingNews=true;let submitted=false;
    const buttons=[...document.querySelectorAll('[data-real="news-save"],[data-real="news-save-process"]')];buttons.forEach(b=>b.disabled=true);
    inputMessage=process?'正在保存新闻并提交分析，请稍候。':'正在保存新闻，请稍候。';
    if($('#news-message'))$('#news-message').textContent=inputMessage;
    try{
      document.querySelectorAll('[data-news-field]').forEach(el=>newsDraft[el.dataset.newsField]=el.value);rememberInput();
      const {publication_clock,...fields}=newsDraft;
      if(publication_clock&&/^\d{4}-\d{2}-\d{2}$/.test(fields.publication_time||''))fields.publication_time+='T'+publication_clock+':00+08:00';
      const saved=await api('news',{}, {...fields,run,revision:status.run.revision});
      inputMessage=saved.duplicate?'重复材料，已找到先前保存的记录。':'新闻已保存，可在下方“我的导入”查看。';
      newsDraft={};rememberInput();
      if(process){try{rememberSubmission(await api('process',{}, {run,revision:saved.revision,material_id:saved.material_id}));submitted=true;inputMessage+=' 已提交分析。';}catch(e){inputMessage+=' '+e.message;}}
      if(submitted){returnToImportRadar();await load();}else{await load();config();}toast(inputMessage);
    }catch(error){inputMessage=error.message;if($('#news-message'))$('#news-message').textContent=inputMessage;else toast(inputMessage);}
    finally{savingNews=false;buttons.forEach(b=>b.disabled=false);}
  }
  function nav(page){$('#navigation').innerHTML=[['radar','事件雷达','radar'],['events','事件档案','file'],['following','关注追踪','target'],['alerts','预警中心','alert']].map(([id,label,ico])=>`<a href="#/${id}" class="nav-item ${page===id?'active':''}" aria-label="${label}" ${page===id?'aria-current="page"':''}>${icon(ico)}<span class="nav-label">${label}</span></a>`).join('');}
  function heading(title,buttons=''){return `<div class="page-heading ${title==='事件雷达'?'radar-heading':''}"><div><h1>${title}</h1></div><div class="heading-actions">${buttons}</div></div>`;}
  let radarReturn=null;
  const filters={from:'',to:'',related:false,scope:'all',tab:'all',category:'',sector:'',impact:'',object:'',query:'',order:'time',emotion:'',page:1,size:6};
  function displayPreferences(s=status){
    const defaults={order:s?.metadata?.curated_showcase_version?'featured':'time',size:6,scope:'all'};
    try{const saved=JSON.parse(localStorage.getItem('zhiyan-display:'+run)||'{}');return {
      order:['time','oldest',...(s?.metadata?.curated_showcase_version?['featured']:[])].includes(saved.order)?saved.order:defaults.order,
      size:[6,20,50,100].includes(saved.size)?saved.size:defaults.size,
      scope:['all','stock','industry'].includes(saved.scope)?saved.scope:defaults.scope};}catch{return defaults;}
  }
  function settings(){
    const saved=displayPreferences(),options=(items,current)=>items.map(([id,label])=>`<option value="${id}" ${String(current)===String(id)?'selected':''}>${label}</option>`).join('');
    const os=events.flatMap(objects),stocks=new Set(os.filter(o=>o.type==='stock').map(objectKey)),sectors=new Set(os.filter(o=>o.type==='industry').map(objectKey));
    drawer('系统设置',`<h3>浏览偏好</h3><div class="stack space-top"><label class="stack">默认事件排序<select class="select" id="setting-order">${options([...(status.metadata.curated_showcase_version?[['featured','重点案例优先']]:[]),['time','最新分析优先'],['oldest','时间从早到晚']],saved.order)}</select></label><label class="stack">默认监测范围<select class="select" id="setting-scope">${options([['all','全部事件'],['stock','个股事件'],['industry','行业事件']],saved.scope)}</select></label><label class="stack">每页事件数<select class="select" id="setting-size">${options([6,20,50,100].map(n=>[n,n+' 条']),saved.size)}</select></label><div class="inline">${btn('保存设置','settings-save','','primary')}${btn('恢复默认','settings-reset')}</div><p class="small muted">偏好保存在当前浏览器，下次打开自动沿用。</p></div><h3 class="space-top">当前工作区</h3><p>${esc(status.run.title)}</p><div class="inline">${badge(events.filter(e=>e.record_kind==='event').length+' 个事件','blue')}${badge(stocks.size+' 家公司','blue')}${badge(sectors.size+' 个行业','green')}</div><h3 class="space-top">工作台入口</h3><div class="inline">${btn('新闻与资料','config')}${btn('关注追踪','settings-following')}${btn('预警中心','open-alerts')}</div><p class="small muted">关注、已读、待办和预警处置自动保存。浏览和调整设置不调用模型。</p>`);
  }
  let followQuery='',followFilter='all',selectedObject='',objectScope='all',selectedImpact='';
  const definitions=e=>{
    const ds=(Array.isArray(e.monitoring_scope?.matches)?e.monitoring_scope.matches:Array.isArray(e.event_subjects?.standard_entity)?e.event_subjects.standard_entity:[]).filter(m=>m.decision==='matched'&&m.definition).map(m=>m.definition);
    for(const o of e.monitoring_scope?.business_objects||[]){const old=ds.find(d=>d.target_name===o.target_name&&(d.target_type==='industry')===(o.target_type==='industry'));if(old){if(o.object_id)old.object_id=o.object_id;}else ds.push({...o,event_name:o.business_name});}
    return [...new Map(ds.map(d=>[d.definition_id||d.object_id||d.target_type+':'+(d.target_code||d.target_name),d])).values()];
  };
  const objects=e=>[...new Map(definitions(e).map(d=>[d.object_id||d.target_type+':'+d.target_code,{id:d.object_id||(d.target_type==='industry'?'industry:':'stock:')+d.target_code,name:d.target_name,code:d.target_code,type:d.target_type==='industry'?'industry':'stock'}])).values()];
  const objectKey=o=>o.code?o.type+'|'+o.code:o.id;
  const emotionDefinition=(defs,x)=>defs.find(d=>x.object_id?d.object_id===x.object_id:x.definition_id!=null&&d.definition_id===x.definition_id);
  const decisions=e=>e.warning_reasons?.decisions||[];
  const effects=e=>decisions(e).flatMap(d=>(d.effects||[]).map(x=>({...x,decision:d})));
  const direction=v=>({positive:'偏正面',negative:'偏负面',neutral:'中性',mixed:'正负并存',uncertain:'不确定',unknown:'未评定'})[v]||v||'未评定';
  const tone=v=>/反驳|冲突|负面/.test(v)?'red':/不足|待|不确定|部分/.test(v)?'orange':/支持|正面/.test(v)?'green':'gray';
  const priority=e=>decisions(e).map(d=>d.level).filter(x=>['高','中','低'].includes(x)).sort((a,b)=>['高','中','低'].indexOf(a)-['高','中','低'].indexOf(b))[0]||'未评定';
  const priorityBadge=e=>badge(priority(e),({高:'red',中:'orange',低:'blue'})[priority(e)]||'gray');
  const updated=e=>(e.event_timeline?.items||[]).some(t=>['有据进展或更正（日期见引用）','实际进展','澄清更正'].includes(t.kind))||(e.credibility?.claims||[]).some(c=>(e.judgment_history||[]).some(h=>(h.claims||[]).some(old=>old.claim===c.claim&&old.verdict&&c.verdict&&old.verdict!==c.verdict)));
  const pending=e=>e.credibility?.needs_followup??(!e.credibility?.claims?.length||e.credibility.claims.some(c=>c.verdict!=='有材料支持'));
  const day=v=>{if(!v)return '';const s=String(v);return /(?:Z|[+-]\d{2}:\d{2})$/.test(s)&&!Number.isNaN(Date.parse(s))?new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(s)):date(s).slice(0,10);};
  const updateTime=e=>e.record_kind==='news'?(original(e).publication_time||e.available_at):e.updated_at||(e.event_timeline?.items||[]).filter(t=>t.kind==='系统形成判断').sort((a,b)=>Date.parse(b.time)-Date.parse(a.time))[0]?.time||e.available_at;
  const disclosureDay=e=>day(e.record_kind==='news'?original(e).publication_time:e.update_time_basis==='received_date_unknown'?null:e.updated_at);
  const matchesDisclosureDate=(e,from,to)=>{const d=disclosureDay(e);return !from&&!to||!!d&&(!from||d>=from)&&(!to||d<=to);};
  const changeText=e=>e.record_kind==='news'?'查看事件与影响分析':e.change_summary|| (updated(e)?'主张结论或进展有变化 · 查看依据':(e.judgment_history||[]).length?'已有历史判断 · 未确认实质变化':e.event_version?'首次保存判断；尚无可比变化':'尚未形成事件判断');
  const objectChips=e=>(objects(e).map(o=>badge(o.name,o.type==='industry'?'green':'blue')).join(' ')||badge('对象待确认','gray'))+' '+(e.emotions||[]).map(x=>badge((emotionDefinition(definitions(e),x)?.target_name||'对象')+'：'+x.label,tone(x.label))).join(' ');
  const impactBadges=e=>e.record_kind==='news'?`<a class="text-button" href="#/events/${encodeURIComponent(e.member_id)}">${e.analysis_available===false?'分析未提供':'查看影响分析'}</a>`:[...new Set(effects(e).map(x=>direction(x.original?.direction)))].map(v=>badge(v+' · 初判',tone(v))).join(' ')||badge('未评定','gray');
  function selectedEvents(tab=filters.tab){return events.filter(e=>
    (run!=='training-showcase'||e.record_kind==='event')&&
    (filters.scope==='all'||objects(e).some(o=>o.type===filters.scope))&&
    matchesDisclosureDate(e,filters.from,filters.to)&&
    (!filters.related||(e.material_count||0)>1)&&
    (!filters.object||objects(e).some(o=>objectKey(o)===filters.object))&&
    (!filters.category||definitions(e).some(d=>d.event_name===filters.category))&&
    (!filters.emotion||(filters.emotion==='未标注'?!degreeLabels(e).some(x=>x.label&&x.label!=='待判断'):degreeLabels(e).some(x=>x.label===filters.emotion)))&&
    (!filters.sector||(filters.sector==='行业未提供'?!(e.industries?.length||objects(e).some(o=>o.type==='industry')):(e.industries||[]).includes(filters.sector)||objects(e).some(o=>o.type==='industry'&&o.name===filters.sector)))&&
    (!filters.impact||(filters.impact==='未评定'?!effects(e).length:effects(e).some(x=>direction(x.original?.direction)===filters.impact)))&&
    (!filters.query||[eventTitle(e),e.event_summary?.text,...objects(e).flatMap(o=>[o.name,o.code]),...definitions(e).map(d=>d.event_name),...(e.industries||[])].join(' ').toLowerCase().includes(filters.query.toLowerCase()))&&
    (tab==='all'||tab==='new'&&!value('read',e.member_id)&&!updated(e)||tab==='updated'&&updated(e)||tab==='pending'&&pending(e)||tab==='followed'&&value('follow',e.member_id))
  );}
  function scopeSwitch(current,action){return `<div class="scope-switch" role="group" aria-label="监测对象范围">${[['all','全部'],['stock','个股'],['industry','行业']].map(([id,label])=>`<button data-real="${action}" data-id="${id}" aria-pressed="${current===id}" class="${current===id?'active':''}">${label}</button>`).join('')}</div>`;}
  function selectFilter(key,label,options){return `<select class="select" data-real-filter="${key}" aria-label="${label}"><option value="">${label}</option>${options.map(v=>`<option value="${esc(v)}" ${filters[key]===v?'selected':''}>${esc(v)}</option>`).join('')}</select>`;}
  function metrics(){return `<section class="metrics radar-metrics" aria-label="事件动态">${[
    ['new','新增事件','red'],['updated','最新进展','blue'],['pending','待跟进','orange'],['followed','已关注','purple']
  ].map(([id,label,t])=>`<button class="metric ${t} ${filters.tab===id?'selected':''}" data-real="radar-tab" data-id="${id}" aria-pressed="${filters.tab===id}"><span><span class="metric-value">${selectedEvents(id).length}</span><span class="metric-label">${label}</span></span><span class="radar-pillars" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i></span></button>`).join('')}</section>`;}
  const scoreMaximum=c=>c.score_max||(c.score_rule==='credibility-20261004-v3-confirmed-rules'?95:100);
  const scoreText=c=>c.score==null?(c.score_label||'待评分'):c.score+'分 · '+(c.score_label||'历史评分');
  const scoreBadge=e=>`<button class="button compact score-badge" data-level="${esc(e.credibility?.score_label||'待评级')}" data-real="score-info" data-id="${esc(e.member_id)}" title="${esc(e.credibility?.short_reason||e.credibility?.score_scope||'查看评分依据')}">${esc(e.credibility?.score_label||'待评级')}</button>`;
  function scoreBreakdown(c){
    return `${c.display?`<p class="score-calculation">${esc(c.display.calculation)} = ${esc(c.raw_score)}${c.raw_score!==c.score?' → 最终 '+esc(c.score)+'分':'分'}</p>${c.display.adjustment?`<p>${esc(c.display.adjustment)}</p>`:''}`:''}${(c.score_items||[]).map(x=>`<article class="material-row"><strong>${esc(x.name)} ${x.points>=0?'+':''}${x.points}</strong><p>${esc(x.reason)}${x.reference&&/^https?:\/\//.test(x.reference)?` <a href="${esc(x.reference)}" target="_blank" rel="noopener noreferrer">来源核对依据</a>`:''}</p>${(x.quotes||[]).map(q=>`<blockquote>${esc(typeof q==='string'?q:q.quote)}${q.url&&/^https?:\/\//.test(q.url)?` <a href="${esc(q.url)}" target="_blank" rel="noopener noreferrer">查看依据</a>`:''}</blockquote>`).join('')}</article>`).join('')}${bullets(c.score_limits||[])}`;
  }

  const followStar=e=>`<button class="star-button ${value('follow',e.member_id)?'active':''}" data-real="follow" data-id="${esc(e.member_id)}" aria-label="${value('follow',e.member_id)?'取消关注':'关注'}" aria-pressed="${!!value('follow',e.member_id)}">${value('follow',e.member_id)?'★':'☆'}</button>`;
  function degreeLabels(e){
    const defs=definitions(e);
    const judged=e.record_kind==='event'&&!e.emotions?.length?effects(e).map(x=>{const d=defs.find(d=>d.target_name===x.original?.target?.name);return {object_id:d?.object_id,definition_id:d?.definition_id,label:({positive:'正面',negative:'负面',neutral:'中性',mixed:'正负并存'})[x.original?.direction]||'待判断'};}):(e.emotions||[]);
    const labels=[...new Map(judged.map(x=>{const d=emotionDefinition(defs,x);return [(d?.object_id||d?.target_type+'|'+d?.target_code)+'|'+x.label,{name:d?.target_name,label:x.label||'未标注'}];})).values()];
    return labels;
  }
  function degreeBadges(e){
    const labels=degreeLabels(e),multiple=objects(e).length>1;
    return labels.map(x=>`<div class="radar-degree">${multiple?`<small>${esc(x.name||'对象未明确')}</small>`:''}${badge(x.label,tone(x.label))}</div>`).join('')||badge('待判断','gray');
  }
  function rows(list){
    if(!list.length)return empty('没有符合条件的事件','可清除筛选或调整日期范围。');
    return `<div class="table-container"><table class="data-table radar-table" aria-label="事件雷达列表"><colgroup><col style="width:11%"><col style="width:31%"><col style="width:15%"><col style="width:10%"><col style="width:10%"><col style="width:12%"><col style="width:11%"></colgroup><thead><tr><th>名称 / 代码</th><th>事件标题</th><th>事件类型</th><th>可信度</th><th>影响方向</th><th>更新时间</th><th>操作</th></tr></thead><tbody>${list.map(e=>`<tr data-real-event="${esc(e.member_id)}" class="${highlightedEvents.has(e.member_id)?'selected':''}"><td class="radar-codes">${objects(e).map(o=>`<div>${(o.code||o.id)?`<button class="text-button radar-code ${o.type==='industry'?'industry':'company'}" data-real="radar-object" data-id="${esc(objectKey(o))}" aria-label="查看${esc(o.name)}的事件" title="查看${esc(o.name)}的事件">${esc(o.name||'名称未提供')}</button>`:`<span class="radar-code ${o.type==='industry'?'industry':'company'}">${esc(o.name||'名称未提供')}</span>`}<small class="radar-object-name">${esc(o.code||'上市身份待核')}</small></div>`).join('')||'代码未提供'}</td><td><a class="event-title" href="#/events/${encodeURIComponent(e.member_id)}">${esc(eventTitle(e))}</a><div class="event-code">${e.record_kind==='event'?btn(`${e.material_count??e.evidence_sources?.source_rows?.length??1}篇材料 · ${e.progress_count??e.event_version??1}个进展`,'event-materials',e.member_id,'text-button'):e.review_status?esc(e.review_status):''}</div></td><td class="radar-event-types">${[...new Set(definitions(e).map(d=>d.event_name).filter(Boolean))].map(name=>`<div>${esc(name)}</div>`).join('')||'事件类型未提供'}${e.event_identity?`<small class="event-code">${esc(e.event_identity)}</small>`:''}</td><td>${scoreBadge(e)}</td><td>${degreeBadges(e)}</td><td class="time-cell">${esc(date(updateTime(e)).slice(0,10))}<br>${esc(date(updateTime(e)).slice(11,16))}<span class="event-code">${e.record_kind==='news'?'披露时间':e.update_time_basis==='received_date_unknown'?'收录时间 · 披露日期未提供':e.updated_at?'进展披露时间':'材料可用时间'}${/[Z]|[+]00:00$/.test(updateTime(e)||'')?' · UTC':''}</span></td><td><div class="row-actions wrap"><a class="button compact" href="#/events/${encodeURIComponent(e.member_id)}">查看</a>${followStar(e)}</div></td></tr>`).join('')}</tbody></table></div>`;
  }
  function returnToImportRadar(){
    Object.assign(filters,{from:'',to:'',related:false,scope:'all',tab:'all',category:'',sector:'',impact:'',emotion:'',object:'',query:'',page:1,order:'time'});
    radarReturn=null;$('#global-search').value='';close();location.hash='/radar';
  }
  function analysisOrder(){
    const times=new Map();
    for(const t of status.tasks||[])for(const p of t.publications||[]){
      if(!['created','progress'].includes(p.change_kind))continue;
      if(!times.has(p.event_id)||times.get(p.event_id)<p.published_at)times.set(p.event_id,p.published_at);
    }
    return times;
  }
  function importProgress(){
    const active=(status.tasks||[]).filter(t=>['queued','running'].includes(t.state));
    return active.length?`<span class="small muted" role="status" aria-live="polite">正在分析${active.length>1?' · '+active.length+' 篇':''}</span>`:'';
  }
  const submissions=new Map();
  let highlightedEvents=new Set();
  try{for(const item of JSON.parse(sessionStorage.getItem('zhiyan-submissions:'+run)||'[]'))submissions.set(item.id,item);}catch{}
  function rememberSubmission(reply){
    if(!reply?.task)return;
    submissions.set(reply.task.id,{id:reply.task.id,duplicate:!!reply.duplicate});
    sessionStorage.setItem('zhiyan-submissions:'+run,JSON.stringify([...submissions.values()]));
  }
  function revealSubmissionResults(){
    for(const [id,submission] of submissions){
      const task=(status.tasks||[]).find(t=>t.id===id);
      if(!task||['queued','running'].includes(task.state))continue;
      const ids=(task.publications||[]).map(p=>p.event_id).filter(id=>eventBy(id));
      if(ids.length){
        highlightedEvents=new Set(ids);
        if(location.hash==='#/radar'){
          const list=orderedEvents(selectedEvents());
          const index=list.findIndex(e=>highlightedEvents.has(e.member_id));if(index>=0)filters.page=Math.floor(index/filters.size)+1;
        }
        toast(submission.duplicate?'已定位原有事件':task.publications.some(p=>p.change_kind==='created')?'事件已加入雷达':'已更新原有事件');
      }else toast('材料已保存，可查看原文与处理记录');
      submissions.delete(id);
    }
    sessionStorage.setItem('zhiyan-submissions:'+run,JSON.stringify([...submissions.values()]));
  }
  function orderedEvents(list){
    const analyzed=analysisOrder();
    return list.sort((a,b)=>{
      if(filters.order==='time'||filters.order==='featured'){
        const recent=String(analyzed.get(b.member_id)||'').localeCompare(String(analyzed.get(a.member_id)||''));
        if(recent)return recent;
      }
      if(filters.order==='featured'){
        const rank=(a.showcase_rank??1000)-(b.showcase_rank??1000);if(rank)return rank;
      }
      return (filters.order==='oldest'?-1:1)*String(updateTime(b)).localeCompare(String(updateTime(a)));
    });
  }
  function radar(){
    const list=orderedEvents(selectedEvents());
    const selected=events.flatMap(objects).find(o=>objectKey(o)===filters.object);
    const pages=Math.max(1,Math.ceil(list.length/filters.size));filters.page=Math.min(pages,Math.max(1,filters.page));
    const table=rows(list.slice((filters.page-1)*filters.size,filters.page*filters.size));
    const materialsPanel=showcase()?'':panel('新闻收件箱',`<p class="small muted">已保存 ${status.readable_rows} 篇新闻。新闻保存成功后即保留在这里；只有识别出合格监测事项才会进入上方事件列表。处理结束不代表一定生成事件。</p><div class="table-container"><table class="data-table"><thead><tr><th>新闻标题</th><th>发布时间</th><th>处理结果</th><th>操作</th></tr></thead><tbody>${pendingMaterials.map(m=>`<tr data-news-row="${esc(m.id)}"><td><button class="text-button" data-real="document" data-id="${esc(m.id)}">${esc(m.title||'未填写标题')}</button></td><td>${esc(materialDate(m))}</td><td>${badge(materialOutcome(m.id),'gray')}</td><td>${btn('查看原文与结果','document',m.id)}</td></tr>`).join('')}</tbody></table></div>${!pendingMaterials.length?'<p>尚未添加新闻。</p>':''}${btn('浏览全部新闻','materials')}`);

    return heading('事件雷达',scopeSwitch(filters.scope,'radar-scope'))+metrics()+(status.case_session?`<p>已加入 ${status.case_session.step} / ${status.case_session.total} 份材料 ${btn('加入下一份材料','case-next')}${btn('重新回放','case-reset')}</p>`:'')+
      `<div class="tabbar">${(showcase()?[['all','全部档案'],['new','新增'],['updated','实质更新'],['pending','需跟进'],['followed','持续关注']]:[['all','全部事件'],['new','新增事件'],['updated','实质更新'],['pending','关键待核'],['followed','我的关注']]).map(([id,label])=>`<button class="tab ${filters.tab===id?'active':''}" data-real="radar-tab" data-id="${id}">${label} (${selectedEvents(id).length})</button>`).join('')}<span style="margin-left:auto"><span id="news-progress">${importProgress()}</span> ${btn('导入新闻','config')}</span></div>`+
      `<div class="filterbar">${filters.object?btn('← 返回事件列表','radar-return'): ''}<span class="scope-label">${esc(selected?selected.name+(selected.code?' · '+selected.code:''):filters.query?'搜索：'+filters.query:'全部事件')}</span><div class="filters">${selectFilter('sector','关联行业',[...new Set(events.flatMap(e=>[...(e.industries||[]),...objects(e).filter(o=>o.type==='industry').map(o=>o.name)])),'行业未提供'])}${selectFilter('category','全部事件类型',[...new Set(events.flatMap(e=>definitions(e).map(d=>d.event_name)))])}${selectFilter('emotion','全部方向',['正面','中性','负面','未标注'])}<select class="select" data-real-filter="order" aria-label="事件排序">${status.metadata.curated_showcase_version?`<option value="featured" ${filters.order==='featured'?'selected':''}>重点案例优先</option>`:''}<option value="oldest" ${filters.order==='oldest'?'selected':''}>时间从早到晚</option><option value="time" ${filters.order==='time'?'selected':''}>最新分析优先</option></select>${btn('清除筛选','clear-radar')}</div></div><div class="date-filters" aria-label="按披露日期筛选"><label>开始日期 <input type="date" class="select" aria-label="开始日期" data-real-filter="from" value="${esc(filters.from)}" ${filters.to?`max="${esc(filters.to)}"`:''}></label><label>结束日期 <input type="date" class="select" aria-label="结束日期" data-real-filter="to" value="${esc(filters.to)}" ${filters.from?`min="${esc(filters.from)}"`:''}></label><button class="button ${filters.related?'primary':''}" data-real="related-only" aria-pressed="${filters.related}">多篇关联</button>${filters.from&&filters.to&&filters.from>filters.to?'<span class="small red-text">开始日期不能晚于结束日期</span>':''}</div>`+`<div id="news-partial">${partialNews()}</div>`+table+
      `<div class="pagination">共 ${list.length} 条 ${btn('上一页','radar-page',String(filters.page-1))}<span>${filters.page} / ${pages}</span>${btn('下一页','radar-page',String(filters.page+1))}<select class="select" data-real-filter="size" aria-label="每页事件数">${[6,20,50,100].map(n=>`<option ${filters.size===n?'selected':''}>${n}</option>`).join('')}</select></div>`+materialsPanel;
  }
  const sourceKind=s=>({user_input:'手工新闻',public_primary:'公开原始披露',train_development:'获准开发材料',train_regression:'固定识别回归',train:'官方训练材料',test:'受保护测试材料',showcase:'新闻材料'})[s]||s||'来源未提供';
  const sourceCheck=s=>({not_checked:'用户填写，尚未核对',identity_split_hash_checked:'已核对材料标识，转载渠道尚未核实',original_page_checked:'已核对公开原页',original_pdf_checked:'已核对披露 PDF',original_frozen_30:'已有案例材料',stored_original_reviewed:'已核对所存原文'})[s]||s||'未核对';
  function judgmentClaims(claims){
    return (claims||[]).map(c=>`<article class="material-row"><strong>${esc(c.claim)}</strong> ${c.verdict?badge(c.verdict):''}${c.score!=null?`<p>${esc(scoreText(c))} · ${esc(c.score_status||'当时评分')}<br>${esc(c.short_reason||'')}</p>`:''}<p class="small muted">${esc(c.score_role||'判断依据')} ${c.assessment_version?'· 判断版本 '+esc(c.assessment_version.slice(0,12)):''}</p>${c.assessment_cutoff?`<p class="small muted">使用材料截至 ${esc(date(c.assessment_cutoff))}</p>`:''}${(c.score_items||[]).filter(x=>x.name==='适用证据'&&x.points>=10).map(x=>`<p><strong>本条评分依据</strong>：${esc(x.reason)}</p>${(x.quotes||[]).map(q=>`<blockquote>${esc(q.quote)}${q.url&&/^https?:\/\//.test(q.url)?` <a href="${esc(q.url)}" target="_blank" rel="noopener noreferrer">查看依据</a>`:''}</blockquote>`).join('')}`).join('')}${(c.valid_assessments||[]).map(a=>`<p>${esc({supports:'材料支持',refutes:'材料反驳',related:'相关，但不能单独支持或反驳',insufficient:'证据不足'}[a.proposal?.judgment]||a.proposal?.judgment)}：${esc(a.proposal?.reason)}</p><p class="small muted">来源关系：${esc({own_document:'自身材料',same_source_reprint:'同源转述',same_source_relay:'相同来源或相同正文，不增加独立证据',no_material:'无可引用材料',dependency_unknown:'依赖关系未知'}[a.source_relation]||a.source_relation||'未知')}；来源核对：${esc(sourceCheck(a.source_check?.status))}；独立核实未建立。</p>${a.proof?`<blockquote>${esc(a.proof.quote)}</blockquote>`:''}${a.source_row?btn('定位证据原文','proof',JSON.stringify({row:a.source_row,proof:a.proof})):''}`).join('')}</article>`).join('');
  }
  function judgmentHistory(e){
    const rows=e.judgment_history||[];
    if(!rows.length)return '';
    return `<div class="space-top">${panel('之前怎么判断',`${rows.map(r=>`<details class="material-row"><summary>${r.assessment_cutoff?esc(date(r.assessment_cutoff)):'当时未记录判断时点'} · ${esc(r.status)}</summary><p>${esc(r.summary)}</p>${judgmentClaims(r.claims)}${btn('查看当时处理的原文','document',r.source_row)}</details>`).join('')}`)}</div>`;
  }
  function claimMatrix(e){
    const claims=e.credibility?.claims||[],counts=claims.reduce((r,c)=>{const key=c.score_status||c.verdict||'尚未核验';r[key]=(r[key]||0)+1;return r;},{});
    return `<section class="panel space-top claims-panel" id="real-claims"><div class="panel-heading"><h2>${icon('shield')}关键主张与证据 <small>${claims.length} 项</small></h2></div><div class="claim-summary">${Object.entries(counts).map(([label,n])=>badge(label+' '+n,tone(label))).join('')}</div><div class="table-container"><table class="data-table matrix-table"><colgroup><col style="width:6%"><col style="width:41%"><col style="width:25%"><col style="width:12%"><col style="width:16%"></colgroup><thead><tr><th>#</th><th>主张及范围</th><th>核查结论 / 可信度</th><th>材料</th><th>操作</th></tr></thead><tbody>${claims.map((c,i)=>`<tr><td><span class="claim-number">${i+1}</span></td><td>${esc(c.claim)}<small class="event-code">${esc(c.score_role||'该版本主张')}</small></td><td>${badge(c.score_status||c.verdict||'待核查',tone(c.verdict||'待核'))}<small class="event-code">${esc(scoreText(c))}</small></td><td><span class="evidence-count">${new Set([...(c.valid_assessments||[]).map(a=>a.source_row),...(c.score_input?.records||[]).filter(r=>r.applicable&&r.time_eligible).map(r=>r.proof?.source_row||r.proof?.document_sha||r.material_id)].filter(Boolean)).size}</span> 份</td><td>${btn('查看','claim',e.member_id+'|'+i,'compact')}</td></tr>`).join('')}</tbody></table></div>${e.credibility?.history_pending_count?`<p class="matrix-note">当前评分对应最新阶段；另有 ${e.credibility.history_pending_count} 条历史阶段主张保留待核。</p>`:''}${!claims.length?empty('本篇主张尚待核查','已保留原文和来源，补充逐条证据后在这里显示核查结论。'):''}</section>`;
  }
  function objectAssessment(e){const os=objects(e);if(!os.some(o=>o.name===selectedImpact))selectedImpact=os[0]?.name||'';const ds=decisions(e).filter(d=>d.target===selectedImpact||(d.targets||[]).includes(selectedImpact));return `<section class="panel space-top" id="real-impact"><div class="assessment-tabs" role="group" aria-label="选择监测对象">${os.map(o=>`<button class="assessment-tab ${o.name===selectedImpact?'active':''}" data-real="impact-object" data-id="${esc(o.name)}">${badge(o.type==='industry'?'行业':o.type==='context'?'关联事项':'个股',o.type==='industry'?'green':'blue')}<strong>${esc(o.name)}</strong><small>${esc(o.code)}</small></button>`).join('')}</div><div class="panel-body"><h2>对 ${esc(selectedImpact||'待确认对象')} 的潜在影响</h2>${ds.map(d=>`<div class="material-row"><p>${d.rule_score?`规则分：${impactPoints(d)}；`:''}优先级：${badge(d.level==='待定'?'未评定':d.level||'未评定','gray')} · 模型建议：${esc(d.proposed_level||'未提供')}（未作为正式等级）</p>${(d.effects||[]).map(x=>`<p>${badge(direction(x.original?.direction),tone(direction(x.original?.direction)))}</p><dl class="detail-list"><dt>判断依据</dt><dd>${esc(explanationText(x.original?.pathway))}</dd><dt>适用条件</dt><dd>${esc((x.original?.conditions||[]).join('；')||'未提供')}</dd><dt>尚缺信息</dt><dd>${esc((x.original?.gaps||[]).join('；')||'未提供')}</dd><dt>时间范围</dt><dd>${esc(({unknown:'未明确',short_term:'短期',medium_term:'中期',long_term:'长期'})[x.original?.horizon]||x.original?.horizon||'未明确')}</dd></dl>`).join('')}${bullets(d.reasons||[])}</div>`).join('')||empty('影响尚未评定','当前对象没有通过检查的影响判断，不据此推断中性。')}</div></section>`;}
  const handlingLabels={pending:'待处理',in_progress:'处理中',waiting_material:'等待材料',closed:'已关闭'};
  const handlingTone={pending:'red',in_progress:'orange',waiting_material:'blue',closed:'green'};
  const levelTone=v=>({高:'red',中:'orange',低:'blue'})[v]||'gray';
  const relationLabels={same_event:'同事件 · 同阶段',same_project_new_progress:'同事项 · 新进展',different_event:'不同事项',insufficient:'关联待核',same_source_relay:'同源转述',same_source_reprint:'同源转载',dependency_unknown:'来源关系待核',own_document:'本篇材料','同一事项的后续材料':'同事项 · 后续报道'};
  function handlingState(d){const r=research.items.find(x=>x.kind==='handling'&&x.id===d.id&&x.notice_version===d.version);return r?.state||(value('alert_closed',d.id)?'closed':'pending');}
  function sourceGraph(graph=currentDossier?.sources){
    if(!graph?.nodes.length)return '<p class="muted">该版本尚无可见材料。</p>';
    const nodes=[...graph.nodes].sort((a,b)=>String(a.publication_time||a.available_at).localeCompare(String(b.publication_time||b.available_at))),names=new Map(nodes.map(m=>[m.source_row,m]));
    const saved=(graph.edges||[]).filter(r=>r.material_a!==r.material_b&&names.has(r.material_a)&&names.has(r.material_b)),pairs=new Map(),history=new Map();
    for(const r of saved){const pair=[r.material_a,r.material_b].sort().join('|');history.set(pair+'|'+r.relation+'|'+(r.operation||''),r);if(r.relation!=='different_event'&&!/pending/.test(r.decision_status||''))pairs.set(pair,r);}
    const relations=[...pairs.values()],groups=new Map();for(const r of relations){const label=r.operation_label||relationLabels[r.relation]||r.relation;groups.set(label,[...(groups.get(label)||[]),r]);}
    const shown=nodes.slice(0,6),height=Math.max(176,Math.ceil(shown.length/2)*96),points=shown.map((m,i)=>({m,x:i%2?82:18,y:(Math.floor(i/2)+1)*height/(Math.ceil((shown.length-i%2)/2)+1)}));
    const color=r=>/reprint|relay/.test(r.relation)?'orange':/insufficient|unknown/.test(r.relation)?'gray':r.relation==='different_event'?'red':'purple';
    return `<div class="relation-summary"><span><strong>${nodes.length}</strong> 篇报道 <span class="relation-separator">/</span> <strong>${relations.length}</strong> 组关联</span>${currentDossier?btn('全部原文','section','real-materials','text-button'):''}</div><div class="relation-canvas" style="height:${height}px" role="group" aria-label="报道归属图，中心为事件，报道可打开原文"><svg class="relation-wires" viewBox="0 0 100 ${height}" preserveAspectRatio="none" aria-hidden="true">${points.map(p=>`<path d="M ${p.x<50?31:69} ${p.y} C ${p.x<50?41:59} ${p.y}, ${p.x<50?38:62} ${height/2}, 50 ${height/2}"/>`).join('')}</svg><div class="graph-event" title="${esc(eventTitle(currentDossier?.event||{}))}">${icon('network')}<strong>当前事件</strong><span>${nodes.length} 篇关联报道</span></div>${points.map((p,i)=>`<button class="graph-report ${i%2?'followup':'initial'}" style="left:${p.x}%;top:${p.y}px" data-real="document" data-id="${esc(p.m.source_row)}" title="${esc(p.m.title)}"><span class="graph-publisher"><i></i>${esc(p.m.source||'来源未提供')}</span><strong>${esc(p.m.title||'标题未提供')}</strong><time>${esc(sourceDate(p.m))} ↗</time></button>`).join('')}${nodes.length===1?'<p class="graph-single">目前收录一篇报道<br>后续材料加入后在此串联</p>':''}</div><div class="relation-decisions">${[...groups].map(([label,rows])=>`<button class="graph-link ${color(rows[0])}" data-real="source-relations" data-id="${esc(JSON.stringify(rows))}">${icon('network')}${esc(label)} · ${rows.length} 组 <span>查看依据 →</span></button>`).join('')||'<p class="small muted">后续报道加入后显示关联依据。</p>'}</div><details class="report-relations"><summary>来源与关联说明${nodes.length>6?' · 另有 '+(nodes.length-6)+' 篇材料':''}</summary><p class="small muted">细线表示报道归入本事件。同一对报道只展示一次关联；转载不增加独立证据，未确认传播方向的关系不绘制箭头。</p><div class="report-list">${nodes.map(m=>`<button class="report-card" data-real="document" data-id="${esc(m.source_row)}"><strong>${esc(m.title)}</strong><small>${esc(m.source||'来源未提供')} · ${esc(sourceDate(m))}</small></button>`).join('')}</div>${history.size>relations.length?`<details class="space-top"><summary>查看其他比较记录</summary>${[...history.values()].map(r=>`<p class="small">${esc(names.get(r.material_a).title)} / ${esc(names.get(r.material_b).title)} ${btn(r.operation_label||relationLabels[r.relation]||r.relation,'source-relation',JSON.stringify(r),'text-button')}</p>`).join('')}</details>`:''}</details>`;
  }

  function snapshotVersions(e){return panel('历史版本与完整回放',`<p class="small muted">事件版本描述业务进展，评估版本描述评分变化，材料版本区分完整保存快照。保存时间是系统操作时间，不是新闻发生时间。</p>${(currentDossier?.navigation?.versions||currentDossier?.versions||[]).map(v=>`<article class="material-row"><a class="button compact ${v.material_version===currentDossier.key.material_version?'primary':''}" href="#/events/${encodeURIComponent(e.member_id)}?material_version=${v.material_version}">第${v.material_version}次保存 · 业务第${v.event_version}阶段 / 第${v.assessment_version||1}次评估</a> <span class="small muted">${esc(date(v.saved_at))}${v.current?' · 当前有效保存版':' · 历史存档，可能已纠正'}</span> ${v.material_version!==currentDossier.key.material_version?btn('与所看版本比较','snapshot-diff',String(v.material_version)):''}</article>`).join('')||'<p>该事件尚无完整保存版本。</p>'}`);}
  function reviewPanel(e){
    const obj=currentDossier?.objects.find(o=>o.name===selectedImpact),all=research.items.filter(r=>r.kind==='review'&&r.event_id===e.member_id&&r.object_id===obj?.id),same=all.filter(r=>r.snapshot_key.body_sha256===currentDossier.key.body_sha256);
    return panel('人工方向复核',`<p class="small muted">使用者填写的复核记录；不认证专家身份，也不代替独立验收。绑定当前对象与完整版本，保留模型原判。</p>${same.map(r=>`<article class="material-row"><strong>${esc(r.operator)} · ${r.status==='confirmed'?'确认原方向':'记录不同意见'} · ${badge(direction(r.result),tone(direction(r.result)))}</strong><p>${esc(r.reason)}</p><small>记录时间 ${esc(date(r.operated_at))}</small>${btn('记录详情','research-history',r.id)}</article>`).join('')||'<p>所选版本尚无人工复核。</p>'}${all.length>same.length?`<p class="small muted">另有 ${all.length-same.length} 条其他版本复核，未套用到当前版本。</p>`:''}${!currentDossier?.historical&&obj&&effects(e).some(x=>x.original?.target?.name===obj.name)?btn('记录人工复核','review-open',e.member_id):`<p class="small muted">${currentDossier?.historical?'历史页只读；新增复核请返回最新档案。':'该对象尚无合格方向判断可供复核。'}</p>`}`);
  }
  function taskCards(list){return list.map(r=>`<article class="task-card ${r.state==='completed'?'completed':''}"><div class="task-date">${icon('target')}<span>内部复查</span><strong>${esc(r.review_at||'未设置日期')}</strong></div><h3>${esc(r.title)}</h3><p>${esc(eventBy(r.event_id)?eventTitle(eventBy(r.event_id)):'原关联档案不在当前事件列表，记录已保留。')}</p><div class="task-status">${badge(r.state==='completed'?'已完成':'待完成',r.state==='completed'?'green':'orange')}<small>复查日期不代表公告日期</small></div><div class="task-actions">${btn(r.state==='completed'?'重新打开':'完成待办','task-state',r.id,'compact')}${btn('编辑','task-edit',r.id,'compact')}${btn('历史','research-history',r.id,'compact')}</div></article>`).join('');}
  function taskPanel(e){if(currentDossier?.historical)return '';return `<div class="space-top">${panel('待验证事项与研究待办',`<p>${esc(readerText(decisions(e).flatMap(d=>d.missing||[]).join('；')))}</p>${taskCards(research.items.filter(r=>r.kind==='task'&&r.event_id===e.member_id))}${btn('新增待办','task-new',e.member_id)}${value('task',e.member_id)?`<p class="small muted">旧版待办保留：${esc(value('task',e.member_id).text)}（${value('task',e.member_id).done?'已完成':'待完成'}）</p>`:''}`)}</div>`;}
  async function openResearch(action,id){
    let kind=action==='review-open'?'review':action==='handling-open'?'handling':'task',record=research.items.find(r=>r.kind===kind&&r.id===id),eventId;
    if(kind==='handling')eventId=notices().find(d=>d.id===id)?.event.member_id;
    else eventId=action==='task-edit'?record?.event_id:id;
    const snapshot=await api('dossier',{id:eventId});const object=snapshot.objects.find(o=>o.name===selectedImpact)||snapshot.objects[0];
    researchForm={kind,eventId,snapshot,record:action==='task-edit'||kind==='handling'?record:null,id:kind==='handling'?id:action==='task-edit'?id:crypto.randomUUID(),object};
    const input=(name,label,v='',type='text')=>`<label class="stack">${label}<input class="select" data-research="${name}" type="${type}" maxlength="1000" value="${esc(v)}"></label>`;
    let form;
    if(kind==='review')form=`<p>${esc(object?.name||'未确认对象')} · 事件v${snapshot.key.event_version} / 材料v${snapshot.key.material_version}</p>${input('operator','复核人（由你填写，不认证专家身份）')}<label class="stack">复核方向<select class="select" data-research="result">${['uncertain','positive','negative','neutral','mixed'].map(v=>`<option value="${v}">${direction(v)}</option>`).join('')}</select></label><label class="stack">复核依据<textarea class="select" data-research="reason" maxlength="1000" rows="4" style="min-height:100px"></textarea></label>`;
    else if(kind==='task')form=input('title','待办事项',record?.title)+input('review_at','内部复查日期（不代表公告发布日期）',record?.review_at,'date')+`<label>状态<select class="select" data-research="state"><option value="pending" ${record?.state==='pending'?'selected':''}>待完成</option><option value="completed" ${record?.state==='completed'?'selected':''}>已完成</option></select></label>`;
    else form=`<p>提醒版本 v${notices().find(d=>d.id===id)?.version}</p><label>处置状态<select class="select" data-research="state">${Object.entries(handlingLabels).map(([k,v])=>`<option value="${k}" ${record?.state===k?'selected':''}>${v}</option>`).join('')}</select></label>${input('owner','责任人',record?.owner)}<label class="stack">处置说明<textarea class="select" data-research="note" maxlength="1000" rows="4" style="min-height:100px">${esc(record?.note||'')}</textarea></label>`;
    drawer(kind==='review'?'记录人工方向复核':kind==='task'?'研究待办':'记录预警处置',`<div class="stack">${form}${btn('保存记录','research-save','','primary')}</div>`);
  }
  async function saveResearch(){
    if(!researchForm)return;const f=researchForm,value=Object.fromEntries([...document.querySelectorAll('[data-research]')].map(n=>[n.dataset.research,n.value]));
    if(f.kind==='review')value.object_id=f.object?.id;
    if(f.kind==='handling')value.notice_version=f.snapshot.event.warning_reasons.decisions.find(d=>d.id===f.id)?.version;
    const button=document.querySelector('[data-real="research-save"]');button.disabled=true;
    try{await api('research',{}, {run,revision:status.run.revision,event_id:f.eventId,kind:f.kind,id:f.id,record_revision:f.record?.revision||0,snapshot_key:f.snapshot.key,value});researchForm=null;await load();close();toast('记录已保存，原判断保持不变');}
    finally{if(button.isConnected)button.disabled=false;}
  }
  function horizontalTimeline(e){
    const nodes=currentDossier.navigation?.timeline_nodes||currentDossier.timeline_nodes||[],versions=currentDossier.navigation?.versions||currentDossier.versions||[],selected=currentDossier.key.event_version;
    return `<section class="panel timeline-panel" id="real-timeline"><div class="panel-heading"><h2>${icon('target')}事件时间线 <small>${nodes.length} 个进展</small></h2><div class="timeline-toolbar"><label>按日回看 <input type="date" class="select" id="timeline-day" aria-label="进展日期" value="${esc(new URLSearchParams(location.hash.split('?')[1]||'').get('day')||day(e.updated_at||original(e).publication_time))}"></label>${btn('定位','timeline-day',e.member_id)}<select class="select" id="real-replay-version" aria-label="回放时点"><option value="">最新档案</option>${versions.map(v=>`<option value="${v.material_version}" ${currentDossier.historical&&v.material_version===currentDossier.key.material_version?'selected':''}>第${v.material_version}次保存 · ${day(v.saved_at)}</option>`).join('')}</select></div></div><div class="timeline real-timeline">${nodes.map(n=>{
      const t=(e.event_timeline?.items||[]).find(t=>t.source_row&&day(t.time)===day(n.time)&&t.kind===n.kind),m=currentDossier.sources?.nodes.find(m=>m.source_row===t?.source_row);
      const color=/澄清|更正|回应/.test(n.kind)?'green':/传闻|待核/.test(n.kind)?'orange':/首次|发布/.test(n.kind)?'blue':'purple';
      const current=n.id&&e.event_timeline?.current_node?n.id===e.event_timeline.current_node:n.event_version===selected;
      return `<a class="timeline-node ${color} ${current?'current':''} ${n.event_version>selected?'subsequent':''}" href="#/events/${encodeURIComponent(e.member_id)}?material_version=${n.material_version}" ${current?'aria-current="step"':''}><div class="timeline-date"><time>${esc(day(n.time))}</time>${current?'<span class="timeline-selected">当前查看</span>':n.event_version>selected?'<span class="timeline-later">后续进展</span>':''}</div><span class="timeline-dot"></span><div class="timeline-content">${badge(n.kind,color)}<p>${esc(n.summary)}</p><span class="timeline-source">${m?esc(m.source||'来源未提供'):'进展记录'}</span></div></a>`;
    }).join('')||'<p class="small muted">尚未记录可回看的进展。</p>'}</div>${e.event_timeline?.business_order?.status==='parallel_pending'?'<p class="small muted">节点先后尚待核实，当前保留已保存的判断；报道较晚不代表业务较晚发生。</p>':''}</section>`;
  }

  function identityPanels(e){
    const subjects=e.event_subjects||{},people=subjects.participants||[],affected=displayedAffected(e);
    const identities=(e.monitoring_scope?.business_objects||[]).map(o=>o.identity_resolution).filter(Boolean);
    const identityProof=identities.length?`<details class="subject-gaps"><summary>名称与代码依据</summary>${identities.map(i=>`<p><strong>${esc(i.name)} · ${esc(i.listings?.map(x=>x.code).join(' / ')||i.code)}</strong><br>${i.relation==='same_entity'?'原文名称已与证券名录核对。':i.relation==='model_alias'?('简称归一为模型初判：'+i.alias_reason+'；代码已查证券名录。'):`原文提及“${esc(i.anchor)}”，依据已保存的产品或公司归属关联到该公司。`}</p>${i.sources.filter(s=>/^https?:\/\//.test(s.url||'')).map(s=>`<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(({sse:'上交所证券名录',szse:'深交所证券名录',hkex:'港交所证券名录','geely-product':'官方产品资料','geely-annual-2025':'公司年报'})[s.id]||'官方身份资料')}</a> `).join('')}`).join('')}</details>`:'';
    const person=p=>`<span class="subject-chip"><strong>${esc(p.name)}</strong><small>${esc(p.role||'角色待明确')}</small>${p.evidence?.length?btn('依据','proof',JSON.stringify({row:p.evidence[0].source_row,proof:p.evidence[0]}),'text-button'):''}</span>`;
    return `<div class="identity-grid"><section id="real-subjects"><h2>${icon('users')}事件主体</h2><div class="identity-content">${people.map(person).join('')||`<p class="small muted">${currentDossier.historical?'该历史版本尚未抽取主体。':subjects.uncertainties?.[0]||'当前材料未明确具体主体。'}</p>`}${subjects.uncertainties?.length?`<details class="subject-gaps"><summary>原文缺口与主体说明</summary>${bullets(subjects.uncertainties)}</details>`:''}</div></section><section id="real-affected"><h2>${icon('target')}影响对象</h2><div class="identity-content">${affected.map(o=>`<article class="affected-item"><div><span class="object-code">${esc(o.code)}</span><strong>${esc(o.name)}</strong> ${badge(direction(o.direction)+(o.reference?' · 原标注':''),tone(direction(o.direction)))}</div><button class="affected-path" data-real="impact-details" data-id="${esc(o.name)}">${esc(o.pathways?.[0]||'影响路径待补充')} <span>详情 ↗</span></button></article>`).join('')||'<p class="small muted">当前材料尚不足以形成具体公司或行业的影响判断。</p>'}${identityProof}</div></section></div>`;
  }
  function currentConclusion(e){
    const affected=displayedAffected(e),gaps=[...new Set(decisions(e).flatMap(d=>d.missing||[]))],reference=affected.length&&affected.every(o=>o.reference);
    const labels=[...new Set(affected.map(o=>direction(o.direction)))];
    return `<section class="verdict-card ${labels.length===1?tone(labels[0]):'orange'}"><h2>${icon('shield')}${currentDossier.historical?'当时结论':'当前结论'}</h2><strong>${reference?'影响方向参考':labels.length===1?labels[0]+' · 影响判断':labels.length>1?'各对象影响不同':'仍需补充判断依据'}</strong><p>${esc(readerText(reference?affected.map(o=>o.name+'：'+direction(o.direction)).join('；'):gaps[0]||affected[0]?.pathways?.[0]||'已有报道可查看；具体影响和适用证据仍待审阅。'))}</p><button class="text-button" data-real="section" data-id="${reference?'real-impact':'real-warning'}">${reference?'查看原标注及分析':'查看影响与待核事项'} →</button></section>`;
  }

  function businessAnalysis(name){
    const rows=(currentDossier?.business_output?.label_rows||[]).filter(r=>(r.secu_abbr||r.industry_name)===name);
    return rows.map(r=>{const a=r.impact||{},terms=[['情感判断依据',(r.emotion_evidence||[]).map(x=>x.reason)],['事实依据',a.facts],['影响路径',a.pathways],['成立条件',a.conditions],['尚缺信息',a.gaps],['后续观察',a.next_observations]];
      return `<div class="material-row business-analysis"><p><strong>${esc(r.event_name||'类别待核')}</strong> · 事件情感：${badge(r.event_emotion||'未单独判断',tone(r.event_emotion||'待判断'))} · 影响方向：${badge(direction(a.direction),tone(direction(a.direction)))}</p><details><summary>完整事件与对象分析</summary><dl class="detail-list">${terms.map(([label,items])=>`<dt>${label}</dt><dd>${esc(items?.length?items.join('；'):label==='成立条件'?'未单独记录，不代表没有条件':'尚未形成该项判断')}</dd>`).join('')}<dt>所属行业</dt><dd>${esc(r.industry_code?[r.industry_code,r.industry_name].filter(Boolean).join(' / '):r.industry_name||'未提供')}${r.industry_reference?'<p class="small muted">来源：获准训练资料中的公司基础字段；非本次模型预测，不代表该行业受到本事件影响。</p>':''}</dd><dt>类别依据</dt><dd>${esc(r.classification?.reason||'待补充定义匹配理由')}${r.classification?.semantic_review_status==='pending'?'（定义匹配尚待语义复核）':''}</dd></dl></details></div>`;
    }).join('');
  }
  function showcaseImpact(e){
    const os=objects(e);for(const d of decisions(e)){if(d.target&&!os.some(o=>o.name===d.target))os.push({name:d.target,code:'关联事项 · 非新增监测对象',type:'context'});}if(!os.some(o=>o.name===selectedImpact))selectedImpact=os[0]?.name||'';
    const annotations=(currentDossier?.historical?[]:e.annotations||[]).filter(a=>(a.secu_abbr||a.industry_name)===selectedImpact);
    const ds=decisions(e).filter(d=>d.target===selectedImpact||(d.targets||[]).includes(selectedImpact));
    const reviewed=ds.filter(impactV3).map(d=>`<article class="material-row"><p>${badge(direction(d.rule_score.direction),tone(direction(d.rule_score.direction)))} ${esc(explanationText(d.rule_score.pathway))}</p>${impactRulesV3(d)}${d.stale_assessment?'<p>当前业务阶段尚待新的影响审阅，以上为上阶段保存判断。</p>':''}${d.missing?.length?`<details><summary>待核事项</summary>${bullets(d.missing)}</details>`:''}</article>`).join('');
    return `<section class="panel space-top" id="real-impact"><div class="assessment-tabs" role="group" aria-label="选择监测对象">${os.map(o=>`<button class="assessment-tab ${o.name===selectedImpact?'active':''}" data-real="impact-object" data-id="${esc(o.name)}" aria-pressed="${o.name===selectedImpact}">${badge(o.type==='industry'?'行业':o.type==='context'?'关联事项':'个股',o.type==='industry'?'green':'blue')}<strong>${esc(o.name)}</strong><small>${esc(o.code)}</small></button>`).join('')}</div><div class="panel-heading"><h2>事件与影响分析</h2><span>${esc(selectedImpact)}</span></div><div class="panel-body">${businessAnalysis(selectedImpact)}${reviewed||annotations.map(a=>`<article class="material-row"><h3>${esc(a.event_name)} ${badge('原数据参考','purple')}</h3><p>${badge(a.event_emotion||'未提供',tone(a.event_emotion))}</p><p class="small muted">以下是原数据附带的分析，不属于本次审阅结论，可能引用报道之后的信息；不能作为当前快照的事实依据。</p><details><summary>展开原数据分析</summary><div class="real-source-text">${esc(a.event_impact_analysis||'该对象尚未提供影响分析。')}</div></details></article>`).join('')||ds.map(d=>`<article class="material-row"><p>影响等级：${badge(d.level||'待定')} ${d.rule_score?`· 规则分 ${impactPoints(d)}`:''}</p>${(d.effects||[]).map(x=>`<p>${badge(direction(x.original?.direction),tone(direction(x.original?.direction)))}</p><div class="real-source-text">${esc(explanationText(x.original?.pathway)||'影响路径未提供')}</div><p>条件：${esc((x.original?.conditions||[]).join('；')||'未提供')}</p><p>缺口：${esc((x.original?.gaps||[]).join('；')||'未提供')}</p>`).join('')}${bullets(d.reasons||[])}</article>`).join('')||'<p>该对象没有已保存的影响分析；不推定中性或复用其他对象的判断。</p>'}${reviewed&&annotations.length?`<details><summary>查看原数据分析参考</summary><p>原标注不作为本次影响评分依据，可能包含较晚信息。</p>${annotations.map(a=>`<div class="real-source-text">${esc(a.event_impact_analysis||'未提供')}</div>`).join('')}</details>`:''}</div></section>`;
  }
  function showcaseArchive(e){
    const members=currentDossier?.sources?.nodes||e.evidence_sources?.members||[],historical=currentDossier?.historical,cred=e.credibility||{},score=typeof cred.score==='number'?cred.score:null;
    const summary=e.event_summary?.text&&e.event_summary.text!==eventTitle(e)?e.event_summary.text:'原始报道已保存，事件摘要待整理。';
    return `${historical?'<p class="replay-banner">正在查看历史版本</p>':''}<div class="dossier-topline"><span class="page-kicker">事件档案</span><div class="heading-actions">${historical?'<a class="button primary" href="#/events/'+encodeURIComponent(e.member_id)+'">返回最新档案</a>':btn(value('follow',e.member_id)?'★ 已关注':'+ 关注事件','follow',e.member_id,'primary')+btn(value('read',e.member_id)?'已读':'标记已读','read',e.member_id)}</div></div><div class="dossier-hero" id="real-overview" data-event-id="${esc(e.member_id)}"><div class="dossier-intro"><h1 class="dossier-title">${esc(eventTitle(e))}</h1><div class="event-tags">${objects(e).map(o=>badge((o.code?o.code+' ':'')+o.name,o.type==='industry'?'teal':'blue')).join(' ')}${[...new Set(definitions(e).map(d=>d.event_name))].map(n=>badge(n,typeTone(n))).join(' ')}</div><div class="summary-copy"><span>【事件摘要】</span><p class="event-summary">${esc(summary)}</p></div><div class="archive-meta"><span>${esc(original(e).source||'来源未提供')}</span><span>披露 ${esc(day(original(e).publication_time)||'日期未提供')}</span><span>${members.length} 篇相关材料${e.record_kind==='news'?' · 原始报道':''}</span></div>${(e.belongs_to_events||[]).map(id=>`<a class="text-button" href="#/events/${encodeURIComponent(id)}">查看所属事件档案 →</a>`).join('')}</div>${currentConclusion(e)}<section class="model-note credibility-card"><h2>综合可信度</h2><div class="score-display">${score===null?'<strong class="score-pending">待评分</strong>':`<strong>${esc(score)}</strong><span>分</span>`}</div><button class="text-button" data-real="score-info" data-id="${esc(e.member_id)}">查看评分依据 →</button></section></div>`+
      (status.case_session&&!historical?`<p class="space-top">已加入 ${status.case_session.step} / ${status.case_session.total} 份材料 ${btn('加入下一份材料','case-next')}${btn('重新回放','case-reset')}</p>`:'')+
      `<nav class="tabbar section-tabs" aria-label="档案内容导航">${[['real-overview','事件总览'],['real-timeline','时间线'],['real-sources','传播关系'],['real-claims','主张与证据'],['real-impact','影响分析'],['real-materials','相关报道'],['real-warning','预警理由']].map(([id,label],i)=>`<button class="tab ${i===0?'active':''}" data-real="section" data-id="${id}">${label}</button>`).join('')}${btn('历史快照','versions',e.member_id)}</nav>`+identityPanels(e)+horizontalTimeline(e)+`<div class="archive-grid"><section class="panel relation-panel" id="real-sources"><div class="panel-heading"><h2>${icon('network')}传播与来源关系</h2></div><div class="panel-body">${sourceGraph()}</div></section>${claimMatrix(e)}</div>`+(e.event_relations||[]).filter(p=>p.raw?.k==='insufficient').map(p=>panel('待核关联',`<p>${esc(p.raw.why)}</p>`)).join('')+showcaseImpact(e)+
      `<section class="panel space-top" id="real-materials"><div class="panel-heading"><h2>${icon('file')}相关报道与证据原文</h2><span class="small muted">${members.length} 篇材料</span></div><div class="panel-body">${members.map(m=>`<article class="material-row"><strong>${esc(m.title)}</strong><p>${esc(m.source||'来源未提供')} · ${esc(sourceDate(m))}</p>${btn('查看原文','document',m.source_row)}</article>`).join('')||'<p>该档案尚未记录可读取的原文。</p>'}${e.identification_review?`<details class="report-relation"><summary>事件要素与情感的审阅记录</summary><p>${esc(e.identification_review.reason)}</p><small>审阅时间 ${esc(date(e.identification_review?.reviewed_at||e.emotions?.[0]?.reviewed_at||e.event_subjects?.reviewed_at))}</small></details>`:''}</div></section>`+
      `<section class="panel space-top" id="real-warning"><div class="panel-heading"><h2>${icon('alert')}预警理由与待核事项</h2></div><div class="panel-body">${noticesForSnapshot(e)}</div></section>`+taskPanel(e);
  }

  function noticesForSnapshot(e){return decisions(e).map(d=>`<article class="material-row"><strong>${esc(d.target||'对象待明确')} · ${d.active?'已触发提醒':d.version?'该版未发布有效提醒':'未触发提醒'}${d.version?' · 第'+d.version+'次判断':''}</strong>${bullets(d.reasons||['尚未形成可检查的预警理由。'])}</article>`).join('')||'<p>该时点尚未形成预警判断。</p>';}
  function archive(id){
    const e=currentDossier?.event||eventBy(id)||(!id?defaultEvent():null);if(!e)return heading('事件档案',btn('查看新闻原文','materials'))+empty('当前没有可展示的事件档案','请先查看材料和处理状态。');
    if(showcase()||currentDossier?.business_output)return showcaseArchive(e);
    const tasks=value('task',e.member_id),cred=e.credibility||{},timeline=e.event_timeline?.items||[],members=currentDossier?.sources?.nodes||e.evidence_sources?.members||[],quotes=e.evidence_sources?.original_quotes||[];
    const counts=(cred.claims||[]).reduce((r,c)=>(r[c.verdict||'尚未核验']=(r[c.verdict||'尚未核验']||0)+1,r),{});
    return `${currentDossier?.historical?`<div class="replay-banner"><span>历史保存快照 · 事件v${currentDossier.key.event_version} / 材料v${currentDossier.key.material_version} · 保存 ${esc(date(currentDossier.saved_at))}<br>这是当时保存的判断，可能包含后来已纠正的错误，不代表当前有效结论。</span><a class="text-button" href="#/events/${encodeURIComponent(e.member_id)}">返回最新档案 →</a></div>`:''}<div class="page-heading archive-heading"><div><h1 class="archive-title"><span>事件档案</span>${esc(eventTitle(e))}</h1><div class="event-tags">${objectChips(e)} ${definitions(e).map(d=>badge(d.event_name,'gray')).join(' ')} ${priorityBadge(e)}</div><div class="archive-meta"><span>新闻发布：${esc(date(original(e).publication_time))}</span><span>事件版本 v${esc(e.event_version||'待定')}</span><span>AI初判 · 未独立复核</span></div></div><div class="heading-actions">${currentDossier?.historical?'':btn(value('follow',e.member_id)?'已关注':'关注','follow',e.member_id)+btn(value('read',e.member_id)?'已读':'标记已读','read',e.member_id)}</div></div>`+
      `<section class="conclusion"><h2>${currentDossier?.historical?'当时结论':'当前结论'} ${badge(cred.status||'尚未核验',tone(cred.status||'待核'))}</h2><p>${esc(e.event_summary?.text||'事件摘要尚未形成')}</p><div class="space-top">${Object.entries(counts).map(([v,n])=>badge(v+' '+n+' 项',tone(v))).join(' ')}</div><p class="small muted">新闻标题保留原文表述；以上摘要是AI识别结果，逐项依据见下表。${cred.assessment_cutoff?'判断使用材料截至 '+esc(date(cred.assessment_cutoff)):''}</p></section>`+
      objectAssessment(e)+reviewPanel(e)+`<nav class="tabbar" aria-label="档案内容导航">${[['real-timeline','时间线'],['real-sources','来源关系与材料'],['real-claims','主张与证据'],['real-impact','对象判断'],['real-history','历史判断']].map(([sid,label])=>btn(label,'section',sid)).join('')}</nav>`+
      `<section class="panel" id="real-timeline"><div class="panel-heading"><h2>事件时间线</h2><span class="small muted">发生、发布、接收与判断时间分别保留</span></div><div class="panel-body">${timeline.map(t=>`<details class="material-row"><summary>${esc(date(t.time))} · ${esc(t.kind)}</summary><p>${esc(t.summary)}</p>${t.source_row?btn('查看依据原文','document',t.source_row):''}${(t.proofs||[]).map(p=>`<blockquote>${esc(p.quote)}</blockquote>`).join('')}</details>`).join('')||'<p>暂无时间记录。</p>'}</div></section>`+
      claimMatrix(e)+`<div class="archive-grid space-top"><section class="panel" id="real-sources"><div class="panel-heading"><h2>材料来源与关系</h2></div><div class="panel-body">${members.map((m,i)=>`<article class="material-row"><strong>${i+1}. ${esc(m.title||'未填标题')}</strong><p class="small muted">${esc(m.source||'来源未提供')} · 可用时间 ${esc(date(m.available_at))}</p>${btn('查看原文','document',m.source_row)}</article>`).join('')||btn('查看新闻原文','document',e.job_id)}${sourceGraph()}<details><summary>查看已定位的原文引文 (${quotes.length})</summary>${quotes.map(q=>`<blockquote>${esc(q.quote)}<p class="small muted">${q.section==='title'?'标题':'正文'}位置 ${q.start}—${q.end}</p></blockquote>`).join('')}</details></div></section>${panel('对象关联依据',definitions(e).map(d=>`<p><strong>${esc(d.target_name)} · ${esc(d.target_code)}</strong></p><p>${esc(d.event_name)}</p>`).join('')+`<details><summary>查看匹配依据与主体角色</summary>${(e.monitoring_scope?.matches||[]).filter(m=>m.decision==='matched').map(m=>`<p>${esc(m.reason)}</p>`).join('')}${bullets((e.event_subjects?.participants||[]).map(p=>p.name+' · '+p.role))}</details><p class="small muted">官方对象关联的模型初判；公司与行业不自动互相推导。</p>`)}</div>`+
      `<section id="real-history" class="space-top">${snapshotVersions(e)}${judgmentHistory(e)}</section>`+
      taskPanel(e);
  }
  function taskGroups(){
    const groups=new Map();
    for(const r of research.items.filter(r=>r.kind==='task')){const key=JSON.stringify([r.event_id,r.title,r.review_at,r.state]);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(r);}
    return [...groups.values()];
  }
  function reviewTasks(){
    const groups=taskGroups();
    drawer('复查安排',`<p class="task-explainer">这里是手动创建的复查记录，和自动发现的待核事项分开。同一事件、标题、日期和状态相同的记录合并展示，原记录与历史均保留。</p>${groups.map(rows=>`<section class="review-task-group"><h3>${esc(rows[0].title)}</h3><p>${esc(eventBy(rows[0].event_id)?eventTitle(eventBy(rows[0].event_id)):'原档案不在当前范围')}</p><div>${badge(rows[0].state==='completed'?'已完成':'待完成',rows[0].state==='completed'?'green':'orange')} <time>计划复查：${esc(rows[0].review_at||'未设置')}</time></div>${rows.length>1?`<details><summary>${rows.length} 条重复安排 · 分别管理</summary>${taskCards(rows)}</details>`:taskCards(rows)}</section>`).join('')||'<p>尚未创建复查安排。可在事件档案中记录需要复查的具体问题。</p>'}`);
  }
  function followingRows(list){return list.length?`<div class="table-container"><table class="data-table following-table" aria-label="事件关注列表"><colgroup><col style="width:24%"><col style="width:14%"><col style="width:24%"><col style="width:24%"><col style="width:8%"><col style="width:6%"></colgroup><thead><tr><th>关注对象 / 事件</th><th>关注标签</th><th>最新动态</th><th>待验证事项</th><th>状态</th><th>操作</th></tr></thead><tbody>${list.map(e=>{
      const os=objects(e),gaps=[...new Set(decisions(e).flatMap(d=>d.missing||[]))],types=[...new Set(definitions(e).map(d=>d.event_name).filter(Boolean))];
      return `<tr data-watch-row="${esc(e.member_id)}"><td><div class="watch-identity"><span class="entity-mark ${os[0]?.type==='industry'?'industry':''}">${esc((os[0]?.name||'事').slice(0,1))}</span><div>${os.map(o=>`<strong>${esc(o.name)} <small class="object-code">${esc(o.code)}</small></strong>`).join('')}</div></div><a class="watch-event-title" href="#/events/${encodeURIComponent(e.member_id)}" title="${esc(eventTitle(e))}">${esc(eventTitle(e))}</a></td><td><div class="watch-tags">${types.map(n=>badge(n,typeTone(n))).join('')}${(e.industries||[]).map(n=>badge(n,'teal')).join('')}</div></td><td><p class="watch-change">${esc(e.record_kind==='news'?(e.identification_review?e.event_summary?.text:'原始报道已保存，尚未形成独立进展判断。'):e.change_summary||e.event_summary?.text||'尚无更新')}</p><time class="watch-time">${esc(day(updateTime(e)))} 披露</time></td><td>${gaps.length?`<ol class="watch-missing">${gaps.slice(0,3).map(g=>`<li>${esc(g)}</li>`).join('')}</ol>${gaps.length>3?`<a class="text-button" href="#/events/${encodeURIComponent(e.member_id)}">另有 ${gaps.length-3} 项 →</a>`:''}`:`<p class="watch-change">${e.record_kind==='news'?'待完成事项识别与证据核查':e.follow_detail_unavailable?'详情暂未读取，可重试打开档案。':'当前未记录待补材料。'}</p>`}</td><td>${badge(value('read',e.member_id)?'已读':'未读',value('read',e.member_id)?'green':'red')}</td><td><div class="watch-actions"><a class="button compact" href="#/events/${encodeURIComponent(e.member_id)}">查看</a>${!value('read',e.member_id)?btn('已读','read',e.member_id,'text-button'):''}${followStar(e)}</div></td></tr>`;
    }).join('')}</tbody></table></div>`:`<div class="watch-empty">${empty('还没有符合条件的关注事件','在事件雷达关注事件后，最新动态和待验证事项会汇集到这里。')}${btn('前往事件雷达','go-radar','','primary')}</div>`;}

  function following(){
    const followed=events.filter(e=>value('follow',e.member_id)),subscriptions=actions.filter(a=>a.kind==='subscription'&&a.value).length;
    const tabs=`<div class="tabbar follow-tabs">${[['events','事件关注',followed.length],['objects','对象订阅',subscriptions]].map(([id,label,n])=>`<button class="tab ${followTab===id?'active':''}" data-real="follow-tab" data-id="${id}">${label} <span>${n}</span></button>`).join('')}<button class="text-button follow-rules" data-real="watch-rules">提醒规则</button></div>`;
    if(followTab==='objects'){
      const catalog=status.catalog.filter(o=>objectScope==='all'||o.type===objectScope);if(!catalog.some(o=>o.id===selectedObject))selectedObject=catalog[0]?.id;
      const selected=catalog.find(o=>o.id===selectedObject),linked=selected?events.filter(e=>objects(e).some(o=>objectKey(o)===objectKey(selected))):[];
      return heading('关注追踪')+tabs+`<div class="filterbar">${scopeSwitch(objectScope,'object-scope')}<span class="small muted">已订阅 ${actions.filter(a=>a.kind==='subscription'&&a.value).length} 个对象 ${showcase()?'':'· 关联为模型初判'}</span></div><div class="subscription-layout">${panel('对象订阅',`<div class="table-container" style="max-height:650px;overflow:auto"><table class="data-table"><thead><tr><th>监测对象 / 代码</th><th>关联事件</th><th>订阅</th></tr></thead><tbody>${catalog.map(o=>`<tr class="${o.id===selectedObject?'selected':''}"><td>${btn(o.name,'object-select',o.id)}<span class="event-code">${esc(o.code)} · ${o.type==='industry'?'行业':'个股'}</span></td><td>${events.filter(e=>objects(e).some(t=>objectKey(t)===objectKey(o))).length}</td><td>${btn(value('subscription',o.id)?'取消订阅':'订阅','subscription',o.id)}</td></tr>`).join('')}</tbody></table></div>`)}${panel(selected?.name||'关联事件',`${selected?btn('查看监测定义','definition',selected.id):''}${linked.map(e=>`<article class="material-row"><a class="event-title" href="#/events/${encodeURIComponent(e.member_id)}">${esc(eventTitle(e))}</a><p>${esc(e.event_summary?.text)}</p>${impactBadges(e)}<div class="space-top">${btn(value('follow',e.member_id)?'已关注事件':'关注此事件','follow',e.member_id)}</div></article>`).join('')||empty('当前没有关联事件','只使用已保存的对象匹配，不按行业分类推定影响，也不自动调用模型。')}`)}</div>`;
    }
    const list=events.filter(e=>value('follow',e.member_id)&&(!followQuery||[eventTitle(e),...objects(e).map(o=>o.name)].join(' ').includes(followQuery))&&(followFilter==='all'||(followFilter==='read')===!!value('read',e.member_id))).map(e=>({...e,...followDetails.get(e.member_id)}));
    const groups=taskGroups(),hidden=actions.filter(a=>a.kind==='follow'&&a.value&&!eventBy(a.target)).length;
    return heading('关注追踪',btn('+ 新增关注','go-radar','','primary')+btn('复查安排'+(groups.length?'（'+groups.length+'组）':''),'review-tasks'))+tabs+`<div class="filterbar follow-filters"><select id="real-watch-status" class="select" aria-label="关注未读状态">${[['all','全部状态'],['unread','未读事件'],['read','已读事件']].map(([v,t])=>`<option value="${v}" ${followFilter===v?'selected':''}>${t}</option>`).join('')}</select><span class="small muted">${followed.filter(e=>!value('read',e.member_id)).length} 件未读 · ${followed.length} 件持续关注</span><label class="search-box">${icon('search')}<input id="real-follow-query" type="search" placeholder="搜索已关注的事件…" aria-label="搜索已关注事件" value="${esc(followQuery)}"></label>${btn('搜索','follow-search')}</div><div class="following-layout">${followingRows(list)}<div class="pagination">当前显示 ${list.length} 个关注事件</div>${hidden?`<p class="list-note">另有 ${hidden} 条关注记录关联的事件不在当前范围，记录已保留。</p>`:''}</div>`;
  }
  let alertFilter='all',alertPriority='',selectedAlert='',selectedAlertVersion=null;
  function alerts(){
    const all=notices(),list=all.filter(d=>(alertFilter==='all'||handlingState(d)===alertFilter)&&(!alertPriority||d.level===alertPriority));
    if(!list.some(d=>d.id===selectedAlert)){selectedAlert=list[0]?.id;selectedAlertVersion=null;}const d=list.find(x=>x.id===selectedAlert);
    const summary=`<section class="metrics alert-metrics" aria-label="预警处置摘要">${Object.entries(handlingLabels).map(([id,label])=>`<button class="metric ${handlingTone[id]} ${alertFilter===id?'selected':''}" data-real="alert-filter" data-id="${id}" aria-pressed="${alertFilter===id}"><span class="metric-symbol">${icon(({pending:'alert',in_progress:'users',waiting_material:'file',closed:'shield'})[id])}</span><span><span class="metric-value">${all.filter(x=>handlingState(x)===id).length}</span><span class="metric-label">${label}</span><span class="metric-note">${id==='closed'?'保留处置记录':id==='waiting_material'?'跟进待补依据':id==='in_progress'?'正在跟进处置':'等待安排处置'}</span></span></button>`).join('')}</section>`;
    return `<div class="alerts-layout"><div class="alerts-main">${heading('预警中心')}${summary}<div class="alert-toolbar"><div class="filters"><select class="select" id="real-alert-priority" aria-label="预警优先级"><option value="">全部优先级</option>${['高','中','低','待定'].map(v=>`<option value="${v}" ${alertPriority===v?'selected':''}>${levelLabel(v)}</option>`).join('')}</select><select class="select" id="real-alert-status" aria-label="预警处置状态"><option value="all">全部处置状态</option>${Object.entries(handlingLabels).map(([v,label])=>`<option value="${v}" ${alertFilter===v?'selected':''}>${label}</option>`).join('')}</select>${btn('重置筛选','alert-clear')}</div><span class="small muted">${all.filter(d=>d.active).length} 条有效提醒</span></div>${list.length?`<div class="table-container"><table class="data-table alert-table" aria-label="预警列表"><colgroup><col style="width:10%"><col style="width:34%"><col style="width:28%"><col style="width:16%"><col style="width:12%"></colgroup><thead><tr><th>优先级</th><th>预警对象 / 事件</th><th>本次触发</th><th>处置状态</th><th>操作</th></tr></thead><tbody>${list.map(x=>`<tr class="${d?.id===x.id?'selected featured':''}" data-alert-row="${esc(x.id)}"><td><span class="priority-flag ${levelTone(x.level)}">${esc(({高:'高',中:'中等',低:'低'})[x.level]||'待定')}</span></td><td><button class="alert-object-title" data-real="alert-select" data-id="${esc(x.id)}">${esc(x.target||'对象待明确')}</button><p class="alert-event-name">${esc(eventTitle(x.event))}</p><time class="watch-time">相关披露 ${esc(day(updateTime(x.event)))}</time></td><td>${badge(x.route==='important_pending'?'重要待核':'事件进展',x.route==='important_pending'?'orange':'blue')}<p class="alert-trigger">${esc(x.fact_summary||x.event.change_summary||x.event.event_summary?.text||'查看触发依据')}</p></td><td>${badge(!x.active?alertInactive(x):handlingLabels[handlingState(x)],!x.active?'gray':handlingTone[handlingState(x)])}</td><td>${btn('查看','alert-select',x.id,'compact')}</td></tr>`).join('')}</tbody></table></div>`:`<div class="alert-empty">${empty(all.length?'这个筛选下没有提醒':'目前没有符合规则的提醒',all.length?'可切换上方状态，或重置筛选查看全部提醒。':'有具体触发依据后，提醒会出现在这里。')}${all.length?btn('查看全部提醒','alert-clear'):''}</div>`}<div class="pagination">显示 ${list.length} 条 · 共 ${all.length} 条已保存提醒</div></div><aside class="alert-detail" aria-label="预警详情" tabindex="-1">${d?'<div class="detail-title"><h2>预警详情</h2><span class="small muted">正在读取依据…</span></div>':empty('选择一条提醒','查看触发依据、材料缺口和处置记录。')}</aside></div>`;
  }
  function handlingFields(record){return `<div class="handling-fields"><label class="field">处置状态<select class="select" data-research="state">${Object.entries(handlingLabels).map(([k,v])=>`<option value="${k}" ${record?.state===k?'selected':''}>${v}</option>`).join('')}</select></label><label class="field">跟进人<input class="select" data-research="owner" maxlength="1000" placeholder="填写姓名" value="${esc(record?.owner||'')}"></label></div><label class="field">跟进说明<textarea class="select" data-research="note" maxlength="1000" rows="2" placeholder="记录核实进展或下一步安排">${esc(record?.note||'')}</textarea></label>${btn('保存处置记录','research-save','','primary')}`;}
  function alertTime(value){
    const time=new Date(value);
    return value&&Number.isFinite(time.getTime())?new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(time):'未记录';
  }
  const alertRuleName=d=>d.route==='important_pending'?'重要事项核查提醒':d.route==='event_notice'?'实质进展提醒':'已保存的预警判断';
  const impactV3=d=>d.rule_version==='alert-rules-20261004-v3';
  const impactPoints=d=>d.rule_score?`${impactV3(d)?d.rule_score.score_text:d.rule_score.score??'待定'} / ${impactV3(d)?100:4}`:'待评定';
  const alertInactive=d=>impactV3(d)?'已更新为观察':'已更正撤回';
  function impactRulesV3(d){
    const s=d.rule_score,e=s.evidence,labels={K:'事件性质',M:'对象影响范围',S:'业务阶段',T:'时间敏感性'},triggers={R1:'首次达到条件',R2:'关键阶段变化',R3:'关键事实确认',R4:'反转、恢复或范围改变',R5:'实质影响增量',R6:'更正先前判断',R7:'到期核查'};
    return `<section class="alert-rule"><h3>触发规则</h3><div class="alert-rule-body"><strong class="alert-rule-name">${esc(s.family_name)}</strong><p class="alert-rule-description">${esc(s.label)} · ${esc(d.delivery?.action||'已保存判断')}。${esc(d.delivery?.reason||'')}</p><div class="rule-outcome"><div><span>对象影响分</span><strong>${esc(s.score_text)} <small>/ 100</small></strong></div><div><span>本次证据状态</span><strong>${esc(e.label)}${e.conflict?' · 存在冲突':''}</strong></div></div><details class="rule-breakdown"><summary>查看四项分数、触发条件与依据</summary><p class="rule-threshold">40—59分黄色，60—79分橙色；80分以上还需明确紧迫性才为红色。优先规则另有下限；缺口按保守档位处理。影响分与可信度分开计算。</p><dl class="rule-components">${Object.entries(s.components).map(([k,v])=>`<dt>${labels[k]}</dt><dd>${esc(v.reason)}<b>${v.lower===v.upper?v.lower:v.lower+'—'+v.upper}分</b>${v.gap?`<p>缺口：${esc(v.gap)}</p>`:''}${(v.refs||[]).map(i=>d.impact_proofs?.[i]).filter(Boolean).map(p=>`<blockquote>${esc(p.quote)}${btn('查看原文','document',p.source_row,'text-button')}</blockquote>`).join('')}</dd>`).join('')}</dl>${s.measurement?`<p>比较口径：${esc(s.measurement.numerator)} / ${esc(s.measurement.denominator)} ${esc(s.measurement.unit)}，约 ${Number(s.measurement.percent).toFixed(2)}%。</p>`:''}${s.priority_rules.map(r=>`<p>${esc(r.rule)}：${esc(r.reason)}</p>`).join('')}${(d.delivery?.triggers||[]).map(r=>`<p>${esc(r)}：${esc(triggers[r])}</p>`).join('')}${d.delivery?.triggers?.includes('R6')?`<p>更正前：${esc(d.change_judgment.old_statement)}</p><p>更正后：${esc(d.change_judgment.new_statement)}</p><p>更正通知保留原通知优先级；当前影响按新事实重新计算。</p>`:''}<h4>本次绑定的主张</h4><ol class="bound-claims">${e.claims.map(q=>`<li><p>${esc(q.claim)}</p><span>${esc(['待核线索','具体报道依据','适用原始依据'][q.level])}${q.conflict?' · 存在冲突':''}</span></li>`).join('')}</ol><p class="rule-version">规则：${esc(d.rule_version)} · 评估于 ${esc(alertTime(d.impact_assessed_at))}</p></details><p>下一观察点：${esc(d.next_observation.text)}${d.next_observation.date?' · '+esc(d.next_observation.date):''}</p></div></section>`;
  }
  function alertRuleDetails(d,event){
    if(impactV3(d))return impactRulesV3(d);
    const known=d.rule_version==='competition-rules-20260923-v1',score=d.rule_score,raw=d.effects?.[0]?.original||{},claims=event.credibility?.claims||[];
    const bound=(d.bound_claims||[]).map(b=>{
      const saved=claims.find(c=>c.claim_id===(typeof b==='string'?b:b.claim_id));
      return {claim:saved?.claim||(typeof b==='object'?b.claim:'未找到该条绑定主张'),support:(typeof b==='object'?b.support:null)||saved?.support_score};
    });
    const supported=bound.filter(b=>b.support?.primary_supported&&!b.support.refuted&&!b.support.conflict).length;
    const conditions=[...new Set((d.effects||[]).flatMap(e=>e.original?.conditions||[]))];
    const ruleText=known?(d.route==='event_notice'?'影响达到中等或以上；绑定主张均有适用原始材料支持，且无反驳或冲突。':d.route==='important_pending'?'影响达到中等或以上；绑定主张存在冲突、反驳或原始依据缺口，触发核查提醒。':'按本次保存的规则判断。'):(d.reasons?.[0]||'未记录具体规则说明。');
    const dims=[['materiality','重要性',{minor:'轻微',substantive:'实质',unknown:'待核'}],['scope','影响范围',{limited:'有限',broad:'广泛',unknown:'待核'}],['urgency','时效',{long_term:'长期',near_term:'近期',unknown:'待核'}]];
    return `<section class="alert-rule"><h3>触发规则</h3><div class="alert-rule-body"><strong class="alert-rule-name">${esc(alertRuleName(d))}</strong><p class="alert-rule-description">${esc(ruleText)}</p><div class="rule-outcome"><div><span>影响等级</span><strong>${esc(({高:'高',中:'中等',低:'低'})[d.level]||'待定')}${score?.score==null?'':` <small>${esc(score.score)}${known?' / 4':''}分</small>`}</strong></div><div><span>原始材料支持</span><strong>${bound.length?`${supported} <small>/ ${bound.length} 项绑定主张</small>`:'未记录绑定主张'}</strong></div></div><details class="rule-breakdown"><summary>查看规则分项与绑定主张</summary>${known?'<p class="rule-threshold">影响分：0—1分为低，2分为中，3—4分为高；高影响另需可定位的比较依据。维度证据不全时等级待定，不自动提醒。此分值与可信度指数分开，历史提醒沿用生成时的评分规则。</p>':''}${score?`<dl class="rule-components">${dims.map(([key,label,values])=>`<dt>${label}</dt><dd>${esc(values[raw.dimensions?.[key]]||'未记录')}<b>${score.components?.[key]==null?'未评分':esc(score.components[key])+'分'}</b></dd>`).join('')}</dl>`:''}<h4>本次绑定的主张</h4>${bound.length?`<ol class="bound-claims">${bound.map(b=>`<li><p>${esc(b.claim)}</p><span class="${b.support?.primary_supported&&!b.support.refuted&&!b.support.conflict?'supported':'pending'}">${esc(b.support?.label||'未保存证据支持记录')}${b.support?.conflict?' · 存在冲突':b.support?.refuted?' · 有反驳依据':''}</span></li>`).join('')}</ol>`:'<p>该提醒未保存可对应的主张，请到档案核查。</p>'}${raw.pathway?`<h4>影响依据</h4><p>${esc(explanationText(raw.pathway))}</p>`:''}${conditions.length?`<h4>影响成立的条件</h4>${bullets(conditions)}`:''}<p class="rule-version">规则版本：${esc(d.rule_version||'未记录')}</p></details></div></section>`;
  }
  async function alertDetail(token){
    const current=notices().find(d=>d.id===selectedAlert);if(!current)return;
    const [latest,notifications]=await Promise.all([api('dossier',{id:current.event.member_id}),api('notifications')]);
    const versions=latest.reminder_versions.filter(v=>v.id===current.id),selected=versions.find(v=>v.version===selectedAlertVersion)||versions.at(-1);
    const snapshot=selected&&selected.material_version!==latest.key.material_version?await api('dossier',{id:current.event.member_id,material_version:selected.material_version}):latest;
    if(token!==renderToken)return;
    const d=decisions(snapshot.event).find(d=>d.id===current.id&&(!selected||d.version===selected.version));if(!d)return;
    const historical=d.version!==current.version,record=research.items.find(r=>r.kind==='handling'&&r.id===d.id&&r.notice_version===d.version);
    researchForm=historical?null:{kind:'handling',eventId:current.event.member_id,snapshot:latest,record,id:d.id};
    const state=record?.state||(historical?null:handlingState(d)),linked=objects(snapshot.event),noticeLog=notifications.items.filter(n=>n.id===d.id&&n.version<=d.version);
    const firstTime=noticeLog.find(n=>n.version===1)?.created||versions.find(v=>v.version===1)?.saved_at;
    const updatedAt=noticeLog.find(n=>n.version===d.version)?.created||selected?.saved_at;
    const ruleReason=d.rule_version==='competition-rules-20260923-v1'?['中高影响且所有绑定前提有适用原始材料支持','中高影响但前提有冲突、反驳或原始依据缺口']:[];
    const specific=(d.reasons||[]).filter(reason=>!ruleReason.includes(reason));
    const reasonText=specific.length?specific:d.reasons?.length?d.reasons:['该次判断未记录具体预警原因。'];
    const disclosure=updateTime(snapshot.event);
    const archiveLink=`#/events/${encodeURIComponent(current.event.member_id)}?material_version=${snapshot.key.material_version}`;
    $('.alert-detail').innerHTML=`<div class="detail-title"><h2>预警详情</h2><a class="text-button" href="${archiveLink}">查看事件档案 ›</a></div>
      <div class="alert-detail-hero ${levelTone(d.level)}"><span class="priority-flag ${levelTone(d.level)}" title="${esc(levelLabel(d.level))}">${esc(({高:'高',中:'中等',低:'低'})[d.level]||'待定')}</span><div><h3>${esc(d.target||'对象待明确')}</h3><span class="small">${esc(levelLabel(d.level))}</span></div>${badge(!d.active?alertInactive(d):state?handlingLabels[state]:'未记录处置',!d.active||!state?'gray':handlingTone[state])}</div>
      ${linked.length?`<div class="detail-object-tags">${linked.map(o=>`<span>关联 ${esc((o.code?o.code+' ':'')+o.name)}</span>`).join('')}</div>`:''}
      <section class="detail-evidence"><h3>预警原因</h3><div>${reasonText.map(reason=>`<p>${esc(explanationText(reason))}</p>`).join('')}<a class="text-button" href="${archiveLink}">查看判断依据 ›</a></div></section>
      ${alertRuleDetails(d,snapshot.event)}
      <section class="detail-missing"><h3>待核事项</h3><div>${(d.missing||[]).length?`<ol>${d.missing.map(x=>`<li>${esc(x)}</li>`).join('')}</ol>`:'<p>该次判断未记录额外材料缺口。</p>'}</div></section>
      <div class="alert-version-bar"><label for="real-alert-version">查看判断</label><select class="select" id="real-alert-version" aria-label="预警版本">${versions.map(v=>`<option value="${v.version}" ${v.version===d.version?'selected':''}>${v.version===1?'首次提醒':'第'+v.version+'次判断'}${v.version===current.version?' · 最新':' · 历史'}</option>`).join('')}</select>${historical?'<span class="historical-label">历史只读</span>':''}</div>
      <details class="alert-metadata"><summary>时间与关联材料</summary><dl class="detail-list alert-facts"><dt>触发时间</dt><dd><time data-alert-time="first">${esc(alertTime(firstTime))}</time><small>首次生成 · 北京时间 · 系统记录</small></dd>${d.version>1?`<dt>本次更新</dt><dd><time data-alert-time="update">${esc(alertTime(updatedAt))}</time></dd>`:''}<dt>依据披露日</dt><dd data-alert-disclosure>${esc(day(disclosure)||'未提供')}</dd><dt>关联材料</dt><dd>${snapshot.sources.nodes.length} 篇报道 · ${(snapshot.event.credibility?.claims||[]).length} 项主张 <span class="alert-credibility">提醒生成时可信度 <b>${snapshot.event.credibility?.score==null?'待评分':esc(snapshot.event.credibility.score)}</b>${snapshot.event.credibility?.score==null?'':' / '+scoreMaximum(snapshot.event.credibility)}</span></dd></dl></details>
      <div class="handling-form"><div class="detail-title"><h3>跟进处置</h3>${btn('处置历史','research-history',d.id,'text-button')}</div>${historical?'<p class="history-readonly">正在查看历史提醒。切回最新判断后可记录处置，旧记录会保留。</p>':handlingFields(record)}${!historical&&impactV3(d)?btn(value('mute',current.event.member_id)?'恢复本事件通知':'静音本事件通知','mute-alert',current.event.member_id,'text-button'):''}</div>
      <div class="notification-log"><h3>站内通知 <small>${noticeLog.length} 条记录</small></h3>${noticeLog.map(n=>`<div class="notification-record"><span class="notification-dot"></span><span><strong>${n.version===1?'首次提醒':'第'+n.version+'次判断更新'}</strong><time>${esc(alertTime(n.created))}</time></span>${badge(n.read_at?'已读':'未读',n.read_at?'green':'blue')}</div>`).join('')||'<p>本次没有已保存的通知记录。</p>'}<details class="alert-audit"><summary>保存记录</summary><p>提醒保存：${esc(date(selected?.saved_at))}</p><p>规则：${esc(d.rule_version||'未提供')}</p><p>材料保存记录：${snapshot.key.material_version}</p><p>通知已读与处置状态分别保存。</p></details></div>`;
  }


  async function render(){
    if(!status)return;const token=++renderToken,raw=route(),path=raw.split('?')[0],page=path.startsWith('/events')?'events':path.slice(1);
    nav(page);$('#demo-state-label').textContent=status.run.title;$('#notification-count').textContent=0;api('notifications').then(n=>{if(n.revision===status?.run?.revision)$('#notification-count').textContent=n.unread;}).catch(()=>{});
    $('.footer').innerHTML='<span>见微 · 事件研究工作台</span>';
    try{
      if(page==='events'){
        const id=path.split('/')[2]||defaultEvent()?.member_id;
        if(id){const params=new URLSearchParams(raw.split('?')[1]||'');const snapshot=await api('dossier',{id,...(params.has('material_version')?{material_version:params.get('material_version')}:{})});if(token!==renderToken)return;currentDossier=snapshot;}
        else currentDossier=null;
      }else currentDossier=null;
      if(page==='following'&&followTab==='events'){
        const revision=run+'|'+status.run.revision;if(followDetailsRevision!==revision){followDetails.clear();followDetailsRevision=revision;}
        await Promise.all(events.filter(e=>value('follow',e.member_id)&&(!followDetails.has(e.member_id)||followDetails.get(e.member_id).follow_detail_unavailable)).map(async e=>{try{const d=await api('dossier',{id:e.member_id});if(token===renderToken&&revision===followDetailsRevision)followDetails.set(e.member_id,d.event);}catch{if(token===renderToken&&revision===followDetailsRevision)followDetails.set(e.member_id,{...e,follow_detail_unavailable:true});}}));
      }
      if(token!==renderToken)return;
      $('#main').innerHTML=page==='events'?archive(path.split('/')[2]):page==='following'?following():page==='alerts'?alerts():radar();$('#main').dataset.run=run;if(page==='alerts')await alertDetail(token);
    }catch(error){if(token===renderToken)$('#main').innerHTML=empty('无法打开档案',error.message)+btn('返回事件雷达','go-radar');}
  }
  function config(){
    if(status?.news_input_enabled){drawer('新闻与资料',newsForm()+uploadPanel()+importedNews()+`<div class="space-top">${btn('浏览新闻库原文','materials')}</div><details class="space-top"><summary>案例回放</summary>${(status.cases||[]).map(c=>`<article class="input-record"><strong>${esc(c.title)}</strong><p class="small muted">${esc(c.description)}</p>${btn('载入初始状态','case-start',c.id)}</article>`).join('')}</details>`);return;}

    if(showcase()){drawer('重点案例与资料',`${(status.cases||[]).map(c=>`<article class="material-row"><h3>${esc(c.title)}</h3><p>${esc(c.description)}</p>${btn('载入初始状态','case-start',c.id)}</article>`).join('')||'<p>案例正在整理，全部新闻与影响分析已可浏览。</p>'}${status.case_session?`<p>已加入 ${status.case_session.step} / ${status.case_session.total} 份材料</p>${btn('加入下一份材料','case-next')}${btn('重新回放','case-reset')}`:''}<p><a href="/?mode=real&run=training-showcase">返回事件雷达</a></p>${btn('浏览全部原文','materials')}`);return;}

    if(['competition-cases','competition-mt'].includes(run)&&status){
      drawer('数据与规则',panel('保存的真实处理结果',`<p>本批 ${status.input_rows} 篇材料，生成 ${events.length} 个事件档案；原文、失败和待核结果均保留。</p><p>新闻采用两次以内的分析流程，已保存事项可在四页查看，历史版本和原文可追溯。</p><p>证据按材料进入工作区的时间限制；发布时间单独保留。保存时间不等于事件发生时间。</p>${btn('浏览全部新闻','materials')}<p><a href="/competition.html">返回参赛入口</a></p>`));return;
    }
    if(!status)return;const daily=new Map();for(const r of status.daily){const key=r.day||'未知日期',n=daily.get(key)||{train:0,test:0,protected:0};n[r.split]+=r.rows;if(r.guard==='protected')n.protected+=r.rows;daily.set(key,n);}
    drawer('数据与规则',`<div class="demo-controller stack"><label class="stack">当前运行<select id="real-run" class="select">${status.runs.map(r=>`<option value="${r.id}" ${r.id===run?'selected':''}>${esc(r.title)}</option>`).join('')}</select></label>${run==='may-replay'?`<label class="stack">回放至（不含，北京时间）<input id="real-cutoff" class="select" type="datetime-local" min="2026-05-26T00:00" max="2026-06-01T00:00" value="${localCutoff(status.run.cutoff)}"></label>${btn('应用回放时点','cutoff','','primary')}<p class="small muted">26—28 日作历史背景，29 日为首个展示日。30—31 日须推进时间后才能查看。只有日期的材料在当天结束后可见；发布时间代理不等于真实采集时间。</p>`:run==='eng061'?'<p>当前是 ENG-061 保存结果的固定诊断快照，保留原始日期。</p>':'<p>固定官方材料集。历史批量与新增单篇共用处理入口；每一步按材料发布时间限制证据范围。</p>'}</div><div class="space-top stack"><p>输入 ${status.input_rows.toLocaleString()} 条；已到时点 ${status.released_rows.toLocaleString()} 条；可查原文 ${status.readable_rows.toLocaleString()} 条。</p><p>导入状态：${esc(status.metadata.state||'尚未导入')}；识别状态：${run==='may-replay'?(events.length?'已有待核验候选':(status.extraction_reviews||[]).length?'已有抽取返回，严格未通过，待审阅':'未执行'):'已有局部结果，覆盖未证实'}。</p>${btn('浏览可见新闻','materials')}${btn('返回模拟演示','demo')}</div><h3 class="space-top">两份无标签文件的日期分布</h3><div class="table-container space-top"><table class="data-table"><thead><tr><th>日期</th><th>train</th><th>test</th><th>受保护</th></tr></thead><tbody>${[...daily].sort().map(([d,n])=>`<tr><td>${esc(d)}</td><td>${n.train.toLocaleString()}</td><td>${n.test.toLocaleString()}</td><td>${n.protected.toLocaleString()}</td></tr>`).join('')}</tbody></table></div><p class="small muted space-top">train/test 保留原始来源身份；输入行数不是唯一事件数。共享目录只使用官方已有公司、行业及监测定义，尚未补全全市场关系。</p>`);
  }
  async function materials(query=materialQuery,page=1){
    materialQuery=query;materialPage=page;const token=generation,data=await api('materials',{q:query,page});if(token!==generation)return;if(data.revision!==status.run.revision){await load();throw Error('运行时点已变化，请重新打开新闻列表');}
    drawer('新闻原文',`<label class="search-box"><input id="real-material-query" aria-label="搜索新闻原文" value="${esc(query)}" placeholder="搜索标题、正文或原始编号"></label><div class="space-top inline">${btn('搜索','material-search')}<span class="small muted">${data.total.toLocaleString()} 条可见材料；第 ${data.page} 页</span></div><div class="stack space-top">${data.rows.map(r=>`<div class="material-row"><button class="event-title text-button" data-real="document" data-id="${esc(r.id)}">${esc(r.title||'无标题')}</button><p class="small muted">${esc(materialDate(r))} · ${esc(r.split||'历史结果原文')}</p></div>`).join('')||'<p>当前范围没有匹配材料；不代表没有事件。</p>'}</div><div class="inline space-top">${page>1?btn('上一页','material-prev'):''}${page*20<data.total?btn('下一页','material-next'):''}</div>`);
  }
  function extractionReview(r){
    const issueList=issues=>issues.map(x=>`<li>${esc(({enum:'匹配值未按约定格式返回，不能直接采用',min_items:'匹配记录缺少字段，不能猜测补齐',outside_selected_evidence:'字段用词与所选原文不完全一致，需核对'})[x.code]||x.message)}<details><summary>查看问题位置与检查详情</summary><code>${esc(x.field)} · ${esc(x.code)}</code><p>${esc(x.message)}</p></details></li>`).join('');
    const mode={asserted:'材料陈述',planned:'计划',conditional:'条件触发',forecast:'预测'},context={focus:'本篇事项',background:'历史或背景'};
    return `<section class="extraction-review space-top" aria-label="抽取失败审阅"><h3>${r.status==='returned_strict_passed_pending_review'?'本次抽取：字段检查通过，事实仍待核验':'本次抽取：已返回，严格校验未通过'}</h3><p>模型提出 ${r.original_count??'未知数量的'} 条事项；字段引文警告 ${r.field_issue_count} 处；定义匹配格式问题 ${r.matching_issue_count} 处。</p><p class="drawer-note">仅供审阅或作为待核验候选，未发布可信结论或预警。没有警告不代表事实已经核实；下方保留原答序号，原文高亮只证明位置。</p><p class="small muted">审阅保存时间：${esc(date(r.saved_at))}（工程操作时间，不是新闻发生时间）</p>${r.items.map(item=>{
      const a=item.original&&typeof item.original==='object'?item.original:{},issues=[...item.field_issues,...item.matching_issues];
      return `<details class="review-item"><summary>原答事项 [${item.original_index}] · ${esc(a.summary||'无法读取摘要')} <span>${issues.length?'有检查问题':'语义待复核'}</span></summary><p class="small muted">原答位置：${esc(item.answer_pointer)} · 保留模型原始分类</p><p>状态：${esc(a.status?.phase||'未提供')} · ${esc(mode[a.status?.mode]||a.status?.mode||'未提供')} · ${esc(context[a.status?.context]||a.status?.context||'未提供')}</p>${issues.length?`<ul class="review-issues">${issueList(issues)}</ul>`:'<p>未发现本项格式或字段定位警告；语义与定义适用性未确认。</p>'}<h4>本事项引用的原文</h4>${item.evidence.map(q=>`<blockquote>${esc(q.quote||'来源单元不存在')}<p class="small muted">${esc(q.ref)} ${q.located?`${q.field==='title'?'标题':'正文'}字符 ${q.start}—${q.end}`:'无法可靠定位，不提供高亮'}</p>${q.located?btn('在原文定位','review-quote',`${q.field}:${q.start}:${q.end}`):''}</blockquote>`).join('')}<details><summary>查看此事项的原始字段</summary><pre class="review-json">${esc(JSON.stringify(a,null,2))}</pre></details></details>`;
    }).join('')}${r.unassigned_issues.length?`<h4>无法归属具体事项的问题</h4><ul>${issueList(r.unassigned_issues)}</ul>`:''}<details class="space-top"><summary>查看完整模型原答（未修复）</summary><pre class="review-json">${esc(r.raw_answer)}</pre></details></section>`;
  }
  function modelRecord(r){
    const a=r.admission;
    const derived=(r.projection_derivations||[]).length?`<details class="space-top"><summary>本地补齐了引用索引，模型原答未改写</summary><p>只汇总原答中已有的核心与字段引用，未新增事实；补齐后仍执行原文检查。</p><pre class="review-json">${esc(JSON.stringify(r.projection_derivations,null,2))}</pre></details>`:'';
    const retained=(r.core_candidates||[]).map(c=>`<article class="review-item"><h4>${esc(c.summary)}</h4><p>${esc((c.scope?.matches||[]).map(m=>[m.definition?.target_name,m.definition?.event_name||m.definition?.definition_id].filter(Boolean).join(' · ')).join('；'))}</p><p>核心识别：已通过引用与匹配检查，尚非事实核实。补充要素：${c.enrichment_status==='ready'?'已通过检查':'未完成，核心结果保留'}。</p>${(c.emotions||[]).map(e=>`<p>情绪 · ${esc(emotionDefinition(definitions({monitoring_scope:c.scope}),e)?.target_name||'对象未明确')}：${esc(e.label)} <span class="small muted">${esc(e.reason)}</span></p>`).join('')||'<p>情绪：未判断。</p>'}${!c.downstream_eligible?(c.enrichment_status==='ready'?'<p>监测定义匹配尚待核实，暂不用于自动合并或提醒。</p>':'<p>缺少合格补充要素，暂不用于自动合并或提醒。</p>'):''}${c.enrichment_reason?`<details><summary>补充字段问题</summary><pre class="review-json">${esc(c.enrichment_reason)}</pre></details>`:''}</article>`).join('');
    const coverage=(r.coverage||[]).map(p=>`<p>分块 ${p.block} · 正文字符 ${p.core.start}–${p.core.end}：${esc(stateText[p.status]||p.status)}；准入 ${p.accepted} 项，隔离 ${p.rejected} 项。${p.error?'原因：'+esc(p.error):''}</p>`).join('');
    const admission=coverage?`<section class="space-top"><h3>识别处理范围</h3>${coverage}<p class="small muted">失败或隔离范围不能按正确否定计；通过引用检查仍不代表语义正确或没有遗漏。</p></section>`:a?`<section class="extraction-review space-top"><h3>逐条检查结果</h3><p>${(a.accepted_original_indices||[]).length} 条可继续判断监测范围；${(a.isolated_original_indices||[]).length} 条有问题，单独保留。</p><p class="small muted">这只是字段和引用检查。原来的整篇检查记录保留；可继续处理不等于已经核实。</p>${(a.items||[]).map(i=>`<details class="review-item"><summary>原答事项 [${i.original_index}] · ${esc(i.original_named_event?.summary||'查看此事项')} · ${i.accepted?'可继续处理':'有问题，未采用'}</summary><pre class="review-json">${esc(JSON.stringify(i.checks||i.error||{core_supported:i.core_supported},null,2))}</pre></details>`).join('')}</section>`:'';
    return (r.unmatched_reason?`<p>${esc(r.unmatched_reason)}</p>`:'')+(retained?`<section class="space-top"><h3>已保留的核心识别</h3>${retained}</section>`:'')+admission+derived+`<details class="space-top"><summary>${esc(stages[r.stage])} · ${esc(stateText[r.status])} · 查看原答与原检查</summary><pre class="review-json">${esc(r.raw_answer||'本次未保存可读取的模型原答')}</pre><pre class="review-json">${esc(JSON.stringify(r.checks,null,2))}</pre></details>`;
  }
  async function documentView(id){
    const token=generation,d=await api('document',{id});if(token!==generation)return;if(d.run_revision!==status.run.revision){await load();throw Error('运行时点已变化，请重新打开原文');}
    let reference;try{reference=showcase()?{available:false,items:[]}:await api('reference',{id});}catch(error){reference={available:false,items:[]};}if(token!==generation)return;
    const official=reference.available?`<details class="space-top"><summary>展开官方参考答案对照</summary><p>${esc(reference.notice)}</p>${reference.items.map(x=>`<h4>${esc(x.title)}</h4><dl>${Object.entries(x.labels).map(([key,value])=>`<dt>${esc(({industry_name:'行业名称',industry_code:'行业代码',event_name:'事件名称',event_emotion:'事件情感',event_impact_analysis:'官方影响分析全文',secu_abbr:'公司简称',trading_code:'证券代码'})[key]||key)}</dt><dd class="real-source-text">${esc(value??'未提供')}</dd>`).join('')}</dl>`).join('')}</details>`:'';
    d.processing=orderedStages(d.processing||[]);openedDocument=d;
    const u=new URL(location.href);u.searchParams.set('material',id);history.replaceState(null,'',u);
    const historical=!!currentDossier?.historical;
    const hasDetails=!historical&&((d.processing||[]).length||(d.extraction_reviews||[]).length||(d.model_records||[]).length||(d.processing_reviews||[]).length);
    const matters=!historical&&(d.matter_outcomes||[]).length?`<section class="space-top" id="material-matters"><h4>本篇事项与去向</h4>${d.matter_outcomes.map(m=>`<article class="review-item"><strong>${esc(m.summary)}</strong><p>${badge(m.label,m.code==='archived'?'blue':m.code==='background'?'gray':'orange')}</p><p>${esc(m.reason)}</p>${m.event_id?`<a class="text-button" href="#/events/${encodeURIComponent(m.event_id)}">查看对应事件 →</a>`:''}</article>`).join('')}</section>`:'';
    const details=hasDetails?`<details class="space-top processing-details"><summary>处理详情</summary>${(d.processing||[]).map(p=>`<p>${esc(stages[p.stage]||p.stage)}：${esc(stateText[p.status]||p.status)}</p><details><summary>查看原因</summary><p>${esc(p.reason)}</p></details>`).join('')}${(d.extraction_reviews||[]).map(extractionReview).join('')}${(d.model_records||[]).map(modelRecord).join('')}${(d.processing_reviews||[]).map(r=>`<details><summary>${r.status==='rejected'?'未采用的答复':r.status==='partial'?'部分采用的答复':'已采用的答复'} · 查看校验记录</summary><p>${esc(r.reason||'通过本次字段和引用检查')}</p><pre class="review-json">${esc(r.body)}</pre></details>`).join('')}</details>`:'';
    drawer('原文回查',`${historical?'<p class="drawer-note">正在回查该历史节点的材料，处理详情以当时档案为准。</p>':''}<h3 id="review-original-title" tabindex="-1">${esc(d.title||'未填标题')}</h3><p class="small muted space-top">${esc(materialDate(d))} · ${esc(d.source||'来源未提供')}</p>${d.source_url?`<p><a href="${esc(d.source_url)}" target="_blank" rel="noopener noreferrer">打开来源原页</a></p>`:''}${!historical&&d.outcome?`<p class="material-outcome" role="status">${esc(d.outcome.label)}</p>${d.outcome.reason?`<details><summary>查看材料判断依据</summary><p>${esc(d.outcome.reason)}</p></details>`:''}`:''}${matters}<h4>原文正文</h4><div id="review-original-text" tabindex="-1" class="real-source-text space-top">${esc(d.text||'正文缺失')}</div>${d.duplicate_relation?`<p>${esc(d.duplicate_relation.status)} ${d.duplicate_relation.target?btn('查看关联原文','document',d.duplicate_relation.target):''}</p>`:''}${official}${details}<p class="small muted space-top">${d.source_kind?'材料类别：'+esc(sourceKind(d.source_kind)):d.path?'来源文件：'+esc(d.path)+'；工作簿行号 '+d.row_number:'与当前档案绑定的原文'}</p>${btn('返回新闻列表','materials')}`,true);
  }
  async function change(kind,target,data){
    if(busy)return;busy=true;
    try{await api('action',{}, {run,revision:status.run.revision,kind,target,value:data});await load();toast('已保存到当前运行');}
    catch(error){toast(error.message);await load();}finally{busy=false;}
  }
  async function handle(event){
    const el=event.target.closest('[data-real],[data-action]');if(!el)return;const action=el.dataset.real||el.dataset.action,id=el.dataset.id;
    try{
      if(action==='close-drawer'){close();return;}if(action==='reload'){await load();return;}if(!status){toast('请先恢复本地运行连接');return;}
      if(action.startsWith('case-')){
        if(busy)return;busy=true;el.disabled=true;
        try{const result=await api('case',{}, {run,revision:status.run.revision,operation:({'case-start':'start','case-next':'next','case-reset':'reset'})[action],case_id:id});run=result.run;const u=new URL(location.href);u.searchParams.set('run',run);u.searchParams.delete('material');u.hash='/radar';history.replaceState(null,'',u);close();await load();toast(result.complete?'全部材料已加入':'已更新案例');}finally{busy=false;el.disabled=false;}
      }
      else if(action==='related-only'){filters.related=!filters.related;filters.page=1;render();}
      else if(action==='timeline-day'){
        const selected=$('#timeline-day').value;if(!selected)throw Error('请选择具体日期');
        const latest=await api('dossier',{id}),nodes=(latest.navigation?.timeline_nodes||latest.timeline_nodes||[]).filter(n=>day(n.time)<=selected).sort((a,b)=>String(a.time).localeCompare(String(b.time))||a.material_version-b.material_version);
        if(!nodes.length)throw Error('该日期之前尚无已记录的业务进展');
        location.hash='/events/'+encodeURIComponent(id)+'?material_version='+nodes.at(-1).material_version+'&day='+selected;
      }
      else if(action==='event-materials'){
        const d=await api('dossier',{id});drawer('关联报道与聚合依据',`<p>${esc(eventTitle(d.event))}</p><p>同事件同阶段的报道放在一个节点内；新进展增加节点，不同事项保持分开。转载不增加独立证据，也不重复预警。</p>${(d.event.evidence_sources?.members||[]).map(m=>`<article class="material-row"><strong>${esc(m.title)}</strong><p>${esc(m.source||'来源未提供')} · ${esc(sourceDate(m))}</p>${btn('查看原文','document',m.source_row)}</article>`).join('')}${sourceGraph(d.sources)}`);
      }
      else if(action==='score-info'){
        const e=currentDossier?.event?.member_id===id?currentDossier.event:(await api('dossier',{id})).event,c=e.credibility||{};
        drawer('可信度与评分依据',`<h3>${esc(scoreText(c))}</h3><p>${esc(c.short_reason||c.score_scope||'尚未完成评分')}</p><p class="small muted">${esc(c.score_status||'沿用该历史版本的核查状态')} · 规则上限 ${scoreMaximum(c)}分</p><details class="score-breakdown"><summary>展开事件加减项与上限</summary>${scoreBreakdown(c)}</details><p class="small muted">${esc(c.score_scope||'')}</p>${(c.claims||[]).map(q=>`<article class="material-row"><strong>${esc(q.claim)}</strong><p>${esc(scoreText(q))} · ${esc(q.score_role||'该版本主张')}</p><p>${esc(q.short_reason||q.verdict||'当时未保存评分说明')}</p>${q.score_status?`<p class="small muted">核查状态：${esc(q.score_status)}</p>`:''}${q.basis?.quote?`<blockquote>${esc(q.basis.quote)}</blockquote>`:''}<details><summary>本条加减项与证据</summary>${scoreBreakdown(q)}</details></article>`).join('')}<p class="small muted">规则版本：${esc(c.score_rule||'历史版本未记录')}<br>评估时间：${esc(c.assessed_at?date(c.assessed_at):'未记录')}</p><p class="small muted">这是材料支持程度的规则指数，不是真实性概率。影响大小与预警另按既有规则判断。</p>`);
      }
      else if(action==='review-tasks'){reviewTasks();return;}
      else if(action==='mute-alert'){await change('mute',id,!value('mute',id));}
      else if(action==='watch-rules'){
        const e=followDetails.get(id)||eventBy(id),gaps=e?[...new Set(decisions(e).flatMap(d=>d.missing||[]))]:[];
        drawer('跟进依据与提醒规则',`${e?`<h3>${esc(eventTitle(e))}</h3><h4 class="space-top">待验证事项</h4>${gaps.length?bullets(gaps):'<p>当前没有保存的具体材料缺口，可关注后续实质进展。</p>'}`:''}<h3 class="space-top">当前提醒规则</h3><p>普通转载只增加材料，不重复产生提醒。实质变化形成新版本后，关注记录显示未读。</p><p>新规则按事件性质、对象范围、业务阶段和时间敏感性计算影响。黄色进入关注，橙色和红色按具体依据提醒；缺少核心依据的重要线索进入待核。转载不重报，关键进展、恢复和更正可再次提醒。历史判断保留当时规则。</p><p>内部复查日期在研究待办中设置，不代表新闻披露日期。</p>`);
      }
      else if(action==='source-relations'){
        const rows=JSON.parse(id),nodes=currentDossier?.sources?.nodes||[],title=row=>nodes.find(m=>m.source_row===row)?.title||'关联报道';
        drawer('报道关联依据',rows.map(r=>`<article class="material-row"><strong>${esc(r.operation_label||relationLabels[r.relation]||r.relation)}</strong><p>${esc(title(r.material_a))}<br>${esc(title(r.material_b))}</p><p>${esc(r.reason||r.claim||'')}</p>${btn('查看原文依据','source-relation',JSON.stringify(r),'compact')}</article>`).join(''));
      }
      else if(action==='source-relation'){
        const r=JSON.parse(id),nodes=currentDossier?.sources?.nodes||[],title=row=>nodes.find(m=>m.source_row===row)?.title||'查看关联原文';
        drawer('材料关系依据',`${badge(r.decision_status?.startsWith('pending')?'关联待核':r.operation_label||relationLabels[r.relation]||r.relation,'blue')}<p class="space-top">${esc(r.reason||r.claim||'尚未记录具体依据')}</p>${r.quote?`<blockquote>${esc(r.quote)}</blockquote>`:''}${(r.proofs||[]).map(q=>`<blockquote>${esc(q.quote)}</blockquote>`).join('')}<div class="stack space-top">${btn(title(r.material_a),'document',r.material_a)}${btn(title(r.material_b),'document',r.material_b)}</div><p class="drawer-note">这条关系不自动证明传播方向或来源独立性。</p>`);
      }
      else if(action==='versions')drawer('历史版本',snapshotVersions(currentDossier.event));
      else if(action==='config')config();
      else if(['settings','market','account'].includes(action))settings();
      else if(action==='settings-save'||action==='settings-reset'){
        if(action==='settings-save')localStorage.setItem('zhiyan-display:'+run,JSON.stringify({order:$('#setting-order').value,scope:$('#setting-scope').value,size:Number($('#setting-size').value)}));else localStorage.removeItem('zhiyan-display:'+run);
        Object.assign(filters,displayPreferences(),{page:1});await render();settings();toast(action==='settings-save'?'设置已保存':'已恢复默认浏览设置');
      }
      else if(action==='settings-following'){close();location.hash='/following';}
      else if(action==='radar-tab'){filters.tab=id;filters.page=1;location.hash='/radar';render();}
      else if(action==='radar-scope'){filters.scope=id;filters.object='';filters.page=1;render();}
      else if(action==='radar-object'){if(!filters.object)radarReturn={...filters};Object.assign(filters,{from:'',to:'',related:false,scope:'all',tab:'all',category:'',sector:'',impact:'',emotion:'',object:id,query:'',page:1});$('#global-search').value='';render();}
      else if(action==='radar-page'){filters.page=Number(id);render();}
      else if(action==='radar-return'){Object.assign(filters,radarReturn||{object:'',page:1});radarReturn=null;$('#global-search').value=filters.query;render();}
      else if(action==='clear-radar'){radarReturn=null;Object.assign(filters,{from:'',to:'',related:false,scope:'all',tab:'all',category:'',sector:'',impact:'',emotion:'',object:'',query:'',page:1});$('#global-search').value='';render();}
      else if(action==='go-radar')location.hash='/radar';
      else if(action==='object-scope'){objectScope=id;render();}
      else if(action==='object-select'){selectedObject=id;render();}
      else if(action==='impact-object'){selectedImpact=id;render();}
      else if(action==='impact-details'){const selectedRoute=route();selectedImpact=id;await render();if(route()===selectedRoute)document.getElementById('real-impact')?.scrollIntoView({block:'start'});}
      else if(action==='section'){document.querySelectorAll('.section-tabs .tab').forEach(t=>t.classList.toggle('active',t.dataset.id===id));document.getElementById(id)?.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'auto':'smooth',block:'start'});}
      else if(action==='follow-search'){followQuery=$('#real-follow-query').value.trim();render();}
      else if(action==='alert-filter'){alertFilter=id;render();}
      else if(action==='alert-clear'){alertFilter='all';alertPriority='';selectedAlertVersion=null;render();}
      else if(action==='alert-select'){selectedAlert=id;selectedAlertVersion=null;await render();if(matchMedia('(max-width:1100px)').matches){$('.alert-detail')?.scrollIntoView({block:'start'});$('.alert-detail')?.focus({preventScroll:true});}}
      else if(action==='history'){const e=eventBy(id);drawer('判断变化与历史',`<h3>${esc(eventTitle(e))}</h3><p>${esc(changeText(e))}</p><p class="small muted">这里只展示保存的判断记录，不冒充完整历史档案回放。</p>${judgmentHistory(e)||'<p>尚无旧判断记录。</p>'}`);}
      else if(action==='claim'){const [eid,index]=id.split('|');const c=(currentDossier?.event?.member_id===eid?currentDossier.event:eventBy(eid))?.credibility?.claims?.[Number(index)];if(c)drawer('主张与证据',judgmentClaims([c]));}
      else if(action==='review-open'||action==='task-new'||action==='task-edit'||action==='handling-open')await openResearch(action,id);
      else if(action==='research-save')await saveResearch();
      else if(action==='task-state'){
        const r=research.items.find(x=>x.kind==='task'&&x.id===id);if(!r)throw Error('待办不存在');const d=await api('dossier',{id:r.event_id});
        await api('research',{}, {run,revision:status.run.revision,event_id:r.event_id,kind:'task',id:r.id,record_revision:r.revision,snapshot_key:d.key,value:{title:r.title,review_at:r.review_at,state:r.state==='completed'?'pending':'completed'}});await load();
      }
      else if(action==='research-history'){const list=research.history.filter(x=>x.id===id);drawer('操作历史',list.map(r=>`<article class="material-row"><strong>${esc(date(r.operated_at))}</strong><p>${esc(r.operator||r.owner||'个人待办')} · ${esc(handlingLabels[r.state]||r.state||direction(r.result))}</p><p>${esc(r.note||r.reason||r.title)}</p><p class="small muted">事件 v${esc(r.snapshot_key.event_version)} / 材料 v${esc(r.snapshot_key.material_version)} · 记录修订 ${r.revision}</p></article>`).join(''));}
      else if(action==='notice-history'){
        const notice=notices().find(d=>d.id===id);const snapshot=await api('dossier',{id:notice.event.member_id});
        drawer('预警版本与关联快照',snapshot.reminder_versions.filter(v=>v.id===id).map(v=>`<article class="material-row"><strong>提醒 v${v.version} · ${v.active?'当时发布':'当时撤回'}</strong><p>${esc(date(v.saved_at))} · 系统保存时间</p><a class="button" href="#/events/${encodeURIComponent(notice.event.member_id)}?material_version=${v.material_version}">查看该版依据快照</a></article>`).join('')||'<p>尚未找到完整保存快照，不用最新档案冒充触发依据。</p>');
      }
      else if(action==='snapshot-diff'){
        const before=await api('dossier',{id:currentDossier.key.event_id,material_version:id}),after=currentDossier;
        drawer('版本对照',`<h3>所选旧版：事件v${before.key.event_version} / 材料v${before.key.material_version}</h3><p>${esc(before.event.event_summary?.text)}</p>${badge(before.event.credibility?.status||'未核验')}<h3 class="space-top">当前查看：事件v${after.key.event_version} / 材料v${after.key.material_version}</h3><p>${esc(after.event.event_summary?.text)}</p>${badge(after.event.credibility?.status||'未核验')}<p>材料数 ${before.sources.nodes.length} → ${after.sources.nodes.length}；主张数 ${(before.event.credibility?.claims||[]).length} → ${(after.event.credibility?.claims||[]).length}</p><p class="small muted">这是保存结果对照；版本增加不自动表示真实业务进展。</p>`);
      }
      else if(action==='input-page'){inputPage=Number(id);config();$('#news-inbox')?.scrollIntoView();}
      else if(action==='upload-save')await saveUpload();
      else if(action==='upload-clear'){upload=null;uploadResult=null;rememberInput();config();}
      else if(action==='input-template'){const blob=new Blob(['\ufeff标题,正文,来源,发布时间,原文链接\r\n'],{type:'text/csv;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='新闻导入模板.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
      else if(action==='news-save'||action==='news-save-process')await saveNews(action==='news-save-process');
      else if(action==='news-retry'){const task=(status.tasks||[]).find(t=>t.id===id);if(!task||el.disabled)return;el.disabled=true;try{rememberSubmission(await api('process',{}, {run,revision:status.run.revision,material_id:task.source_row,retry_of:id}));returnToImportRadar();await load();toast('已提交再次分析');}finally{el.disabled=false;}}
      else if(action==='news-process'){rememberSubmission(await api('process',{}, {run,revision:status.run.revision,material_id:id}));returnToImportRadar();await load();toast('已提交或复用已有任务');}
      else if(action==='notifications'){const n=await api('notifications');drawer('站内通知',`<p>未读 ${n.unread} 条。通知已读不关闭提醒，也不改变事件事实。</p>${n.items.map(x=>`<article class="material-row"><strong>${esc(x.notice.change||'提醒')} · v${x.version}</strong><p>${esc((x.notice.reasons||[]).join('；'))}</p><p>${esc(date(x.created))} · ${x.read_at?'已读':'未读'}</p>${!x.read_at?btn('标为已读','notification-read',x.id+'|'+x.version):''}<a class="button" href="#/events/${encodeURIComponent(x.event_id)}">打开事件</a></article>`).join('')||'<p>尚无已发布的站内通知；未核验材料不自动制造提醒。</p>'}${btn('打开预警中心','open-alerts')}`);}
      else if(action==='notification-read'){const [noticeId,version]=id.split('|');await api('notification-read',{},{run,revision:status.run.revision,id:noticeId,version:Number(version)});toast('此版本通知已读');close();await load();}
      else if(action==='open-alerts'){close();location.hash='/alerts';}
      else if(action==='demo')location.href=location.pathname+'#/radar';
      else if(action==='materials')await materials(materialQuery,1);
      else if(action==='material-search')await materials($('#real-material-query').value,1);
      else if(action==='material-prev')await materials(materialQuery,materialPage-1);
      else if(action==='material-next')await materials(materialQuery,materialPage+1);
      else if(action==='document')await documentView(id);
      else if(action==='proof'){
        const target=JSON.parse(id);await documentView(target.row);const proof=target.proof;if(!proof?.quote)return;
        const field=proof.section==='title'?'title':'text',chars=Array.from(openedDocument[field]||'');let start=proof.start,end=proof.end;
        if(!Number.isInteger(start)||chars.slice(start,end).join('')!==proof.quote){const text=chars.join(''),at=text.indexOf(proof.quote);if(at<0||text.indexOf(proof.quote,at+1)>=0){toast('引文位置无法唯一确认，请核对原文');return;}start=Array.from(text.slice(0,at)).length;end=start+Array.from(proof.quote).length;}
        const node=$(field==='title'?'#review-original-title':'#review-original-text');node.innerHTML=esc(chars.slice(0,start).join(''))+'<mark>'+esc(proof.quote)+'</mark>'+esc(chars.slice(end).join(''));node.querySelector('mark').scrollIntoView({block:'center'});node.focus({preventScroll:true});
      }
      else if(action==='review-quote'){
        const q=(openedDocument?.extraction_reviews||[]).flatMap(r=>r.items.flatMap(i=>i.evidence)).find(q=>q.located&&`${q.field}:${q.start}:${q.end}`===id);
        if(!q||Array.from(openedDocument[q.field]).slice(q.start,q.end).join('')!==q.quote)throw Error('位置无法确认，未高亮');
        const text=openedDocument[q.field],el=$(q.field==='title'?'#review-original-title':'#review-original-text');
        // Offsets use Python Unicode characters, not JavaScript UTF-16 code units.
        const chars=Array.from(text);el.innerHTML=esc(chars.slice(0,q.start).join(''))+'<mark>'+esc(q.quote)+'</mark>'+esc(chars.slice(q.end).join(''));el.querySelector('mark').scrollIntoView({block:'center'});el.focus({preventScroll:true});
      }
      else if(action==='cutoff'){const v=$('#real-cutoff').value;if(!v)throw Error('请选择有效时间');await change('cutoff','',v+':00+08:00');config();}
      else if(['follow','read','subscription','alert_closed'].includes(action))await change(action,id,action==='read'?true:!value(action,id));
      else if(action==='task-save')await change('task',id,{text:$('#real-task-text').value.trim(),done:false});
      else if(action==='task-toggle'){const v=value('task',id);await change('task',id,{...v,done:!v.done});}
      else if(action==='follow-tab'){followTab=id;render();}
      else if(action==='definition'){const o=status.catalog.find(o=>o.id===id);drawer(o.name+' · 监测定义',`<p class="small muted">${esc(o.source)} · 官方对象代码 ${esc(o.code)}</p>${o.definitions.map(d=>`<div class="material-row"><h3>${esc(d.event_name)}</h3><p>${esc(d.description)}</p></div>`).join('')}`);}
    }catch(error){toast(error.message);}
  }
  async function start(){
    restoreInput();
    const dataNav=document.querySelector('.sidebar [data-action="config"]');if(dataNav){dataNav.setAttribute('aria-label','新闻与资料');dataNav.querySelector('span:last-child').textContent='新闻与资料';}
    $('#main').innerHTML='<div class="loading">正在连接真实运行…</div>';$('.footer').textContent='正在读取本地运行库';$('#demo-state-label').textContent='连接真实运行…';
    document.body.classList.add('real-mode');document.querySelectorAll('[data-icon]').forEach(n=>n.innerHTML=icon(n.dataset.icon));
    $('.drawer .eyebrow').textContent='见微 · 真实运行';$('.account strong').textContent='本地研究员';$('.account small').textContent='研究工作台';
    $('.market-button').textContent='原始资讯';
    $('#global-search').placeholder='搜索个股 / 行业 / 代码 / 新闻标题…';$('#global-search').setAttribute('aria-label','搜索监测对象与事件');
    document.addEventListener('click',handle);
    document.addEventListener('input',event=>{if(event.target.dataset.newsField){newsDraft[event.target.dataset.newsField]=event.target.value;rememberInput();}});
    $('#drawer').addEventListener('close',close);
    document.addEventListener('change',async event=>{if(event.target.id==='news-file'){await previewFile(event.target.files[0]);return;}if(event.target.id==='real-alert-priority'){alertPriority=event.target.value;render();return;}if(event.target.id==='real-alert-status'){alertFilter=event.target.value;render();return;}if(event.target.id==='real-alert-version'){selectedAlertVersion=Number(event.target.value);render();return;}if(event.target.id==='real-replay-version'){location.hash='/events/'+encodeURIComponent(currentDossier.key.event_id)+(event.target.value?'?material_version='+event.target.value:'');return;}if(event.target.dataset.realFilter){const k=event.target.dataset.realFilter;filters[k]=k==='size'?Number(event.target.value):event.target.value;filters.page=1;render();return;}if(event.target.id==='real-watch-status'){followFilter=event.target.value;render();return;}if(event.target.id==='real-run'){run=event.target.value;restoreInput();materialQuery='';const u=new URL(location.href);u.searchParams.set('run',run);u.hash='/radar';history.replaceState(null,'',u);close();await load();}});
    document.addEventListener('keydown',event=>{if(event.key!=='Enter')return;if(event.target.id==='global-search'){filters.query=event.target.value.trim();filters.object='';filters.page=1;location.hash='/radar';render();}else if(event.target.id==='real-material-query')materials(event.target.value,1).catch(e=>toast(e.message));else if(event.target.id==='real-follow-query'){followQuery=event.target.value.trim();render();}});
    window.addEventListener('hashchange',async()=>{close();clearTimeout(timer);$('#toast').hidden=true;await render();window.scrollTo(0,0);});await load();
  }
  return {start};
})();


