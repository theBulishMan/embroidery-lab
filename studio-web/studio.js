import {inspectUpload, uploadRoute} from './upload-treatment.js';
import {Preview} from './preview.js';
import {SplitEditor} from './split-editor.js';
import {LocalEditor} from './local-editor.js';
import {ArtEditor} from './art-editor.js';
import {Wizard} from './wizard.js';
import {THREAD_PROFILES,referenceWidth,validThreadWidth} from './thread-profile.js';
import {reviewDetails, reviewRegion} from './detail-fit.js';
import {assessProduction, ASSESSMENT_RULES, measuredRegion, narrowBorderTrial, repairForRegion} from './production-assessment.js';
const $=selector=>document.querySelector(selector),$$=selector=>[...document.querySelectorAll(selector)];
const state={project:null,version:null,art:null,view:null,plan:null,thread:'105D',mode:'auto',upload:null,busy:false,loading:false,creating:false,backProject:null,load:0,hasPlan:false,job:null,resume:null,paletteDraft:null,paletteInvalid:false,comparisons:[]};
const fmt=n=>Number(n).toLocaleString('zh-CN',{maximumFractionDigits:1});
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let storagePrefix='';
const storage={getItem:k=>localStorage.getItem(storagePrefix+k),setItem:(k,v)=>localStorage.setItem(storagePrefix+k,v),removeItem:k=>localStorage.removeItem(storagePrefix+k)};
let replanTimer,localEditor=null,artEditor=null,wizard=null,splitEditor=null;
let draftKey='',draftInfo=null,draftBody=null,draftPrevious=null,manualDraft=null;
state.craftTool='split';
state.plateMode='rules';
let activePlanJob=null;
const plateNames={rules:'普通制版',ai:'AI 辅助制版'};
const nextModeKey=id=>'embroidery-next-mode:'+id;
const jobModeKey=id=>'embroidery-job-mode:'+id;
function trackPlanJob(job){
  if(job.kind!=='plan'||activePlanJob?.id===job.id)return;
  const saved=storage.getItem(jobModeKey(job.id));
  const mode=Object.hasOwn(plateNames,saved)?saved:state.plateMode;
  activePlanJob={id:job.id,project:job.project_id,mode};
  storage.setItem(jobModeKey(job.id),mode);
}
function processingPlan(){
  return !!activePlanJob&&activePlanJob.project===state.project?.id&&!state.loading&&(state.busy||!!state.resume);
}
let sizeAdvice=null,sizeAdviceKey='';
async function loadSizeAdvice(){
  sizeAdvice=null;const key=state.project.id+':'+state.version.id;sizeAdviceKey=key;
  $('#size-advice-title').textContent='正在分析色块大小…';$('#size-advice-text').textContent='';$('#apply-size-advice').hidden=true;
  const result=await api(`/api/projects/${state.project.id}/size-advice`,{version_id:state.version.id});
  if(sizeAdviceKey!==key)return;sizeAdvice=result;updateSizeAdvice();
}
function updateSizeAdvice(){
  updateDetailFit();
  if(!sizeAdvice)return;const p=sizeAdvice.profiles[state.thread],w=Number($('#width').value),d=sizeAdvice.details[0];
  $('#size-advice-title').textContent=p.over_limit?'部分细节不宜直接按面填绣':`全部按面绣保留的参考宽度：${p.width_mm} mm`;
  $('#size-advice').classList.toggle('size-warning',p.over_limit||w<p.width_mm);
  $('#size-advice-text').textContent=d?`最细的独立${d.color}色块约 ${(d.short_mm_at_60*w/60).toFixed(2)} mm。${p.detail_target_mm} mm 是面状细节的试排参考，不是所有针法的最小尺寸；细线可能改用走针保留。下一步按色块复核，不必整图删花纹。`:sizeAdvice.note;
  $('#apply-size-advice').hidden=p.over_limit||w>=p.width_mm;$('#apply-size-advice').disabled=locked();
}
function fitSummary(report){
  const counts=[[report.removed_parts,'处碎纹已归并'],[report.separated_motifs,'处相连花纹已分开'],
    [report.gap_adjusted_parts,'个色块已留宽分隔'],[report.strengthened_lines,'条原稿细线已保留并加宽'],[report.retained_parallel_marks,'条并排花纹已留出间隔'],[(report.omitted_motifs||[]).reduce((n,g)=>n+(g.count||0),0),'处明显花纹未保留，需复核']];
  const changes=counts.filter(([n])=>n>0).map(([n,label])=>n+label);
  return `${fmt(report.width)} mm · ${report.thread}，${changes.join('，')||'沿用原稿轮廓'}。`;
}
function updateDetailFit(){
  if(!localEditor)return;
  const list=$('#detail-fit-list');list.replaceChildren();
  if(localEditor.analysisPending){$('#detail-fit-summary').textContent='正在按新尺寸复核细节…';return}
  if(localEditor.analysisError){$('#detail-fit-summary').textContent='细节复核暂未更新：'+localEditor.analysisError;return}
  const report=reviewDetails(localEditor.regions,state.thread,localEditor.overrides);
  $('#detail-fit-summary').textContent=`${Number($('#width').value)} mm · ${state.thread}：${report.risk.length} 处局部风险，${report.adapt.length} 处可先调整针法。其余 ${report.normal.length} 区未触发尺寸提示。`;
  const fitted=state.version?.fit_report;
  $('#fit-version-note').textContent=fitted?fitSummary(fitted):'按当前尺寸检查花纹，改动会另存一版。';
  $('#detail-fit-toggle').textContent=`定位需要复核的部位（${report.inspect.length}）`;
  for(const item of report.inspect.slice(0,20)){
    const button=document.createElement('button');button.className='detail-fit-item';button.type='button';button.dataset.level=item.level;
    const title=document.createElement('strong');title.textContent=`${localEditor.colors?.[item.region.color]?.name||'色块'} · ${item.label}`;
    const note=document.createElement('span');note.textContent=item.reasons.join('；');button.append(title,note);
    button.disabled=locked()||localEditor.pending||!!localEditor.drawing||dirtyPalette();
    button.onclick=()=>focusProblem({id:item.region.id,color:item.region.color});list.append(button);
  }
  if(report.inspect.length>20){const note=document.createElement('p');note.className='muted note';note.textContent='先显示 20 处。其他部位可直接在图案上点选查看。';list.append(note)}
}
const densityPresets={'75D':{normal:[.20,.35],dense:[.18,.32],detail:[.20,.20]},'105D':{normal:[.22,.40],dense:[.20,.36],detail:[.22,.26]}};
async function api(path,body){
  const response=await fetch(path,body===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const result=await response.json();
  if(response.status===401){location.replace('/login');throw new Error('请重新登录。')}
  if(!response.ok){const error=new Error(result.error||'操作未完成，请重试。');error.status=response.status;throw error}
  return result;
}
function message(text,error=false){
  $('#job-message').textContent=text;$('#job-state').classList.toggle('error',error);
  const artFeedback=$('#art-feedback');
  artFeedback.hidden=!error||!$('#art-advanced-dialog').open;
  if(!artFeedback.hidden)artFeedback.textContent=text;
  const feedback=$('#create-feedback');feedback.hidden=!state.creating||!(state.busy||state.resume||error);
  feedback.classList.toggle('error',error);$('#create-status').textContent=text;
  $('#create-status-note').textContent=state.resume?'任务已提交，继续查询即可，不要重复生成。':error?'输入内容已保留，可调整后重新提交。':treatment()==='flat'?'正在整理色块，完成后自动进入确认图案。':'正在生成圆润的纯色平面稿，完成后自动进入确认图案。无需重复点击。';
  $('#create-resume').hidden=!state.resume||state.busy;
  const raw=$('#raw-result');$('#create-raw-result').hidden=raw.hidden;if(!raw.hidden)$('#create-raw-result').href=raw.href;
}
function dirtyPalette(){return !!state.paletteDraft||state.paletteInvalid}
function locked(){return state.busy||state.loading||!!state.resume||!!localEditor?.working||!!artEditor?.uploading}
const fabricNames={twill:'绣章布／斜纹布',felt:'毛毡／无纺布',knit:'衣服／针织面料'};
function currentSettings(){return {width:Number($('#width').value),thread:state.thread,fabric:$('#fabric').value,spacing:Number($('#spacing').value),satin_spacing:Number($('#satin-spacing').value),adaptive_fill:$('#adaptive-fill').checked}}
function createSizeNote(){
  const field=$('#create-width'),width=field.valueAsNumber,thread=$('#create-thread').value;
  const source=state.mode==='text'?null:state.sourceShape;
  const direct=uploadRoute(state.mode,source,$('#create-prompt').value)==='flat';
  const aspect=source?.subjectAspect||(direct?($('#flat-background').value==='preserve'?source?.canvasAspect:source?.inkAspect):null);
  const height=aspect?`按原图估计高 ${fmt(width*aspect)} mm，出稿后按主体轮廓核对。`:'出稿后按主体轮廓计算高度。';
  $('#create-size-note').textContent=field.validity.valid
    ?`宽 ${fmt(width)} mm。${height} ${thread==='75D'?'0.8':'1.0'} mm 的面状细节约占全宽 ${fmt((thread === '75D' ? 0.8 : 1)*100/width)}%。`
    :'填写图案本身的宽度（20–160 mm）。出稿后按主体比例计算高度。';
}
function sourceShape(image){
  const scale=Math.min(1,480/Math.max(image.naturalWidth,image.naturalHeight));
  const canvas=document.createElement('canvas');
  canvas.width=Math.max(1,Math.round(image.naturalWidth*scale));canvas.height=Math.max(1,Math.round(image.naturalHeight*scale));
  const ctx=canvas.getContext('2d',{willReadFrequently:true});ctx.drawImage(image,0,0,canvas.width,canvas.height);
  const pixels=ctx.getImageData(0,0,canvas.width,canvas.height).data;
  return {...inspectUpload(pixels,canvas.width,canvas.height),canvasAspect:image.naturalHeight/image.naturalWidth};
}
function creationPane(size){
  $('#create-source-pane').hidden=size;$('#create-size-pane').hidden=!size;
  $('#create-title').textContent=size?'确定成品尺寸':'选择图案来源';
  if(!size)return;
  const text=state.mode==='text';
  $('#chosen-thumb').hidden=text;
  if(!text)$('#chosen-thumb').src=state.upload;
  $('#chosen-caption').textContent=text?$('#create-prompt').value.trim():$('#upload-label').textContent;
  $('#back-to-source').textContent=text?'修改描述':'换图或修改要求';
  createSizeNote();$('#create-width').focus({preventScroll:true});
}
function fabricNote(){
  $('#fabric-note').textContent={twill:'按稳定底布设置底针、色块压叠和拉缩补偿。',felt:'增加适合软质底料的支撑；毛面厚度仍需试绣调整。',knit:'加强适合的底针与拉缩补偿，配合衬料绷框试绣。'}[$('#fabric').value];
}
function validParams(){const {width,spacing,satin_spacing}=currentSettings();return width>=20&&width<=160&&spacing>=.15&&spacing<=.6&&satin_spacing>=.18&&satin_spacing<=.8}
function densityButtons(){
  const {spacing,satin_spacing}=currentSettings();
  for(const b of $$('[data-density]')){const p=densityPresets[state.thread][b.dataset.density];b.setAttribute('aria-pressed',String(spacing===p[0]&&satin_spacing===p[1]))}
  $('#density-summary').textContent=`填充 ${spacing.toFixed(2)} mm · 包边 ${satin_spacing.toFixed(2)} mm${spacing>.30?' · 填充偏疏，请试绣核对':''}`;
}
function applyDensity(name='normal'){
  const p=densityPresets[state.thread][name];$('#spacing').value=p[0];$('#satin-spacing').value=p[1];densityButtons();
}
const preview=new Preview($('#canvas'),progress=>{
  $('#progress').value=progress.count;
  $('#progress-text').textContent=`已绣 ${fmt(progress.count)} / ${fmt(progress.total)} 针`;
  $('#current-color').textContent=progress.blocks?`第 ${progress.block} / ${progress.blocks} 色段 · ${state.view?.meta.colors[progress.color]?.name||''} · 本段 ${fmt(progress.colorCount)} / ${fmt(progress.colorTotal)} 针`:'';
  if(document.activeElement!==$('#sewing-stage'))$('#sewing-stage').value=String(Math.max(0,progress.block-1));
  $('#play').textContent=progress.playing?'暂停':progress.done?'从头播放':'播放';
  if(document.activeElement!==$('#needle-number'))$('#needle-number').value=progress.count;
  $('#step-back').disabled=locked()||!state.plan||progress.count===0;
  $('#step').disabled=locked()||!state.plan||progress.done;
});
preview.onRenderState=result=>{
  $('#render-status').textContent=result.state==='working'?`当前电脑正在绘制绣线质感… ${Math.round((result.progress||0)*100)}%`:result.state==='error'?result.error:'已在当前电脑绘制 · 按实际落针顺序遮挡，未加粗绣线或补底色。';
};
localEditor=new LocalEditor({preview,request:api,
  context:()=>({project_id:state.project?.id,version_id:state.version?.id,...currentSettings(),hasPlan:!!state.plan,locked:state.busy||state.loading||!!state.resume||dirtyPalette()}),
  onMode:mode,onState:()=>availability(),onChange:()=>{
    clearTimeout(replanTimer);state.plan=null;state.localChanged=true;preview.stop();mode('local');renderColors();stats();
    localEditor.recommendation();
    updateDetailFit();
    message('局部工艺已修改。更新工程后核对针数、针路和导出文件。');
  }
});
function autoCraftSummary(){
  const counts={};for(const r of localEditor.regions){const label=r.recommendation?.label||'待分析';counts[label]=(counts[label]||0)+1}
  $('#auto-craft-summary').textContent=localEditor.regions.length?`规则已匹配 ${localEditor.regions.length} 个色块 · ${Object.entries(counts).map(([k,v])=>`${v} 处${k}`).join(' / ')}`:'';
  $('#craft-method-summary').textContent=$('#auto-craft-summary').textContent;
}
localEditor.onCatalog=()=>{autoCraftSummary();updateDetailFit();renderProductionAssessment()};
function focusProblem(issue, showSettings=false){
  if(locked()||dirtyPalette()||localEditor.pending||localEditor.drawing)return;
  let region=localEditor.regions.find(r=>r.id===issue.id&&r.color===issue.color);
  if(!region&&issue.point){
    const hit=preview.hit(issue.point);
    if(hit?.color===issue.color)region=hit;
  }
  if(!region){message('这个旧方案的部位无法对应当前分区。可在图上点选，或重新生成针迹。',true);return}
  if(wizard.step===5)rememberRepair(region.id);
  localEditor.setMulti(false);localEditor.select(region.id);
  wizard.showPane('canvas',true);preview.focusRegion(region,issue.point);$('#zoom').value=String(preview.zoom);
  if(showSettings){wizard.showPane('inspector',true);$('#local-repair-note').scrollIntoView({block:'nearest'})}
}
function coverageLocations(regions){
  const list=$('#coverage-colors');list.replaceChildren();
  $('#coverage-region-count').textContent=`按问题面积定位色块（${regions.length}）`;
  $('#coverage-region-details').hidden=!regions.length;
  const describe=n=>n>0&&n<.01?'小于 0.01':n.toFixed(2);
  const more=document.createElement('button');more.type='button';more.className='text-button';
  let shown=0;
  function addPage(){
    more.remove();
    for(const issue of regions.slice(shown,shown+8)){
      const button=document.createElement('button');button.type='button';button.className='coverage-color';
      const details=[];
      if(issue.bareMm2)details.push(`露底 ${describe(issue.bareMm2)} mm²（占本色块 ${describe(issue.barePercent)}%）`);
      if(issue.colorMm2)details.push(`本色异常 ${describe(issue.colorMm2)} mm²`);
      button.textContent=`${++shown}. ${state.view.meta.colors[issue.color].name} · ${details.join(' · ')} →`;
      button.dataset.regionId=issue.id||'';
      button.onclick=()=>focusProblem({...issue,point:issue.barePoint||issue.point});
      list.append(button);
    }
    if(shown<regions.length){more.textContent=`显示更多（余 ${regions.length-shown} 处）`;list.append(more)}
  }
  more.onclick=addPage;addPage();
}
preview.onRegionPick=(id,options)=>{
  if(wizard.step===5)rememberRepair(id);
  localEditor.select(id,options);
  if(localEditor.selected===id&&!localEditor.multi&&!localEditor.pending&&!localEditor.drawing)wizard.showPane('inspector',true);
};
preview.onCoverage=result=>{
  state.coverage=result;state.coverageView=preview.data;renderProductionAssessment();wizard?.refresh();
  $('#coverage-colors').replaceChildren();$('#coverage-panel').dataset.status=result.state;
  $('#coverage-region-details').hidden=result.state!=='ready';
  $('#coverage-rebuild').hidden=result.state!=='error';
  if(result.state!=='ready'){
    $('#coverage-summary').textContent=result.state==='loading'?'正在按真实针迹检查…':result.state==='error'?result.error:'生成针迹后，按参考线宽计算。';
    $('#coverage-detail').textContent='';return;
  }
  $('#coverage-summary').textContent=`应绣处未覆盖 ${result.barePercent.toFixed(2)}% · ${result.bareMm2.toFixed(2)} mm²`;
  $('#coverage-detail').textContent=`${state.thread} · 参考线宽 ${result.lineWidth.toFixed(2)} mm · 检查精度约 ${result.mmPerPixel.toFixed(3)} mm。另有 ${result.colorGapPercent.toFixed(2)}% 缺少本色，${(result.occludedPercent||0).toFixed(2)}% 被后绣颜色遮住。${result.barePercent<.01?'当前精度下未发现明显空隙，不代表实绣一定不漏底。':'点击“查看露底位置”，放大检查红色区域。'}`;
  coverageLocations(result.regions);
};
artEditor=new ArtEditor({request:api,message,onBusy:()=>availability()});
wizard=new Wizard({context:()=>({
  version:state.version,plan:state.plan,assessment:state.assessment,plateMode:state.plateMode,
  planning:processingPlan(),
  locked:locked(),dirty:dirtyPalette(),pending:!!(localEditor.pending||localEditor.drawing),valid:validParams(),
  craftAction:aiNextAction()||(oldPlan()?{label:'用新版工艺重排 →',run:upgradePlan}:null)
}),mode,newDesign,make,message,preview});
function busy(value,label=''){
  state.busy=value;if(value)$('#raw-result').hidden=true;
  if(value){clearTimeout(replanTimer);preview.stop()}
  $('#stage').setAttribute('aria-busy',String(value));$('#job-state').classList.toggle('working',value);
  $('#generate').textContent=value?'正在处理…':generateLabel();
  availability();if(label)message(label);
}
function availability(){
  const stop=locked(),dirty=dirtyPalette(),ready=!!state.version&&!state.creating,pending=!!(localEditor?.pending||localEditor?.drawing);
  for(const el of $$('#auto-simplify,#fit-details,#generate,#create-route,#create-width,#create-thread,#choose-size,#back-to-source,#detail-mode,#edit-detail-mode,#detail-simplify,#new,#projects,#width,#height,#spacing,#satin-spacing,#adaptive-fill,#fabric,#plan-history,#max-colors,#edit-max-colors,#upload,#close-composer,#create-prompt,#edit-prompt,#image-treatment-select,#flat-background,[data-source],[data-thread],[data-density],[data-create-mode],[data-prompt],.version'))el.disabled=stop||dirty||pending;
  for(const el of $$('#palette-fields input'))el.disabled=stop;
  $('#edit').disabled=stop||dirty||pending||!ready;
  $('#edit').textContent=processingPlan()?'本版排针中':state.busy?'正在处理…':'生成修改稿';
  updatePreviewStats();
  $('#make').disabled=stop||dirty||pending||!ready||!validParams();
  $('#make').textContent=state.busy?'正在处理…':state.plan?'重新制作工程':state.hasPlan?'更新绣花工程':'制作绣花工程';
  $('#local-make').textContent=$('#make').textContent;
  for(const el of $$('#play,#restart,#step,#step-back,#progress,#needle-number,#go-needle,#speed,#jumps,#sewing-stage'))el.disabled=stop||dirty||!state.plan;
  if(!stop&&state.plan&&!dirty){$('#step-back').disabled=preview.count()===0;$('#step').disabled=preview.count()>=preview.ends.length}
  $$('[data-view="simulation"],[data-view="result"],[data-view="coverage"]').forEach(b=>b.disabled=!state.plan||stop||dirty);
  $$('[data-view="compare"],[data-view="original"],[data-view="overlay"]').forEach(b=>b.disabled=dirty);
  $('#open-palette').disabled=stop||pending||!ready;
  $('#open-palette').hidden=!dirty;
  $('#palette-save').disabled=stop||!state.paletteDraft||state.paletteInvalid;
  $('#palette-cancel').disabled=stop||!dirty;
  $('#edit-section').hidden=state.creating||!ready||preview.mode==='local';
  $('#local-editor').hidden=state.creating||!ready||preview.mode!=='local';
  $('#local-toolbar').hidden=state.creating||!ready||preview.mode!=='local';
  $('[data-view="local"]').disabled=stop||dirty||!ready;
  $('#palette-editor').hidden=state.creating||!ready;
  $('#download-section').hidden=!state.plan||state.creating||dirty;
  $('#history-controls').hidden=!state.plan||state.creating||dirty||(state.version?.plans.length||0)<2;
  $('#close-composer').hidden=!state.backProject;
  $('#preview-toolbar').hidden=state.creating||!ready;
  const inspecting=['result','coverage'].includes(preview.mode)&&!!state.plan;
  $('#result-controls').hidden=!inspecting;$('#coverage-panel').hidden=!inspecting;
  $('#effect-options').hidden=!inspecting;
  $('#proof-tools').hidden=!inspecting;$('#proof-range').hidden=!preview.proofEnabled;
  $('#proof-right-label').textContent=preview.mode==='coverage'?'右侧覆盖检查':'右侧针迹';
  $('#proof-enabled').disabled=stop||!inspecting;
  $('#edit-picked').hidden=!ready||preview.mode!=='local'||!localEditor?.selected;
  $('#edit-picked').textContent=localEditor?.multi?'查看选中的色块 →':'编辑此色块 →';
  $('.make-section').hidden=!state.plan;
  for(const el of $$('#result-controls input,#result-controls select,#toggle-coverage'))el.disabled=stop||!state.plan;
  $('#preview-material').disabled=stop||preview.mode==='coverage';
  $('#ai-craft-panel').hidden=!(ready&&wizard?.step===4&&state.craftTool==='needle');
  $('#craft-help').hidden=!(ready&&wizard?.step===4);
  const usingAI=state.plateMode==='ai';
  const processing=processingPlan();
  const nextMode=processing?storage.getItem(nextModeKey(activePlanJob.id)):null;
  for(const button of $$('[data-plate-mode]')){
    button.setAttribute('aria-pressed',String(button.dataset.plateMode===(processing?(nextMode||activePlanJob.mode):state.plateMode)));
    button.disabled=!ready||dirty||pending||(stop&&!processing);
  }
  $('#plate-mode-title').textContent=processing?'下一轮制版方式':'选择制版方式';
  $('#plate-resolve').hidden=stop||(!pending&&!dirty);
  $('#plate-resolve').textContent=dirty?'返回配色设置 →':localEditor?.drawing?'回到正在画的线 →':'处理未应用的参数 →';
  $('#split-ai-options').hidden=!usingAI;$('#craft-ai-options').hidden=!usingAI;
  $('#plate-ai-actions').hidden=!ready||wizard?.step!==4||!usingAI||state.craftTool!=='needle'||!!processing;
  let plateNote=usingAI?'点图选色块，或分析整张图。点击分析时按次调用，建议可预览后采用。':'可以直接生成针迹，也可以点色块修改针法。';
  if(processing)plateNote='当前'+plateNames[activePlanJob.mode]+'正在排针。'+(nextMode?'本版完成后进入'+plateNames[nextMode]+'。':'可在上方预选下一轮方式。');
  else if(state.resume)plateNote='正在等待恢复任务进度，点击上方“继续查看进度”可接着查看。';
  else if(stop)plateNote=localEditor?.working?'正在准备色块，完成后即可选制版方式。':'当前任务正在处理，完成后可切换制版方式。';
  else if(dirty)plateNote='配色有改动，保存或还原后可切换制版方式。';
  else if(pending)plateNote=localEditor.drawing?'有一条线正在绘制，完成或取消后可切换方式。':'选区参数尚未应用。应用或放弃本次修改后可切换方式。';
  if($('#plate-mode-note').textContent!==plateNote)$('#plate-mode-note').textContent=plateNote;
  for(const b of $$('[data-craft-tool]')){b.setAttribute('aria-pressed',String(b.dataset.craftTool===state.craftTool));b.disabled=stop||dirty||pending}
  $('#split-open-manual').disabled=stop||dirty||pending;$('#craft-open-manual').disabled=stop||dirty||pending;
  $('#ai-craft-start').disabled=!usingAI||stop||dirty||pending||!validParams()||!localEditor?.regions.length;$('#ai-craft-prompt').disabled=stop;
  const selectedForAI=localEditor?.selectedRegion();
  $('#ai-craft-selected').disabled=$('#ai-craft-start').disabled||!selectedForAI||localEditor.multi||!!localEditor.overrides[selectedForAI?.id];
  $('#ai-craft-selected').textContent=selectedForAI&&!localEditor.multi
    ? '仅分析选中这块 · '+localEditor.colors[selectedForAI.color].name : '请先在图上点选一个色块';
  $('#ai-craft-apply').disabled=stop||dirty||pending||!state.craftAdvice||!$('#ai-craft-results input:checked')||stable(state.craftAdvice.snapshot)!==stable(craftSnapshot());
  if(state.craftAdvice&&!stop&&stable(state.craftAdvice.snapshot)!==stable(craftSnapshot()))$('#ai-craft-status').textContent='尺寸、图案或局部设置已变化。请重新分析，避免把旧建议应用到新图案。';
  $('#coverage-rebuild').disabled=stop||dirty||pending;
  preview.pickRegions=[4,5].includes(wizard?.step)&&ready&&!stop&&!dirty;
  document.body.classList.toggle('editing-region',preview.mode==='local');
  $('#resume-job').hidden=!state.resume||state.busy;
  $('#version-label').classList.toggle('unsaved',dirty);
  splitEditor?.available();
  $('#detail-review').hidden=!ready;
  $('#detail-preserve').disabled=stop||dirty||pending;
  const manualRegions=!!(localEditor?.cuts?.length||Object.keys(localEditor?.overrides||{}).length);
  $('#fit-details').disabled=stop||dirty||pending||manualRegions||!validParams();
  const fitted=state.version?.fit_report;
  const needsFit=!!fitted&&(fitted.width!==Number($('#width').value)||fitted.thread!==state.thread);
  $('#refit-size').hidden=!needsFit;
  if(needsFit)$('#refit-size-note').textContent=manualRegions?'当前版本有手动分区或针法。可从版本记录选择母稿，再按新尺寸取舍细节。':`当前稿按 ${fmt(fitted.width)} mm / ${fitted.thread} 整理。可从母稿按新尺寸重新取舍细节。`;
  $('#refit-source').disabled=$('#fit-details').disabled;
  $('#auto-simplify').disabled=stop||dirty||pending||manualRegions;
  for(const el of $$('#detail-fit-list button'))el.disabled=stop||dirty||pending;
  $('#detail-compare').disabled=stop||dirty||pending;
  localEditor?.availability();
  $('#local-make').disabled=$('#make').disabled;
  artEditor?.setLocked(stop||dirty||pending||!ready);renderProductionAssessment();wizard?.refresh();
  if(sizeAdvice)$('#apply-size-advice').disabled=stop||dirty||pending;
  if(ready&&state.plan?.quality_review&&wizard?.step===5&&!stop&&!dirty&&!pending)preview.inspectCoverage();
}
async function projects(){
  const result=await api('/api/projects'),selected=state.project?.id;
  state.currentEngine=result.engine_version;
  $('#projects').replaceChildren(...result.projects.map(p=>new Option(p.title,p.id)));
  if(selected)$('#projects').value=selected;
  return result.projects;
}
function mode(next){
  if(next==='local'&&wizard?.step===5)wizard.go(4,true);
  if(next==='local')state.craftTool='needle';
  if(next==='layers')state.craftTool='split';
  if(next!=='local'&&(localEditor?.pending||localEditor?.drawing)){message('请先应用局部设置，或点击“放弃本次修改”。');return}
  if(['simulation','result','coverage'].includes(next)&&(!state.plan||dirtyPalette()))return;
  if(dirtyPalette()&&['compare','original','overlay'].includes(next))return;
  $('#comparison').hidden=next!=='compare';
  $('.preview-sheet').hidden=next==='compare';
  $('#canvas').hidden=!state.version||state.creating;
  $('#compare-controls').hidden=next!=='compare';
  $('#layer-controls').hidden=next!=='layers';$('#overlay-controls').hidden=next!=='overlay';$('#play-controls').hidden=next!=='simulation';
  $('#sewing-stage-control').hidden=next!=='simulation';
  $$('[data-view]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.view===next)));
  const captions={artwork:dirtyPalette()?'配色预览 · 保存后生成新版本':'圆润曲线 · 纯色色块',local:'虚线框为选区，滑过仅描边；可拆分、合并，再更新真实针迹',compare:'并排核对造型与配色，原图按各自比例显示',layers:`${state.version?.color_count||0} 色 · ${state.view?.meta.separated_objects.length||0} 个色块`,overlay:'平面稿与分色边界叠合检查',original:'上传的参考原图',simulation:'按实际落针计数，跳针与剪线不占针数',result:'实际针迹渲染 · 点选色块可微调针法',coverage:'红色为未覆盖位置，橙色为本色不足 · 放大检查，点选色块调整'};
  $('#caption').textContent=captions[next]||'';
  $('#canvas').setAttribute('aria-label',`${next==='simulation'?'逐针模拟':next==='result'?'刺绣效果':'当前平面稿'}及尺寸标注`);
  if(next==='local'){
    localEditor?.recommendation();
    if(localEditor?.analysisError&&!localEditor.working&&!state.loading&&!state.busy&&!localEditor.pending&&!localEditor.drawing)
      localEditor.load(localEditor.payload()).catch(e=>message(e.message,true));
  }
  preview.setMode(next);
  $('#toggle-coverage').textContent=next==='coverage'?'返回刺绣效果':'查看露底位置';
  availability();
}
function setupComparison(){
  const parent=state.project.versions.find(v=>v.id===state.version.parent_id);
  state.comparisons=[];
  if(parent)state.comparisons.push({value:'parent',label:`${state.version.kind==='fit'?'尺寸适配前':'修改前'} · 版本 ${state.project.versions.indexOf(parent)+1}`,url:parent.flat_url});
  if(state.version.generated_url)state.comparisons.push({value:'ai_raw',label:'AI 原始结果（分色前）',url:state.version.generated_url});
  if(state.project.source_url)state.comparisons.push({value:'original',label:'参考原图',url:state.project.source_url});
  $('#compare-source').replaceChildren(...state.comparisons.map(c=>new Option(c.label,c.value)));
  $('#fit-marks').checked=false;
  $('[data-view="compare"]').hidden=!state.comparisons.length;
  $('[data-view="original"]').hidden=!state.project.source_url;
  if(state.comparisons.length)updateComparison();
}
function updateComparison(){
  const source=state.comparisons.find(c=>c.value===$('#compare-source').value)||state.comparisons[0];
  if(!source)return;
  $('#compare-label').textContent=source.label;$('#compare-before').src=source.url;$('#compare-before').alt=source.label;
  const available=source.value==='parent'&&!!state.version.change_map_url;
  $('#fit-marks-label').hidden=!available;
  const marked=available&&$('#fit-marks').checked;
  $('#compare-after').src=marked?state.version.change_map_url:state.version.flat_url;
  $('#compare-after').alt=marked?'尺寸适配稿，玫红色为实际改动位置':'当前平面稿';
}
function showFitComparison(focusCanvas=true){
  mode('compare');$('#compare-source').value='parent';$('#fit-marks').checked=!!state.version.change_map_url;
  updateComparison();if(focusCanvas)wizard.showPane('canvas',true);
}
function renderColors(){
  const colors=state.view?.meta.colors||[],selected=preview.layer;
  $('#colors').replaceChildren();$('#layer').replaceChildren(new Option('全部色块','all'));
  colors.forEach((c,i)=>{
    const b=document.createElement('button');b.type='button';b.className='color-row';
    const dot=document.createElement('span');dot.className='swatch';dot.style.backgroundColor=state.paletteDraft?.[i]||c.hex;
    const title=document.createElement('span');title.className='color-title';title.textContent=c.name;
    const small=document.createElement('small');small.textContent=dirtyPalette()?'保存后重新匹配':`线色 ${i+1} · 示范线号 ${c.thread.code}`;title.append(small);
    const count=document.createElement('span');count.className='color-count';count.textContent=state.plan&&!dirtyPalette()?`${fmt(state.view.stats.stitches_by_color[i])} 针`:'待排针';
    b.append(dot,title,count);
    b.onclick=()=>{if(state.plan&&!dirtyPalette()&&['simulation','result'].includes(preview.mode)){mode('simulation');preview.throughColor(i)}else{mode('layers');preview.layer=String(i);$('#layer').value=String(i);preview.render()}};
    $('#colors').append(b);$('#layer').add(new Option(`${i+1}. ${c.name}`,String(i)));
  });
  preview.layer=selected==='all'||Number(selected)<colors.length?selected:'all';$('#layer').value=preview.layer;
  $('#sewing-stage').replaceChildren(...(preview.blocks||[]).map((block,i)=>{const c=colors[block.color];return new Option(`${i+1}. ${c?.name||''} ${c?.thread?.code||''} · ${state.view?.meta.sewing_steps?.[i]?.label||'填绣'} · ${fmt(block.end-block.start)} 针`,String(i))}));
}
function renderPalette(){
  $('#palette-fields').replaceChildren();state.paletteDraft=null;state.paletteInvalid=false;
  (state.art?.meta.colors||[]).forEach((c,i)=>{
    const row=document.createElement('div');row.className='palette-row';
    const label=document.createElement('label');label.textContent=`${i+1}. ${c.name}`;label.htmlFor=`palette-color-${i}`;
    const picker=document.createElement('input');picker.type='color';picker.id=label.htmlFor;picker.value=c.hex;
    picker.setAttribute('aria-label',`颜色 ${i+1} 的色板`);
    const value=document.createElement('input');value.type='text';value.className='palette-hex';value.value=c.hex;
    value.maxLength=7;value.pattern='#[0-9A-Fa-f]{6}';value.spellcheck=false;value.setAttribute('aria-label',`颜色 ${i+1} 的颜色值`);
    picker.oninput=()=>{value.value=picker.value.toUpperCase();updatePaletteDraft()};
    value.oninput=()=>{if(/^#[0-9a-f]{6}$/i.test(value.value))picker.value=value.value;updatePaletteDraft()};
    row.append(label,picker,value);$('#palette-fields').append(row);
  });
}
function updatePaletteDraft(){
  clearTimeout(replanTimer);
  const fields=$$('.palette-hex'),colors=fields.map(e=>e.value.toUpperCase());
  state.paletteInvalid=fields.some(e=>!/^#[0-9a-f]{6}$/i.test(e.value));
  fields.forEach(e=>e.setAttribute('aria-invalid',String(!/^#[0-9a-f]{6}$/i.test(e.value))));
  if(!state.paletteInvalid)state.paletteDraft=colors.some((h,i)=>h!==state.art.meta.colors[i].hex.toUpperCase())?colors:null;
  preview.setPalette(state.paletteDraft);preview.layer='all';mode('artwork');renderColors();stats();versionLabel();
  $('#palette-note').textContent=state.paletteInvalid?'请输入完整的颜色值，例如 #F3D978。':state.paletteDraft?'当前为配色预览。保存为新版后，再重新制作绣花工程。':'保存后重新匹配线色，再制作绣花工程。';
}
function cancelPalette(){renderPalette();preview.setPalette(null);renderColors();stats();versionLabel();mode('artwork');$('#palette-note').textContent='配色已还原，原来的工程仍可使用。'}
function updatePreviewStats(){
  const stitches=state.plan&&!dirtyPalette()?state.view?.stats?.stitch_count:null;
  const label=processingPlan()?'正在排针':stitches!=null?fmt(stitches)+' 针':dirtyPalette()?'配色未保存':'等待排针';
  $('#preview-stats').textContent=state.version?`${fmt(Number($('#width').value))} × ${fmt(Number($('#height').value))} mm · ${state.thread} · ${label}`:'';
}
function stats(){
  const s=state.plan&&!dirtyPalette()?state.view.stats:null;
  $('#total').textContent=s?fmt(s.stitch_count):'—';
  updatePreviewStats();
  $('#stats-note').textContent=s?`${s.unique_thread_colors} 色 · ${s.color_blocks||s.color_changes+1} 色段 · ${s.color_changes} 次换色 · ${s.trims_dst} 次剪线`:dirtyPalette()?'配色尚未保存，保存后重新排针':'制作工程后，按实际针迹计算';
  const craft=s?state.view.meta.craft:null;
  $('#craft-section').hidden=!craft;
  if(craft){
    const types=craft.object_types;
    const cross=state.view.meta.planned_objects?.filter(o=>o.stitch_type==='填针'&&o.underlay).length;
    $('#craft-summary').textContent=`${types['轮廓底针']||0} 处轮廓底针；${types['填针']||0} 处填针；${types['缎面针']||0} 处缎面针；${types['三重走针']||0} 处细线走针。${cross??0} 处填针使用交叉打底，面针错开落针点。`;
    if(craft.overlap_objects?.length)$('#craft-summary').textContent+=` 相接处底色向后绣区域压入 ${craft.underlap_mm} mm${craft.closed_detail_holes?'，'+craft.closed_detail_holes+' 处小细节下保留连续底色':''}。`;
    $('#craft-order').textContent=craft.border_last?'主体细节完成后，最后绣外圈包边。同色尽量连续，覆盖关系需要时再返回。':'按区域形状安排针向，同色尽量连续。';
    if(craft.curved_satin_objects)$('#craft-summary').textContent+=` ${craft.curved_satin_objects} 处弯曲窄条按轮廓转向。`;
    if(craft.local_regions)$('#craft-summary').textContent+=` ${craft.local_regions} 处局部工艺设置，${craft.guided_fill_objects||0} 处随曲线填针，${craft.region_cuts||0} 次拆分、${craft.region_merges||0} 次合并。`;
    if(craft.merged_local_regions)$('#craft-summary').textContent+=` ${craft.merged_local_regions} 处相同参数的相邻区已连续填绣，减少多余接缝。`;
    if(craft.hidden_connections?.length){
      const joins=craft.hidden_connections,hidden=joins.filter(j=>j.role==='同色藏线').length,short=joins.filter(j=>j.role==='同色短接').length,same=joins.filter(j=>j.role==='共点连续').length;
      $('#craft-order').textContent+=` 同色衔接：${[hidden?hidden+' 处藏线':'',short?short+' 处针面内短接':'',same?same+' 处共点连续':''].filter(Boolean).join('，')}。跨过露底位置的连接保留剪线。`;
    }
    $('#craft-fabric').textContent=`${craft.profile}。${craft.local_regions?'已应用选区的针向和工艺，其余区域自动判断':'针向按区域形状推算'}，需结合实际底料和衬料试绣。`;
  }
  $('#plan-note').textContent=s?.travel_mm!==undefined?`含打底和主绣。跨区空移 ${fmt(s.travel_mm)} mm${s.travel_saved_mm>0?'，本版绣序优化减少 '+fmt(s.travel_saved_mm)+' mm':''}；跳针与剪线不计针数。`:'包含打底和主绣；跳针、剪线不计入。';
  $('#progress').max=s?.stitch_count||0;$('#needle-number').max=s?.stitch_count||0;
  $('#downloads').replaceChildren();
  $('#plan-history').replaceChildren(...(state.version?.plans||[]).map((p,i)=>new Option(`${i+1}. ${p.width} mm · ${p.thread} · ${fabricNames[p.fabric]||'旧版底料'} · ${fmt(p.stitch_count)} 针${Object.prototype.hasOwnProperty.call(p,'object_overrides')?' · 局部 '+Object.keys(p.object_overrides||{}).length+' 区':p.engine_version==='generic-flat-v8-curves'?' · 随形排针':''}`,p.id)));
  if(s){
    $('#plan-history').value=state.plan.id;
    $('#plan-params').textContent=`${fmt(state.plan.width)} × ${fmt(state.plan.height)} mm · ${state.plan.thread} · ${fabricNames[state.plan.fabric]||'稳定底布'} · 填充 ${state.plan.spacing} / 包边 ${state.plan.satin_spacing??state.plan.spacing} mm`;
    for(const [label,url] of Object.entries(state.plan.downloads)){const a=document.createElement('a');a.textContent=label;a.href=url;a.download='';$('#downloads').append(a)}
  }
  renderNeedleReview();availability();
}
function renderNeedleReview(){
  const q=state.plan?.quality_review;$('#needle-review-list').replaceChildren();
  const problems=(q?.issues||[]).map(i=>({id:i.region_key,color:i.color,reason:i.area_mm2+' mm² · '+i.reasons[0]}));
  for(const item of q?.stitch_suitability?.issues||[])problems.unshift({id:item.region_key,color:item.color,reason:item.reason});
  for(const gap of q?.gap_bridges||[])problems.push({id:gap.first,color:gap.color,point:gap.point_mm,reason:`两块之间的 ${gap.gap_mm} mm 留白被同色针迹挤占`});
  for(const key of q?.unstitched_regions||[]){
    const obj=state.view?.meta.separated_objects?.find(o=>o.region_key===key||o.id===key);
    if(obj)problems.push({id:key,color:obj.color,reason:'细节没有产生独立色段'});
  }
  $('#needle-review-title').textContent=!q?'旧方案尚未做局部形状检查':q.status==='needs_review'?'局部形状需复核'+(problems.length?' · '+problems.length+' 处':''):'未发现明显的局部形状异常 · 待试绣';
  $('#needle-review-note').textContent=q?(q.status==='needs_review'?'请先复核原稿花纹、缺色、遮盖和相邻针迹的间隙，再决定是否试绣。':'这是几何检查结果，实际覆盖仍需试绣。'):'重新生成针迹后，系统会检查花纹是否被遮住、间隙是否堵住。';
  $('#needle-review-details').hidden=!problems.length;$('#needle-review-toggle').textContent=`查看需要调整的 ${problems.length} 处`;
  for(const [index,issue] of problems.slice(0,40).entries()){
    const b=document.createElement('button');b.type='button';b.className='text-button';
    b.textContent=`${index+1}. ${state.view.meta.colors[issue.color]?.name||'色块'} · ${issue.reason} →`;
    b.onclick=()=>focusProblem(issue);
    $('#needle-review-list').append(b);
  }
}
function oldPlan(){
  return !!(state.plan && state.currentEngine && state.plan.engine_version!==state.currentEngine);
}
function repairIdentity(plan){
  return JSON.stringify([state.project?.id,state.version?.id,plan.width,plan.height,plan.thread,plan.fabric,plan.region_cuts||[]]);
}
function matchingCoverage(){
  return state.plan && state.coverageView===state.view && state.coverage?.state==='ready' &&
    Math.abs(state.coverage.lineWidth-state.plan.quality_review?.reference_width_mm)<.001 ? state.coverage : null;
}
function rememberRepair(id=null){
  const coverage=matchingCoverage();
  if(!coverage){state.repairTrack=null;return}
  const region=localEditor.regions.find(r=>r.id===id);
  const name=region?state.view.meta.colors[region.color].name:'';
  state.repairTrack={key:repairIdentity(state.plan),planId:state.plan.id,id,name,line:coverage.lineWidth,
    note:repairForRegion(id,state.plan.quality_review,coverage,name,region&&reviewRegion(region,state.thread,localEditor.overrides[id],state.plan.quality_review?.feature_witnesses?.[id])),
    before:{bare:coverage.barePercent,color:coverage.colorGapPercent+coverage.occludedPercent,
      local:measuredRegion(coverage,id)}};
}
async function upgradePlan(){
  if(!oldPlan()||locked()||dirtyPalette()||localEditor.pending||localEditor.drawing)return;
  const plan=state.plan;
  if(!matchingCoverage()&&state.view.meta.command_registration==='explicit-design-center'){
    busy(true,'正在记录旧版的针迹覆盖');
    try{await preview.inspectCoverage()}finally{busy(false)}
  }
  if(plan!==state.plan)return;
  rememberRepair(state.repairFocus?.id);
  make({sameArtwork:true});
}
function renderRepairCheck(result,details){
  const coverage=matchingCoverage(),p=state.plan,q=p?.quality_review,track=state.repairTrack;
  const stopped=locked()||dirtyPalette()||localEditor.pending||!!localEditor.drawing;
  $('#repair-upgrade').hidden=!oldPlan();$('#repair-upgrade').disabled=stopped;
  state.borderTrial=result.status==='adjust'&&!oldPlan()&&!q?.gap_bridges?.length&&!q?.unstitched_regions?.length&&!q?.stitch_suitability?.issues.length
    ?narrowBorderTrial(p,state.view?.meta?.planned_objects||[],coverage):null;
  $('#repair-border').hidden=!state.borderTrial;$('#repair-border').disabled=stopped;
  if(state.borderTrial)$('#repair-border').textContent='试排窄包边（峰间距 '+state.borderTrial.pitch.toFixed(2)+' mm）';
  $('#repair-focus').disabled=stopped;$('#repair-next').hidden=true;state.repairFocus=null;
  if(coverage&&['adjust','blocked'].includes(result.status)&&state.view.stats?.sew_geometry_dst_pes_equal){
    const coverageRisk=coverage.barePercent>=ASSESSMENT_RULES.bareLimit||coverage.colorGapPercent+coverage.occludedPercent>=ASSESSMENT_RULES.colorLimit;
    const first=q.unstitched_regions?.[0]||q.stitch_suitability?.issues[0]?.region_key||q.issues?.[0]?.region_key||q.gap_bridges?.[0]?.first||details.risk[0]?.region.id||(coverageRisk&&coverage.regions[0]?.id);
    const region=localEditor.regions.find(r=>r.id===first);
    if(region){
      const note=repairForRegion(first,q,coverage,state.view.meta.colors[region.color].name,details.risk.find(item=>item.region.id===first));
      if(note){
        state.repairFocus={id:first,color:region.color,point:note.point||coverage.regions.find(r=>r.id===first)?.point};
        $('#repair-next-title').textContent=note.title;$('#repair-next-note').textContent=note.note;$('#repair-next').hidden=false;
      }
    }
  }
  const active=track?.note && track.id===localEditor.selected && p?.id===track.planId && repairIdentity(p)===track.key;
  $('#local-repair-note').hidden=!active;
  const recheck=!!((active||manualDraft)&&localEditor.pending&&!localEditor.drawing&&!localEditor.multi);
  $('#repair-apply-plan').hidden=!recheck;$('#repair-apply-plan').disabled=!recheck||locked()||dirtyPalette();
  $('#local-apply').classList.toggle('primary',!recheck);
  if(active){
    $('#local-repair-guidance').textContent=track.note.note;
    $('#repair-return-art').hidden=!track.note.artwork;$('#repair-return-art').disabled=stopped;
  }
  $('#repair-diff').hidden=true;
  if(!track||!coverage||p.id===track.planId||repairIdentity(p)!==track.key||Math.abs(coverage.lineWidth-track.line)>.001)return;
  const rows=$('#repair-diff-rows');rows.replaceChildren();
  const add=(label,before,after,unit)=>{
    const row=rows.insertRow(),heading=document.createElement('th');heading.scope='row';heading.textContent=label;row.append(heading);
    for(const value of [before,after])row.insertCell().textContent=value.toFixed(2)+' '+unit;
  };
  const current=measuredRegion(coverage,track.id);
  if(track.before.local&&current){
    add('选区露底',track.before.local.bare,current.bare,'mm²');
    add('选区本色异常',track.before.local.color,current.color,'mm²');
  }
  add('整图预计露底',track.before.bare,coverage.barePercent,'%');
  add('整图本色异常',track.before.color,coverage.colorGapPercent+coverage.occludedPercent,'%');
  $('#repair-diff-note').textContent=`${track.name&&current?'选区为'+track.name+'。':''}两版使用相同图稿、尺寸和参考线宽。本色异常包含缺色及后绣遮盖。`;
  $('#repair-diff').hidden=false;
}
function renderSewouts(plan){
  const details=$('#sewout-details'),form=$('#sewout-form');
  const key=plan?[state.project.id,state.version.id,plan.id].join('/'):'';
  details.hidden=!key||!!state.localChanged;
  if(form.dataset.plan!==key){
    form.reset();form.dataset.plan=key;delete form.dataset.sample;
    $('#sewout-status').textContent='';details.open=false;
  }
  const entries=(plan?.sewouts||[]).filter(r=>r.file_signature===plan.file_signature);
  if(form._entries!==plan?.sewouts||form.dataset.signature!==(plan?.file_signature||'')){
    form._entries=plan?.sewouts;form.dataset.signature=plan?.file_signature||'';
    const list=$('#sewout-history');list.replaceChildren(...entries.slice().reverse().map(record=>{
      const li=document.createElement('li');
      li.textContent=[record.created_at.slice(0,10),record.result==='pass'?'样品合格':'需要改版',record.setup,record.note].filter(Boolean).join(' · ');
      return li;
    }));list.hidden=!entries.length;
  }
  $('#sewout-toggle').textContent=entries.length?'实物试绣记录（'+entries.length+'）':'登记实物试绣';
  $('#sewout-save').disabled=locked()||form.dataset.saving==='true'||dirtyPalette()||!!localEditor?.pending||!!localEditor?.drawing;
  if(entries.length){
    const last=entries.at(-1);
    $('#assessment-production').textContent='本版已登记 '+entries.length+' 次实物试绣 · 最近一次'+(last.result==='pass'?'样品合格':'需要改版');
  }
}
$('#sewout-form').onsubmit=async event=>{
  event.preventDefault();const form=event.currentTarget,plan=state.plan;
  if(!plan||$('#sewout-save').disabled||!form.reportValidity())return;
  const project=state.project,version=state.version,key=form.dataset.plan;
  const sample=form.dataset.sample||(form.dataset.sample=crypto.randomUUID().replaceAll('-',''));
  const payload={version_id:version.id,plan_id:plan.id,file_signature:plan.file_signature,
    sample_id:sample,result:$('#sewout-result').value,setup:$('#sewout-setup').value,note:$('#sewout-note').value};
  form.dataset.saving='true';$('#sewout-save').disabled=true;
  try{
    const saved=await api('/api/projects/'+project.id+'/sewouts',payload);
    Object.assign(plan,saved);
    if(form.dataset.plan===key){
      if(state.plan!==plan)Object.assign(state.plan,saved);
      delete form.dataset.sample;$('#sewout-result').value='';$('#sewout-note').value='';$('#sewout-status').textContent='本次试绣已登记。';
    }
  }catch(e){if(form.dataset.plan===key)$('#sewout-status').textContent=e.message}
  finally{delete form.dataset.saving;renderProductionAssessment()}
};
function editDraftRegion(region,point){
  if(locked()||!draftInfo)return;
  manualDraft={body:draftBody,...currentSettings()};
  if(region&&state.plan)rememberRepair(region.id);
  wizard.go(4);mode('local');
  if(region)focusProblem({id:region.id,color:region.color,point},true);
  else wizard.showPane('canvas',true);
}
function draftLocation(point,review){
  const horizontal=(point[0]-5)/review.width,vertical=(point[1]-5)/review.height;
  return (horizontal<.34?'左':horizontal>.66?'右':'中')+(vertical<.34?'上':vertical>.66?'下':'部');
}
function renderDraftActions(){
  const panel=$('#draft-revision'),review=draftInfo;
  const legacy=!review&&oldPlan()&&!state.loading&&!localEditor.pending;
  panel.hidden=(!review&&!legacy)||state.creating||![2,3,5].includes(wizard?.step)||dirtyPalette()||state.localChanged;
  const shortcut=$('#open-draft-review');
  shortcut.hidden=panel.hidden||(!legacy&&!review?.items.length&&!state.version?.revision_registration?.needs_review);
  shortcut.textContent=legacy?'旧针版待复核 →':review?.items.length?'查看 '+review.items.length+' 条修改建议 →':'查看本版复评 →';
  if(panel.hidden)return;
  $('#draft-recheck').hidden=!legacy;$('#draft-recheck').disabled=locked();
  $('#draft-manual').hidden=!!legacy;
  if(legacy){
    $('#draft-review-title').textContent='初版评估与修改';
    $('#draft-review-summary').textContent='这份旧针版缺少新版工艺检查。可以沿用 '+fmt(state.plan.width)+' mm、'+state.plan.thread+' 和已有设置，重新排针后查看修改意见。';
    $('#draft-review-items').replaceChildren();$('#draft-more').hidden=true;$('#draft-ai').hidden=true;
    $('#draft-source').hidden=true;$('#draft-review-cost').textContent='规则排针与评估免费。';
    return;
  }
  const previous=(state.project.revisions||[]).slice().reverse().find(r=>r.result.version_id===state.version.id&&
    (r.result.plan_id||null)===(state.plan?.id||null));
  draftPrevious=previous;
  $('#draft-review-title').textContent=previous?'修改稿复评':'初版评估与修改';
  const metrics=previous?[
    ['明显纹样未保留','motifs','处'],['笔画挤占','gap_bridges','处'],['形状异常','shape_issues','处'],
    ['针向异常','direction_issues','处'],['未落针','unstitched','处'],
    ['预计露底','bare','%'],['本色异常','color','%'],['工艺评分','score','分']
  ].filter(([,key])=>(previous.source_plan||key==='motifs')&&Number.isFinite(previous.before[key])&&Number.isFinite(review[key])&&
        (previous.before[key]!==review[key]||key==='score')).map(([label,key,unit])=>
    label+' '+previous.before[key]+unit+' → '+review[key]+unit):[];
  let verdict='';
  if(previous){
    const prior=previous.before;
    const increase=(key,margin=0)=>Number.isFinite(review[key])&&Number.isFinite(prior[key])&&review[key]>prior[key]+margin;
    const newRisk=!!state.version.revision_registration?.needs_review||increase('motifs')||
      previous.source_plan&&(increase('gap_bridges')||increase('shape_issues')||increase('direction_issues')||
        increase('unstitched')||(review.color>=5&&increase('color',.25))||(review.bare>=2&&increase('bare',.25)));
    verdict=newRisk?'复评发现风险增加，建议保留修改前的版本。 ':
      state.plan&&state.assessment?.canTrial?'修改稿通过软件检查，可安排试绣。 ':
      review.items.length?'修改后仍有部位需要调整。 ':'修改稿仍待针迹检查。 ';
  }
  $('#draft-review-summary').textContent=verdict+(metrics.length?metrics.join(' · ')+'。 ':'')+(review.items.length?
    '按 '+fmt(review.width)+' mm、'+review.thread+' 检查，建议优先调整以下部位。':
    state.plan&&state.assessment?.canTrial?'本轮检查通过，可安排试绣。':state.plan?'请先核对下方的机器文件和工艺评估。':'平面稿已保存。生成针迹后可检查覆盖和笔画留白。');
  const list=$('#draft-review-items'),more=$('#draft-more'),rest=more.querySelector('ol');
  list.replaceChildren();rest.replaceChildren();more.hidden=review.items.length<=3;
  if(more.dataset.review!==draftKey){more.open=false;more.dataset.review=draftKey}
  more.querySelector('summary').textContent='其余 '+Math.max(0,review.items.length-3)+' 条修改意见';
  for(const [index,item] of review.items.entries()){
    const li=document.createElement('li');const region=localEditor.regions.find(r=>r.id===item.region);
    const color=region&&state.view?.meta.colors[region.color]?.name;
    const box=region?.bbox;
    const location=box?draftLocation([(box[0]+box[2])/2,(box[1]+box[3])/2],review):'';
    const detail=item.kind==='detail'&&region&&reviewRegion(region,state.thread,localEditor.overrides[region.id],state.plan?.quality_review?.feature_witnesses?.[region.id]);
    const note=detail?.level==='risk'?detail.reasons.join('。')+'。可调整该处针法或留白。':item.text;
    li.textContent=(color?location+' · '+color+' · ':'')+note;
    if(region){
      const locate=document.createElement('button');locate.className='text-button';locate.type='button';
      locate.textContent='在图上调整';locate.disabled=locked();
      locate.onclick=()=>editDraftRegion(region);
      li.append(' ',locate);
    }
    for(const loc of item.locations||[]){
      const locate=document.createElement('button');locate.className='text-button';locate.type='button';
      locate.textContent=draftLocation(loc.point,review)+' · '+loc.count+' 处';
      locate.disabled=locked();locate.onclick=()=>{
        const hit=preview.hit(loc.point);
        if(hit)editDraftRegion(hit,loc.point);
        else {wizard.go(2);showFitComparison();message('玫红区域标出适配时改变的花纹。')}
      };li.append(' ',locate);
    }
    (index<3?list:rest).append(li);
  }
  $('#draft-manual').disabled=locked();$('#draft-ai').hidden=!review.route;$('#draft-ai').disabled=locked();
  $('#draft-review-cost').textContent=review.route==='artwork'?'AI 将按建议改图，另存新版并重新排针。使用一次生图服务。':
    review.route==='craft'?'AI 只调整列出的色块工艺，按模型调用计费。新针版会自动复评。':'';
  $('#draft-source').hidden=!previous;$('#draft-source').disabled=locked();
}
function requestDraftReview(result,detailReview,stale){
  const p=state.plan,v=state.version,c=matchingCoverage();
  const ready=v&&!state.creating&&!state.loading&&!stale&&(p?
    c&&['trial','blocked','adjust'].includes(result.status):v.fit_report||v.preferred_settings?.width);
  if(!ready){draftKey='';draftInfo=null;renderDraftActions();return}
  const body={version_id:v.id,plan_id:p?.id||null,observations:p?{
    score:result.score,bare:c.barePercent,color:c.colorGapPercent+c.occludedPercent,
    detail_ids:detailReview.risk.slice(0,5).map(x=>x.region.id),
    bare_ids:c.regions.filter(x=>x.bareMm2>0).sort((a,b)=>b.bareMm2-a.bareMm2).slice(0,4).map(x=>x.id),
    color_ids:c.regions.filter(x=>x.colorMm2>0).sort((a,b)=>b.colorMm2-a.colorMm2).slice(0,4).map(x=>x.id)
  }:{}};
  const key=state.project.id+JSON.stringify(body);
  if(draftKey===key){renderDraftActions();return}
  draftKey=key;draftBody=body;draftInfo=null;renderDraftActions();
  api('/api/projects/'+state.project.id+'/draft-review',body).then(value=>{
    if(draftKey===key){draftInfo=value;renderDraftActions()}
  }).catch(()=>{if(draftKey===key){draftKey='';draftInfo=null;renderDraftActions()}});
}
$('#draft-ai').onclick=async()=>{
  if(locked()||!draftInfo?.route||!draftBody)return;
  const body=draftBody;busy(true,'正在提交本版的评估意见');
  try{await watch(await api('/api/projects/'+state.project.id+'/revise-draft',body))}
  catch(e){message(e.message,true);busy(false)}
};
$('#draft-recheck').onclick=upgradePlan;
$('#draft-manual').onclick=()=>{
  if(locked()||!draftInfo)return;
  const item=draftInfo.items.find(item=>item.region);
  editDraftRegion(localEditor.regions.find(x=>x.id===item?.region));
};
$('#draft-source').onclick=async()=>{
  if(locked()||!draftPrevious)return;
  await loadVersion(draftPrevious.source_version,draftPrevious.source_plan);
  wizard.go(state.plan?5:2,true);
};
function renderProductionAssessment(){
  $('#needle-review').hidden=!state.version||state.creating;
  const pending=!!(localEditor?.pending||localEditor?.drawing),regions=localEditor?.regions||[];
  const stale=dirtyPalette()||pending||state.localChanged;
  const detailReview=reviewDetails(regions,state.thread,localEditor?.overrides||{},stale?{}:state.plan?.quality_review?.feature_witnesses);
  const result=assessProduction({hasPlan:!!state.plan,stale,
    quality:state.plan?.quality_review,stats:state.plan?state.view?.stats:null,
    coverage:state.coverageView===state.view?state.coverage:null,
    details:detailReview,totalRegions:regions.length,
    detailsReady:!state.loading&&!localEditor?.analysisPending&&!localEditor?.analysisError});
  state.assessment=result;$('#needle-review').dataset.status=result.status;
  requestDraftReview(result,detailReview,stale);
  $('#assessment-score').textContent=result.score??'—';$('#assessment-title').textContent=result.title;
  $('#assessment-production').textContent=result.production;
  renderSewouts(state.plan);
  const p=state.plan,w=p?.quality_review?.reference_width_mm;
  const envelope=p?.quality_review?.needle_envelope;
  const sewnSize=envelope?` · 文件落针范围 ${fmt(envelope.width_mm)} × ${fmt(envelope.height_mm)} mm`:'';
  $('#assessment-basis').textContent=p?`目标 ${fmt(p.width)} × ${fmt(p.height)} mm${sewnSize} · ${p.thread} · ${fabricNames[p.fabric]||'底料未记录'}${Number.isFinite(w)?' · 评估参考线宽 '+w.toFixed(2)+' mm':''}`:'评分只针对当前尺寸和工艺生成的针迹。';
  const components=$('#assessment-components');components.replaceChildren();components.hidden=!result.components.length;
  for(const part of result.components){
    const cell=document.createElement('div');cell.className='assessment-component';
    const heading=document.createElement('div');heading.className='section-title';
    const title=document.createElement('span');title.textContent=part.label;
    const points=document.createElement('strong');points.textContent=`${part.points.toFixed(1)} / ${part.max}`;
    const note=document.createElement('p');note.textContent=part.note;heading.append(title,points);cell.append(heading,note);components.append(cell);
  }
  $('#assessment-reasons').textContent=result.reasons.join('；');$('#assessment-reasons').hidden=!result.reasons.length;
  $('#assessment-next').textContent=result.next;
  renderRepairCheck(result,detailReview);
  $('#assessment-coverage').hidden=!state.plan||result.status==='stale';$('#assessment-coverage').disabled=locked()||pending||dirtyPalette();
  $('#assessment-reset-width').hidden=!result.widthMismatch;$('#assessment-reset-width').disabled=locked()||pending;
  $('#assessment-retry').hidden=state.coverage?.state!=='error';$('#assessment-retry').disabled=locked()||pending||preview.inspectionDisabled;
}
function versionLabel(){
  const index=state.project?.versions.findIndex(v=>v.id===state.version?.id);
  $('#version-label').textContent=state.version?`版本 ${index+1} · ${state.version.color_count} 色${dirtyPalette()?' · 配色未保存':''}`:'';
}
let versionPage=0,versionStripWidth=0;
function displayVersionPage(page){
  const strip=$('#versions'),buttons=[...strip.children];
  if(!strip.clientWidth)return;
  const style=getComputedStyle(strip);
  const chipWidth=parseFloat(style.getPropertyValue('--version-chip-width'));
  const gap=parseFloat(style.columnGap);
  const capacity=Math.max(1,Math.floor((strip.clientWidth+gap)/(chipWidth+gap)));
  const pages=Math.max(1,Math.ceil(buttons.length/capacity));
  const active=buttons.findIndex(button=>button.getAttribute('aria-pressed')==='true');
  versionPage=Math.max(0,Math.min(pages-1,page??Math.floor(active/capacity)));
  buttons.forEach((button,index)=>{button.hidden=Math.floor(index/capacity)!==versionPage});
  $('#version-pages').hidden=pages===1;
  $('#version-page').textContent=(versionPage+1)+' / '+pages;
  $('#versions-back').disabled=versionPage===0;
  $('#versions-forward').disabled=versionPage===pages-1;
}
$('#versions-back').onclick=()=>displayVersionPage(versionPage-1);
$('#versions-forward').onclick=()=>displayVersionPage(versionPage+1);
new ResizeObserver(([entry])=>{
  if(entry.contentRect.width===versionStripWidth)return;
  versionStripWidth=entry.contentRect.width;
  if(versionStripWidth)displayVersionPage();
}).observe($('#versions'));
function renderVersions(){
  const versions=state.project?.versions||[];$('#versions').replaceChildren();
  versions.forEach((v,i)=>{
    const button=document.createElement('button');button.type='button';button.className='version';
    button.setAttribute('aria-pressed',String(state.version?.id===v.id));button.setAttribute('aria-label',`版本 ${i+1}：${v.prompt}`);
    button.disabled=locked()||dirtyPalette();const img=new Image();img.src=v.flat_url;img.alt=`版本 ${i+1} 的平面稿`;
    const label=v.kind==='fit'?`${fmt(v.fit_report.width)} mm 适配稿`:`版本 ${i+1} · ${v.color_count} 色`;
    button.append(img,document.createTextNode(label));
    button.onclick=()=>loadVersion(v.id).catch(e=>message(e.message,true));$('#versions').append(button);
  });
  versionLabel();
  const parent=versions.findIndex(v=>v.id===state.version?.parent_id);
  const note=$('#version-note');
  note.textContent=state.version?`${parent>=0?`基于版本 ${parent+1} · `:''}${state.version.kind==='palette'?'直接改色，轮廓保留':state.version.prompt}`:'';
  note.title=note.textContent;
  displayVersionPage();
}
function threadButtons(){for(const b of $$('[data-thread]'))b.setAttribute('aria-pressed',String(b.dataset.thread===state.thread))}
function scaledArtwork(width){
  const base=state.art,factor=width/base.meta.design_width_mm;
  const m={...base.meta,design_width_mm:width,design_height_mm:width*state.version.aspect_ratio,page_width_mm:width+10,page_height_mm:width*state.version.aspect_ratio+10,thread_spec:state.thread,row_spacing_mm:Number($('#spacing').value)};
  m.separated_objects=base.meta.separated_objects.map(o=>({...o,path:o.path.replace(/-?\d+(?:\.\d+)?/g,v=>((Number(v)-5)*factor+5).toFixed(3)),area_mm2:o.area_mm2*factor*factor,bbox:o.bbox.map(v=>(v-5)*factor+5)}));
  return {...base,meta:m,width:width+10,height:m.design_height_mm+10,stats:null,commands:[],prefix:[]};
}
async function loadVersion(vid,planId){
  clearTimeout(replanTimer);preview.stop();manualDraft=null;const token=++state.load,project=state.project;
  state.loading=true;availability();
  try{
  const v=project.versions.find(v=>v.id===vid);if(!v)throw new Error('版本不存在。');
  if(project.id!==state.repairProject||vid!==state.repairVersion){
    state.repairTrack=null;$('#craft-options').open=false;state.repairProject=project.id;state.repairVersion=vid;
  }
  const art=await api(v.preview_url);if(token!==state.load)return;
  const planKey='embroidery-plan:'+project.id+':'+v.id;
  const preferred=planId===undefined?storage.getItem(planKey):null;
  const plan=planId===undefined?(preferred==='flat'?null:v.plans.find(p=>p.id===preferred)||v.plans.at(-1)):v.plans.find(p=>p.id===planId);
  const planView=plan?await api(plan.preview_url):null;if(token!==state.load)return;
  const settings=plan||v.preferred_settings||{};
  if(splitEditor)splitEditor.advice=null;preview.splitSuggestions=null;
  $('#detail-preserve').hidden=art.meta.detail_mode==='preserve';$('#detail-advanced').hidden=art.meta.detail_mode==='preserve';
  const report=v.fit_report;
  $('#detail-review-note').textContent=report?fitSummary(report):`本版含 ${art.meta.separated_objects.length} 个色块。请核对主体、主要花纹和镂空。`;
  $('#detail-compare').textContent=v.kind==='fit'?'对照尺寸适配前后':'对照参考原图';
  const pixelMm=(settings.width||art.meta.design_width_mm)/(art.meta.source_width_px-1);
  if(Number.isFinite(pixelMm)&&pixelMm>referenceWidth(settings.thread)){
    $('#detail-review-note').textContent+=` 原图每个像素约占 ${pixelMm.toFixed(2)} mm。如需更细的花纹，可上传更清晰的原稿。`;
  }
  state.version=v;state.art=art;state.plan=plan||null;state.hasPlan=!!plan;state.paletteDraft=null;state.paletteInvalid=false;state.localChanged=false;
  state.plateMode=Object.values(plan?.object_overrides||{}).some(s=>s.source==='ai')?'ai':'rules';
  state.thread=settings.thread||'105D';$('#width').value=settings.width||60;$('#height').value=((settings.width||60)*v.aspect_ratio).toFixed(1);
  $('#adaptive-fill').checked=settings.adaptive_fill??true;
  $('#preview-thread').value=state.thread;preview.inspectionDisabled=false;
  let savedWidth;try{savedWidth=Number(storage.getItem(`thread-width:${project.id}:${v.id}:${plan?.id||'draft'}:${state.thread}`))}catch{}
  preview.threadWidth=validThreadWidth(savedWidth)?savedWidth:referenceWidth(state.thread);$('#preview-width').value=preview.threadWidth.toFixed(2);
  $('#fabric').value=settings.fabric||'twill';fabricNote();
  $('#spacing').value=settings.spacing||densityPresets[state.thread].normal[0];
  $('#satin-spacing').value=settings.satin_spacing||(plan?plan.spacing:densityPresets[state.thread].normal[1]);
  threadButtons();densityButtons();
  state.view=planView||scaledArtwork(Number($('#width').value));
  $('#empty-stage').hidden=true;$('#preview-scroll').hidden=false;$('#canvas').hidden=false;
  $('#project-title').textContent=project.title;
  setupComparison();preview.layer='all';$('#zoom').value='1';preview.zoom=1;$('#comparison').style.width='100%';
  preview.proofEnabled=false;preview.proofPosition=.5;$('#proof-enabled').checked=false;$('#proof-position').value='50';
  await preview.setData(state.view,v.flat_url,project.source_url);if(token!==state.load)return;
  await localEditor.load(plan);if(token!==state.load)return;
  artEditor.load(project,v);mode('artwork');renderVersions();renderColors();renderPalette();stats();wizard.go(2,true);
  loadSizeAdvice().catch(()=>{});
  storage.setItem('embroidery-project',project.id);storage.setItem('embroidery-version:'+project.id,v.id);
  if(planId!==undefined)storage.setItem(planKey,plan?.id||'flat');
  if(!state.busy&&!state.resume)message(plan?`已载入当前方案 · ${fmt(plan.stitch_count)} 针，可切换旧方案对比。`:'平面稿已载入，可调整配色、尺寸或制作绣花工程。');
  }finally{if(token===state.load){state.loading=false;availability()}}
}
async function loadProject(pid,{versionId,planId,resumeActive=true,openResult=false}={}){
  clearTimeout(replanTimer);const token=++state.load;state.loading=true;availability();
  let p;
  try{p=await api('/api/projects/'+pid)}
  catch(error){
    if(token!==state.load)return;
    if(state.project)$('#projects').value=state.project.id;
    state.loading=false;availability();throw error;
  }
  if(token!==state.load)return;
  state.project=p;state.creating=false;$('.workspace').classList.remove('creating');$('#projects').value=pid;
  $('#close-composer').hidden=true;$('#project-title').textContent=p.title;
  const saved=storage.getItem('embroidery-version:'+pid);
  const selected=versionId||(!openResult&&p.versions.some(v=>v.id===saved)?saved:p.current_version_id)||p.versions.at(-1)?.id;
  if(selected){
    const latest=openResult?p.versions.find(v=>v.id===selected)?.plans.at(-1):null;
    await loadVersion(selected,openResult?latest?.id??null:planId);
  }
  else{state.loading=false;state.version=null;state.art=null;state.view=null;state.plan=null;state.paletteDraft=null;state.paletteInvalid=false;preview.clear();$('#canvas').hidden=true;$('#preview-scroll').hidden=true;$('#empty-stage').hidden=false;renderVersions();renderColors();stats()}
  if(resumeActive&&state.project?.id===pid&&p.active_job_id&&!state.busy&&!state.resume)await watch({id:p.active_job_id,project_id:pid});
  else if(openResult&&state.project?.id===pid&&state.version&&!state.loading)wizard.go(state.plan?5:4,true);
}
async function watch(job){
  trackPlanJob(job);
  if(job.kind==='split_advice'&&!storage.getItem(jobModeKey(job.id)))storage.setItem(jobModeKey(job.id),state.plateMode);
  state.resume=null;state.job=job.id;storage.setItem('embroidery-job',job.id);busy(true,job.message||'正在处理');
  let finished=false,failed=false,failures=0;
  try{
    while(true){
      let current;
      try{current=await api('/api/jobs/'+job.id);failures=0}
      catch(e){if(++failures<=3){message('连接恢复中，正在继续查询已提交的任务…');await delay(1000*failures);continue}throw new Error('暂时连接不上服务。任务可能仍在处理，点击“继续查看进度”即可查询原任务。')}
      if(current.kind==='plan'&&activePlanJob?.id!==job.id){
        trackPlanJob(current);
        if(!job.kind&&state.version)wizard.go(4,true);
        availability();
      }
      message(current.message);
      if(current.status==='error'){finished=true;failed=true;if(current.generated_url){$('#raw-result').href=current.generated_url;$('#raw-result').hidden=false}throw new Error(current.message)}
      if(current.status==='done'){
        const r=current.result;
        if(current.kind==='split_advice'){finished=true;splitEditor.show(r.split_advice);message('拆区建议已核对。查看建议后采用，可继续手动调整。');return}
        if(current.kind==='craft_advice'){finished=true;showCraftAdvice(r.advice);message('AI 工艺建议已检查。查看逐区方案后采用，原针迹尚未改动。');return}
        await projects();await loadProject(r.project_id,{versionId:r.version_id,planId:r.plan_id});finished=true;
        const nextMode=current.kind==='plan'&&storage.getItem(nextModeKey(job.id));
        if(Object.hasOwn(plateNames,nextMode)&&current.project_id===r.project_id){
          wizard.go(4,true);selectPlateMode(nextMode);wizard.showPane('inspector',true);
          message('本次针版已保存，下一轮已选'+plateNames[nextMode]+'。');
          return;
        }
        if(current.kind==='plan'||(r.unattended&&r.plan_id)){
          wizard.go(5,true);
          message(state.plan?.quality_review?.status==='needs_review'?'针迹已生成，局部形状需要调整。':'机器针迹已生成，可查看覆盖与细节检查。');
        }else if(r.fit_stop){
          wizard.go(2,true);if(state.version?.change_map_url)showFitComparison(false);message(r.note,true);
        }else if(r.plan_pending){
          wizard.go(r.revision?2:4,true);message('平面稿已保存，尚未生成可试绣的文件。'+r.note,true);
        }else if(current.kind==='fit'){
          if(state.version?.kind==='fit')showFitComparison();
          message(state.version?.change_map_url?'尺寸适配完成。玫红色标出实际改动，可取消标记查看平面稿。':'尺寸检查完成，可核对图案后生成针迹。');
        }else{
          window.scrollTo({top:0,behavior:'smooth'});
          const notices={palette:'新配色已保存，可对照两个版本。',import:'平面稿已分色，可以核对图案并制作绣花工程。'};
          message(notices[current.kind]||'平面稿已生成，可以继续用文字修改，或制作绣花工程。');
        }
        return;
      }
      await delay(800);
    }
  }finally{
    state.job=null;
    if(finished){
      const nextMode=storage.getItem(nextModeKey(job.id));
      if(failed&&activePlanJob?.project===state.project?.id&&Object.hasOwn(plateNames,nextMode)&&state.version){
        wizard.go(4,true);selectPlateMode(nextMode);wizard.showPane('inspector',true);
      }
      storage.removeItem(nextModeKey(job.id));storage.removeItem(jobModeKey(job.id));
      storage.removeItem('embroidery-job');state.resume=null;activePlanJob=null;
    }else state.resume=job;
    busy(false);
  }
}
function treatment(){return uploadRoute(state.mode,state.sourceShape,$('#create-prompt').value)}
function generateLabel(){
  const route=treatment(),plan=$('#create-route').value==='auto';
  if(route==='flat')return plan?'按尺寸自动制版 →':'按尺寸直接分色 →';
  if(route==='text')return plan?'AI 生成图案并制版 →':'用 AI 生成平面稿 →';
  return plan?'AI 修复并自动制版 →':'用 AI 整理平面稿 →';
}
function createMode(value){
  state.mode=value;$$('[data-source]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.source===(value==='text'?'text':'image'))));
  $('#image-treatment').hidden=value==='text';if(value!=='text')$('#image-treatment-select').value=value;const route=treatment(),source=state.sourceShape;
  $('#flat-background-field').hidden=route!=='flat'||!!source?.subjectAspect;
  $('#image-detail-options').hidden=value==='text';
  $('#treatment-note').textContent=route==='flat'?(value==='auto'?'这张图以纯色色块为主，可以直接分色和排针，免生图费用。':'使用原图色块，免生图费用。'):
    value==='auto'&&source?.flat?'已填写修改要求，本次将调用 AI 修改图案，再按尺寸排针。':'本次将调用 AI 整理平面稿，按生图服务计费。';
  $('#upload-area').hidden=value==='text';$('#create-prompt-label').textContent=value==='flat'?'作品名称（选填）':value!=='text'?'图案修改要求（选填）':'想生成什么图案？';
  $('#create-prompt').placeholder=value==='flat'?'例如：小猫绣章。此模式直接分色，不按文字重绘。':value!=='text'?'如需改图，写下要改的地方，例如把围巾改成蓝色。':'例如：戴蓝色蝴蝶结的橘色小猫头像，粉色腮红，开心微笑。';
  $('#generate').textContent=generateLabel();
  $('#upload-preview').alt=value==='flat'?'待分色的平面稿':'即将修复的参考图片';
  $('#style-rule').textContent=value==='text'?`自动整理为简洁色块 · 圆润边缘 · 最多 ${$('#max-colors').value} 色`:value==='flat'?'直接整理色块，白色默认保留。':($('#detail-mode').value==='preserve'?'归并线色 · 尽量保留花纹 · 规整平面稿':'简化颜色和装饰 · 清楚大色块 · 规整平面稿');
}
function newDesign(){
  if(locked()||dirtyPalette())return;
  clearTimeout(replanTimer);++state.load;state.backProject=state.project?.id;state.upload=null;state.repairTrack=null;localEditor.clear();
  $('#upload').value='';$('#upload-preview').hidden=true;$('#upload-label').textContent='选择图片';$('#clear-upload').hidden=true;
  state.creating=true;state.plan=null;state.version=null;state.view=null;state.art=null;state.hasPlan=false;state.paletteDraft=null;state.paletteInvalid=false;
  preview.clear();mode('artwork');$('.workspace').classList.add('creating');$('#canvas').hidden=true;$('#preview-scroll').hidden=true;$('#empty-stage').hidden=false;
  $('[data-view="original"]').hidden=true;$('[data-view="compare"]').hidden=true;
  $('#project-title').textContent='新图案';$('#version-label').textContent='';$('#version-note').textContent='';$('#caption').textContent='';
  $('#versions').replaceChildren();$('#colors').replaceChildren();$('#create-prompt').value='';$('#edit-prompt').value='';$('#width').value='60';$('#height').value='';
  $('#create-route').value='auto';$('#create-width').value='';$('#create-thread').value='105D';state.sourceShape=null;creationPane(false);
  state.thread='105D';$('#fabric').value='twill';fabricNote();threadButtons();applyDensity();
  state.plateMode='rules';state.craftTool='split';$('#split-ai-options').open=false;$('#craft-ai-options').open=false;
  $('#flat-background').value='preserve';createMode('auto');stats();wizard.go(1,true);window.scrollTo({top:0,behavior:'smooth'});message('第1步：先选择图片或输入描述。');
}
function paramsChanged(commit=false){
  if(!state.version||!state.art||locked()||dirtyPalette())return;
  clearTimeout(replanTimer);const width=Number($('#width').value);if(commit||document.activeElement!==$('#height'))$('#height').value=width>0?(width*state.version.aspect_ratio).toFixed(1):'';
  state.plan=null;
  densityButtons();
  if(validParams()){
    if(commit)localEditor.invalidateStaleAI();
    const wasNeedleView=['simulation','result','coverage'].includes(preview.mode);
    state.view=scaledArtwork(width);preview.setData(state.view,state.version.flat_url,state.project.source_url).catch(e=>message(e.message,true));
    localEditor.resize(width,width*state.version.aspect_ratio);
    if(wasNeedleView)mode('artwork');renderColors();stats();
    message('参数已更新。确认后点击“生成针迹，查看模拟”。');updateSizeAdvice();
    // Guided workflow: changing inputs does not repeatedly regenerate a plan.
  }else{stats();message('宽度20–160 mm；填充行距0.15–0.60 mm；包边间距0.18–0.80 mm。',true)}
}
async function make({sameArtwork=false,border=null}={}){
  clearTimeout(replanTimer);if(locked()||!state.version||state.creating||dirtyPalette()||localEditor.pending||localEditor.drawing||!validParams())return;
  busy(true,'准备制作绣花工程');
  const local=localEditor.payload();
  if(border)local.object_overrides[border.id]={method:'satin',pull_compensation:0,satin_spacing:border.pitch};
  const settings=currentSettings();
  const before=manualDraft?.body.version_id===state.version.id&&manualDraft.width===settings.width&&manualDraft.thread===settings.thread&&manualDraft.fabric===settings.fabric?manualDraft.body:null;
  try{const job=await api(`/api/projects/${state.project.id}/plans`,{version_id:state.version.id,auto_simplify:!before&&!sameArtwork&&$('#auto-simplify').checked,...settings,...local,revision_from:before});await watch(job)}
  catch(e){message(e.message,true);busy(false)}
}
const stable=value=>JSON.stringify(value,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
function craftSnapshot(){return {project_id:state.project?.id,version_id:state.version?.id,...currentSettings(),...localEditor.payload()}}
function showCraftAdvice(advice){
  state.craftAdvice=advice;state.craftTool='needle';state.plateMode='ai';$('#craft-options').open=true;$('#craft-ai-options').open=true;$('#ai-craft-results').replaceChildren();
  const labels={angle:'针向 °',fill_spacing:'填针行距 mm',satin_spacing:'缎面间距 mm',underlay:'底针',max_stitch_length:'最大针长 mm',pull_compensation:'拉缩补偿 mm',running_stitch_length:'走针针长 mm',running_tolerance:'容差 mm',running_repeats:'整线重复',bean_repeats:'回针次数'};
  for(const item of advice.suggestions){
    const row=document.createElement('div');row.className='ai-craft-row';
    const pick=document.createElement('input');pick.type='checkbox';pick.checked=true;pick.value=item.id;pick.setAttribute('aria-label','采用'+(item.part||item.color)+'的建议');pick.onchange=availability;
    const title=document.createElement('button');title.className='text-button';title.textContent=[item.part,item.color,item.method_label,item.sewing_label].filter(Boolean).join(' · ');
    title.onclick=()=>localEditor.select(item.id);
    const reason=document.createElement('p');reason.textContent=item.reason+(item.split_hint?' 建议分区：'+item.split_hint:'');
    const params=document.createElement('small');params.textContent=Object.entries(item.settings).filter(([k])=>Object.hasOwn(labels,k)).map(([k,v])=>`${labels[k]} ${typeof v==='string'?({auto:'自动',none:'无',light:'轻底针',full:'加强底针'}[v]||v):v}`).join(' · ');
    row.append(pick,title,reason,params);$('#ai-craft-results').append(row);
  }
  $('#ai-craft-status').textContent=(advice.region_id?'已分析选中色块':'已分析 '+(advice.reviewed_count??advice.suggestions.length)+' 块')+` · 可采用 ${advice.suggestions.length} 块 · ${advice.rule_count||0} 块沿用规则 · 已有设置 ${advice.protected_count} 块${advice.usage.cached?' · 已复用分析结果':''}`;
  if(advice.incomplete)$('#ai-craft-status').textContent+='。部分分组未完成。可先采用本次建议，或再次点击分析继续处理剩余分组。';
  $('#ai-craft-apply').hidden=!advice.suggestions.length;
}
async function startCraftAdvice(regionId=null){
  if(state.plateMode!=='ai'||locked()||dirtyPalette()||localEditor.pending||localEditor.drawing||!validParams()||!localEditor.regions.length)return;
  busy(true,'正在提交 AI 工艺分析');state.craftAdvice=null;$('#ai-craft-results').replaceChildren();$('#ai-craft-apply').hidden=true;
  $('#ai-craft-status').textContent='正在分析，现有针迹保持不变…';
  const body={...craftSnapshot(),instruction:$('#ai-craft-prompt').value};
  if(regionId)body.region_id=regionId;
  try{const job=await api(`/api/projects/${state.project.id}/craft-advice`,body);await watch(job)}
  catch(e){message(e.message,true);$('#ai-craft-status').textContent=e.message;busy(false)}
}
$('#ai-craft-start').onclick=()=>startCraftAdvice();
$('#ai-craft-selected').onclick=()=>{if(!$('#ai-craft-selected').disabled)startCraftAdvice(localEditor.selected)};
function applySelectedAdvice(){
  const advice=state.craftAdvice;if(!advice||stable(advice.snapshot)!==stable(craftSnapshot()))return;
  const ids=new Set($$('#ai-craft-results input:checked').map(input=>input.value));
  if(ids.size&&localEditor.applyAdvice(advice.suggestions.filter(item=>ids.has(item.id)))){
    state.craftAdvice=null;$('#ai-craft-results').replaceChildren();$('#ai-craft-apply').hidden=true;
    $('#ai-craft-status').textContent=`已采用 ${ids.size} 块建议，可继续手动微调。更新工程后可查看真实绣序和落针。`;
    availability();return true;
  }
  return false;
}
$('#ai-craft-apply').onclick=applySelectedAdvice;
function aiNextAction(){
  if(state.plateMode!=='ai'||!localEditor?.regions.length||!state.version)return null;
  if(state.craftAdvice&&stable(state.craftAdvice.snapshot)===stable(craftSnapshot())){
    const count=$$('#ai-craft-results input:checked').length;
    return {label:count?`采用 ${count} 块建议并生成针迹 →`:'按现有设置生成针迹 →',run:()=>{if(count&&!applySelectedAdvice())return;make()}};
  }
  const settings=Object.values(localEditor.overrides);
  if(settings.some(s=>s.source==='ai')||settings.length===localEditor.regions.length)return null;
  return {label:'用 AI 分析整张图（按调用计费）→',run:()=>{mode('local');startCraftAdvice()}};
}
splitEditor=new SplitEditor({snapshot:craftSnapshot,context:()=>({useAI:state.plateMode==='ai',locked:locked()||dirtyPalette(),pending:localEditor.pending||localEditor.drawing,valid:validParams(),visible:!!state.version&&!state.creating&&wizard?.step===4&&state.craftTool==='split'}),request:api,watch,busy,message,local:localEditor,preview,mode});
function selectPlateMode(nextMode){
  const useAI=nextMode==='ai';
  if(state.plateMode!==nextMode){
    state.plateMode=nextMode;
    localEditor.switchPlanMode(useAI);state.craftAdvice=null;splitEditor.clearProposal();
    $('#ai-craft-results').replaceChildren();$('#ai-craft-apply').hidden=true;$('#ai-craft-status').textContent='';
  }
  $('#craft-ai-options').open=false;$('#split-ai-options').open=useAI;
  $('#craft-options').open=useAI;
  mode(useAI?'local':'layers');
}
$$('[data-plate-mode]').forEach(b=>b.onclick=()=>{
  const choice=b.dataset.plateMode;
  if(b.disabled)return;
  if(activePlanJob&&activePlanJob.project===state.project?.id&&(state.busy||state.resume)){
    if(choice===activePlanJob.mode)storage.removeItem(nextModeKey(activePlanJob.id));
    else storage.setItem(nextModeKey(activePlanJob.id),choice);
    availability();return;
  }
  if(!locked()&&!dirtyPalette()&&!localEditor.pending&&!localEditor.drawing&&choice!==state.plateMode)selectPlateMode(choice);
});
$('#plate-resolve').onclick=()=>{
  if(dirtyPalette()){wizard.go(2,true);wizard.showPane('inspector',true);wizard.showInspectorTool('palette');$('#palette-cancel').focus();return}
  if(localEditor.drawing){wizard.showPane('canvas',true);$('#canvas').scrollIntoView({block:'nearest'});return}
  if(localEditor.pending){wizard.showPane('inspector',true);$('#local-apply').focus()}
};
$$('[data-craft-tool]').forEach(b=>b.onclick=()=>{if(locked()||dirtyPalette()||localEditor.pending||localEditor.drawing)return;mode(b.dataset.craftTool==='split'?'layers':'local')});
$('#split-open-manual').onclick=$('#craft-open-manual').onclick=()=>{mode('local');wizard.showPane('canvas',true)};
$('#detail-compare').onclick=()=>{
  if(state.version?.kind==='fit'){showFitComparison();return}
  mode('compare');$('#compare-source').value='original';updateComparison();wizard.showPane('canvas',true)
};
$('#detail-preserve').onclick=async()=>{
  if(locked()||dirtyPalette()||localEditor.pending||localEditor.drawing)return;
  busy(true,'正在保留细节重新分色');
  try{await watch(await api('/api/projects/'+state.project.id+'/preserve-details',{parent_version_id:state.version.id}))}
  catch(e){message(e.message,true);busy(false)}
};
$('#new').textContent='新建图案';$('#new').onclick=newDesign;
$('#close-composer').onclick=()=>{if(state.backProject)loadProject(state.backProject).catch(e=>message(e.message,true))};
$$('[data-source]').forEach(b=>b.onclick=()=>createMode(b.dataset.source==='text'?'text':$('#image-treatment-select').value));
$('#create-prompt').oninput=()=>{if(state.mode==='auto')createMode('auto')};
$('#image-treatment-select').onchange=e=>{createMode(e.target.value);createSizeNote()};
$$('[data-view]').forEach(b=>b.onclick=()=>mode(b.dataset.view));
$$('[data-prompt]').forEach(b=>b.onclick=()=>{$('#edit-prompt').value=b.dataset.prompt;$('#edit-prompt').focus()});
$('#projects').onchange=e=>loadProject(e.target.value).catch(e=>message(e.message,true));
$('#compare-source').onchange=updateComparison;
$('#fit-marks').onchange=updateComparison;
$('#zoom').onchange=e=>{preview.setZoom(e.target.value);$('#comparison').style.width=Number(e.target.value)*100+'%';$('#preview-scroll').scrollTo({top:0,left:0})};
$('#open-palette').onclick=()=>{wizard.showInspectorTool('palette');$('[data-inspector-tab="palette"]').focus()};
$('#palette-cancel').onclick=cancelPalette;
$('#palette-save').onclick=async()=>{
  if(locked()||!state.paletteDraft||state.paletteInvalid)return;
  busy(true,'正在保存新配色');
  try{const job=await api(`/api/projects/${state.project.id}/palette`,{parent_version_id:state.version.id,colors:state.paletteDraft,settings:currentSettings()});await watch(job)}
  catch(e){message(e.message,true);busy(false)}
};
$('#upload').onchange=async event=>{
  const file=event.target.files[0];if(!file)return;
  if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>8_000_000){message('请选择8 MB以内的PNG、JPG或WebP图片。',true);event.target.value='';return}
  let uri;
  try{uri=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('图片未能读取，请重新选择。'));reader.readAsDataURL(file)})}
  catch(e){message(e.message,true);return}
  if(!state.creating||event.target.files[0]!==file||state.mode==='text')return;
  const original=new Image();original.src=uri;
  try{await original.decode()}catch{message('图片无法读取，请重新选择。',true);return}
  if(!state.creating||event.target.files[0]!==file||state.mode==='text')return;
  state.upload=uri;state.sourceShape=sourceShape(original);createMode(state.mode);
  $('#upload-preview').src=state.upload;$('#upload-preview').hidden=false;$('#upload-label').textContent=file.name;$('#upload-label').title=file.name;$('#clear-upload').hidden=false;
  creationPane(true);message('已选原图。填好成品宽度后，再开始简化。');
};
$('#choose-size').onclick=()=>{
  if(locked())return;
  if(state.mode==='text'&&!$('#create-prompt').value.trim()){message('先输入想画的图案。',true);$('#create-prompt').focus();return}
  if(state.mode!=='text'&&!state.upload){$('#upload').click();return}
  creationPane(true);message('确定成品宽度和线粗，再按此尺寸出稿。');
};
$('#back-to-source').onclick=()=>{if(!locked())creationPane(false)};
$('#clear-upload').onclick=()=>$('#upload').click();
$('#generate').onclick=async()=>{
  if(locked())return;const prompt=$('#create-prompt').value.trim();
  const field=$('#create-width');
  if(!field.validity.valid){message('请先填写成品宽度，范围 20–160 mm，精确到 0.1 mm。',true);field.focus();return}
  const settings={width:field.valueAsNumber,thread:$('#create-thread').value,fabric:'twill'};
  if(state.mode!=='text'&&!state.upload){message('请先选择一张图片。',true);return}
  if(state.mode==='text'&&!prompt){message('请先描述想生成的图案。',true);$('#create-prompt').focus();return}
  const route=treatment();
  busy(true,route==='flat'?'正在导入平面稿':'正在提交生成请求');
  try{
    const job=await api('/api/projects',{mode:route,prompt,settings,auto_plan:$('#create-route').value==='auto',max_colors:Number($('#max-colors').value),detail_mode:state.mode==='text'?'simplify':$('#detail-mode').value,background_mode:$('#flat-background').value,...(state.mode!=='text'?{image_data:state.upload}:{})});
    state.project=null;await watch(job);state.upload=null;$('#upload').value='';$('#upload-preview').hidden=true;$('#upload-label').textContent='选择图片';$('#clear-upload').hidden=true;
  }catch(e){message(e.message,true);busy(false)}
};
$('#create-route').onchange=()=>{$('#generate').textContent=generateLabel()};
$('#detail-mode').onchange=()=>createMode(state.mode);
$('#create-width').oninput=createSizeNote;$('#create-thread').onchange=createSizeNote;
$('#flat-background').onchange=createSizeNote;
$('#max-colors').onchange=()=>createMode(state.mode);
$('#create-resume').onclick=()=>$('#resume-job').click();
$('#detail-simplify').onclick=()=>{
  if(locked())return;
  wizard.go(4);updateDetailFit();wizard.showPane('inspector',true);$('#detail-fit-panel').scrollIntoView({block:'nearest'});
};
$('#edit').onclick=async()=>{
  if(locked()||!state.version||dirtyPalette())return;
  const prompt=$('#edit-prompt').value.trim();if(!prompt){message('请填写想修改的部分。',true);$('#edit-prompt').focus();return}
  let editOptions;try{editOptions=artEditor.payload()}catch(e){message(e.message,true);return}
  busy(true,'正在提交修改要求');
  try{const job=await api(`/api/projects/${state.project.id}/versions`,{parent_version_id:state.version.id,prompt,max_colors:Number($('#edit-max-colors').value),detail_mode:$('#edit-detail-mode').value,settings:currentSettings(),...editOptions});artEditor.submitted();await watch(job)}
  catch(e){message(e.message,true);busy(false)}
};
$('#resume-job').onclick=()=>{if(state.resume)watch(state.resume).catch(e=>message(e.message,true))};
$('#fit-details').onclick=async()=>{
  if(locked()||dirtyPalette()||localEditor.pending||localEditor.drawing||!validParams())return;
  busy(true,'正在按尺寸自动取舍难绣细节');
  try{await watch(await api(`/api/projects/${state.project.id}/fit-details`,{parent_version_id:state.version.id,...currentSettings()}))}
  catch(e){message(e.message,true);busy(false)}
};
$('#make').onclick=make;
$('#refit-source').onclick=()=>$('#fit-details').click();
$('#apply-size-advice').onclick=()=>{if(!sizeAdvice||locked())return;$('#width').value=sizeAdvice.profiles[state.thread].width_mm;paramsChanged(true)};
$('#local-make').onclick=make;
$('#plan-history').onchange=e=>loadVersion(state.version.id,e.target.value).then(()=>wizard.go(5,true)).catch(e=>message(e.message,true));
$('#width').oninput=()=>paramsChanged(false);$('#width').onchange=()=>paramsChanged(true);
$('#fabric').onchange=()=>{fabricNote();paramsChanged(true)};
$('#height').oninput=()=>{if(!state.version)return;$('#width').value=(Number($('#height').value)/state.version.aspect_ratio).toFixed(1);paramsChanged(false)};
$('#height').onchange=()=>paramsChanged(true);
$('#spacing').oninput=()=>paramsChanged(false);$('#spacing').onchange=()=>paramsChanged(true);
$('#adaptive-fill').onchange=()=>paramsChanged(true);
$('#satin-spacing').oninput=()=>paramsChanged(false);$('#satin-spacing').onchange=()=>paramsChanged(true);
$$('[data-density]').forEach(b=>b.onclick=()=>{applyDensity(b.dataset.density);paramsChanged(true)});
function changeThread(thread){
  if(locked()||!THREAD_PROFILES[thread]||thread===state.thread)return;
  const selected=$('[data-density][aria-pressed="true"]')?.dataset.density||'normal';state.thread=thread;applyDensity(selected);threadButtons();
  $('#preview-thread').value=thread;$('#preview-width').value=referenceWidth(thread).toFixed(2);preview.inspectionDisabled=false;preview.setThreadWidth(referenceWidth(thread));
  paramsChanged(true);if(wizard.step===5)wizard.go(3,true);
}
$$('[data-thread]').forEach(b=>b.onclick=()=>changeThread(b.dataset.thread));
$('#preview-thread').onchange=e=>changeThread(e.target.value);
$('#preview-width').oninput=e=>{
  const value=Number(e.target.value);e.target.setAttribute('aria-invalid',String(!validThreadWidth(value)));
  if(!validThreadWidth(value)){preview.inspectionDisabled=true;preview.invalidateCoverage();preview.onCoverage({state:'error',error:'参考线宽需要在 0.10–0.45 mm 之间，当前输入尚未应用。'});return}
  preview.inspectionDisabled=false;preview.setThreadWidth(value);preview.render();
  try{storage.setItem(`thread-width:${state.project.id}:${state.version.id}:${state.plan?.id||'draft'}:${state.thread}`,String(value))}catch{}
};
$('#preview-material').onchange=e=>{preview.material=e.target.value;if(preview.material==='plain'&&preview.threadRenderPending)preview.invalidateThreads();$('#render-status').textContent=preview.material==='plain'?'显示工程中的实际针路；露底检查使用同一参考线宽。':'绣线预览由当前电脑绘制，采用工程中的实际针迹。';preview.render()};
$('#preview-fabric').onchange=e=>{preview.fabricColor=e.target.value;preview.render()};
$('#assessment-coverage').onclick=()=>{mode('coverage');wizard.showPane('canvas',true)};
$('#repair-focus').onclick=()=>{if(state.repairFocus)focusProblem(state.repairFocus,true)};
$('#repair-upgrade').onclick=upgradePlan;
$('#repair-border').onclick=()=>{
  const trial=state.borderTrial;if(!trial||locked())return;
  rememberRepair(trial.id);make({sameArtwork:true,border:trial});
};
$('#repair-restore').onclick=()=>{
  const previous=state.repairTrack?.planId;
  if(!locked()&&state.version?.plans.some(p=>p.id===previous))loadVersion(state.version.id,previous).then(()=>wizard.go(5,true)).catch(e=>message(e.message,true));
};
$('#repair-apply-plan').onclick=()=>{if(!$('#repair-apply-plan').disabled&&localEditor.apply())make({sameArtwork:true})};
$('#repair-return-art').onclick=()=>{if(!locked()&&!localEditor.pending&&!localEditor.drawing){wizard.go(2);wizard.showPane('inspector',true)}};
$('#edit-picked').onclick=()=>wizard.showPane('inspector',true);
for(const id of ['#local-draw-guide','#local-cut','#local-multi'])$(id).addEventListener('click',()=>{
  if(localEditor.drawing||localEditor.multi)wizard.showPane('canvas',true);
});
$('#proof-enabled').onchange=e=>{preview.proofEnabled=e.target.checked;$('#proof-range').hidden=!e.target.checked;preview.render()};
$('#proof-position').oninput=e=>preview.setProofPosition(Number(e.target.value)/100);
preview.onProofPosition=value=>{$('#proof-position').value=String(Math.round(value*100))};
$('#assessment-reset-width').onclick=()=>{
  const width=state.plan?.quality_review?.reference_width_mm;if(!validThreadWidth(width)||locked())return;
  const input=$('#preview-width');input.value=width.toFixed(2);input.oninput({target:input});
};
$('#assessment-retry').onclick=()=>{if(locked()||preview.inspectionDisabled)return;preview.invalidateCoverage();preview.inspectCoverage()};
$('#toggle-coverage').onclick=()=>{mode(preview.mode==='coverage'?'result':'coverage');wizard.showPane('canvas',true)};
$('#coverage-rebuild').onclick=make;
$('#layer').onchange=e=>{preview.layer=e.target.value;preview.render()};
$('#opacity').oninput=e=>{preview.opacity=Number(e.target.value)/100;$('#opacity-value').textContent=e.target.value+'%';preview.render()};
$('#play').onclick=()=>preview.toggle();$('#step').onclick=()=>preview.step(1);$('#step-back').onclick=()=>preview.step(-1);$('#restart').onclick=()=>preview.seek(0);
$('#progress').oninput=e=>preview.seek(e.target.value);
$('#go-needle').onclick=()=>preview.seek($('#needle-number').value);
$('#needle-number').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();preview.seek(e.target.value)}};
$('#speed').onchange=e=>preview.speed=Number(e.target.value);
$('#jumps').onchange=e=>{preview.showJumps=e.target.checked;preview.render()};
$('#sewing-stage').onchange=e=>{const block=preview.blocks[Number(e.target.value)];if(block)preview.seek(block.start+1)};
$('#logout').onclick=async()=>{try{await api('/api/auth',{action:'logout'});location.replace('/login')}catch(e){message(e.message,true)}};
addEventListener('pageshow',e=>{if(e.persisted)location.reload()});
try{
  const session=await api('/api/auth');
  if(session.enabled&&!session.user){location.replace('/login');throw new Error('请先登录。')}
  if(session.user){storagePrefix=`account:${session.user.id}:`;$('#account-menu').hidden=false;$('#account-link').hidden=false;$('#account-link').textContent=session.user.display_name;$('#admin-link').hidden=session.user.role!=='admin';$('#logout').hidden=false}
  const items=await projects(),savedJob=storage.getItem('embroidery-job');let pending=null;
  const entry=new URLSearchParams(location.search);
  const linked=items.find(p=>p.id===entry.get('project'));
  if(savedJob){try{pending=await api('/api/jobs/'+savedJob)}catch(e){if(e.status===404){storage.removeItem('embroidery-job');storage.removeItem(nextModeKey(savedJob));storage.removeItem(jobModeKey(savedJob))}else throw e}}
  if(pending&&(!linked||pending.project_id===linked.id)){
    if(['plan','craft_advice','split_advice'].includes(pending.kind)&&items.some(p=>p.id===pending.project_id)){
      await loadProject(pending.project_id,{versionId:pending.result?.version_id,resumeActive:false});
      if(state.version){
        wizard.go(4,true);
        if(pending.kind==='craft_advice')selectPlateMode('ai');
        if(pending.kind==='split_advice'){
          const saved=storage.getItem(jobModeKey(pending.id));
          if(Object.hasOwn(plateNames,saved))selectPlateMode(saved);
          mode('layers');
        }
      }
    }
    await watch(pending);
  }
  else if(items.length){
    const saved=storage.getItem('embroidery-project');
    const target=linked||items.find(p=>p.id===saved)||items[0];
    await loadProject(target.id,{openResult:!!linked&&entry.get('view')==='result'});
    if(wizard.step===2)message('第2步：检查当前平面稿，满意后确认图案。');
  }
  else newDesign();
}catch(e){message(e.message,true);busy(false)}
