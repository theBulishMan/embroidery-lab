import {reviewRegion} from './detail-fit.js';
const $=s=>document.querySelector(s);
const clone=x=>JSON.parse(JSON.stringify(x));
const area=n=>n.toFixed(n<1?3:1);
const settingsKey=value=>JSON.stringify(Object.keys(value).sort().map(k=>[k,value[k]]));
const fields={angle:'#local-angle',fill_spacing:'#local-fill-spacing',satin_spacing:'#local-satin-spacing',pull_compensation:'#local-pull',max_stitch_length:'#local-max-stitch',running_stitch_length:'#local-run-length',running_tolerance:'#local-run-tolerance',running_repeats:'#local-run-repeats',bean_repeats:'#local-bean-repeats'};

export function narrowSatinTrial(region,meta,actual,settings){
  const report=meta?.quality_review,line=report?.reference_width_mm,width=region.recommendation?.width_mm;
  if(meta?.fabric!=='twill'||!line||!Number.isFinite(meta.satin_spacing_mm)||Object.keys(settings).length||
    region.detail_metrics?.hole_count!==0||region.recommendation?.structure!=='band'||
    !Number.isFinite(width)||width<1.8*line||width>.55||
    actual.length!==1||actual[0].stitch_type!=='三重走针')return null;
  const issue=report.issues?.find(i=>i.region_key===region.id);
  if(!(issue?.interior_mismatch_percent>=18))return null;
  // Offer a local trial only after the exported running path lost interior colour.
  // Keep a tighter project pitch and limit expansion into neighbouring shapes.
  return {pitch:Number(Math.max(.18,Math.min(meta.satin_spacing_mm,line*1.4)).toFixed(2)),pull:.02,loss:issue.interior_mismatch_percent};
}

export class LocalEditor{
  constructor({preview,context,request,onChange,onState,onMode}){
    Object.assign(this,{preview,context,request,onChange,onState,onMode});
    this.regions=[];this.cuts=[];this.overrides={};this.history=[];this.selected=null;this.pending=false;this.working=false;this.sequence=0;this.drawing=null;this.dots=[];
    this.selectedIds=[];this.multi=false;
    preview.onRegionPick=(id,options)=>this.select(id,options);preview.onLocalPoint=point=>this.point(point);
    $('#local-single').onclick=()=>this.setMulti(false);$('#local-multi').onclick=()=>this.setMulti(!this.multi);$('#local-merge').onclick=()=>this.merge();
    $('#local-region').onchange=e=>this.select(e.target.value);
    for(const selector of ['#local-method','#local-underlay','#local-role',...Object.values(fields)])$(selector).oninput=()=>{this.pending=true;this.status('参数尚未应用，请点击“应用到选区”。');this.updateFields();this.paint();this.onState()};
    $('#local-apply').onclick=()=>this.apply();
    $('#local-cancel-edit').onclick=()=>{this.cancelDraw(false);this.form();this.status('已恢复选区上次应用的设置。')};
    $('#local-reset-region').onclick=()=>{if(!this.selected)return;this.remember();delete this.overrides[this.selected];this.commit('选区已恢复自动设置，重新制作工程后生效。')};
    $('#local-draw-guide').onclick=()=>this.begin('guide');$('#local-cut').onclick=()=>this.begin('cut');
    $('#local-cancel-draw').onclick=()=>{this.cancelDraw();this.status('画线已取消。')};
    $('#local-undo').onclick=()=>this.undo();$('#local-reset-all').onclick=()=>this.reset();
  }
  payload(){return {region_cuts:clone(this.cuts),object_overrides:clone(this.overrides)}}
  physicalKey(){const c=this.context();return JSON.stringify([c.project_id,c.version_id,c.width,c.thread,c.fabric,c.spacing,c.satin_spacing])}
  clear(){++this.sequence;this.regions=[];this.cuts=[];this.overrides={};this.parkedAI=null;this.aiContext=null;this.history=[];this.selected=null;this.selectedIds=[];this.multi=false;this.pending=false;this.drawing=null;this.dots=[]}
  invalidateStaleAI(){
    if(!this.aiContext||this.aiContext===this.physicalKey())return;
    this.aiContext=null;this.history=[];
    for(const [id,s] of Object.entries(this.overrides))if(s.source==='ai')delete this.overrides[id];
    this.form();
  }
  switchPlanMode(useAI){
    const key=JSON.stringify([this.physicalKey(),this.cuts]);
    let changed=false;
    if(useAI&&this.parkedAI?.key===key){
      for(const [id,value] of Object.entries(this.parkedAI.settings)){
        if(!this.overrides[id]&&this.regions.some(r=>r.id===id)){this.overrides[id]=value;changed=true}
      }
      this.parkedAI=null;this.aiContext=this.physicalKey();
    }else if(!useAI){
      const settings={};
      for(const [id,value] of Object.entries(this.overrides))if(value.source==='ai'){
        settings[id]=value;delete this.overrides[id];changed=true;
      }
      this.parkedAI={key,settings};this.aiContext=null;
    }
    if(changed){this.history=[];this.form();this.onChange()}
    return changed;
  }
  selectedRegion(){return this.regions.find(r=>r.id===this.selected)}
  status(text,error=false){$('#local-status').textContent=text;$('#local-status').classList.toggle('local-error',error)}
  remember(){this.history.push({...this.payload(),selected:this.selected,selectedIds:[...this.selectedIds],multi:this.multi});if(this.history.length>30)this.history.shift()}
  async fetchCatalog(cuts=this.cuts){const c=this.context();return this.request(`/api/projects/${c.project_id}/regions`,{version_id:c.version_id,width:c.width,thread:c.thread,region_cuts:cuts})}
  async load(plan){
    const saved=clone(plan||{});this.clear();
    const token=++this.sequence;this.cuts=saved.region_cuts||[];this.overrides=saved.object_overrides||{};this.cancelDraw(false);
    if(Object.values(this.overrides).some(s=>s.source==='ai'))this.aiContext=this.physicalKey();
    this.working=true;let data;
    try{data=await this.fetchCatalog()}
    catch(e){
      if(token!==this.sequence)return;
      if(e.status===429||e.status===503){
        this.analysisPending=false;this.analysisError=e.message;
        $('#local-region').replaceChildren();this.paint();
        this.status('色块分析暂缓；图案和已有针迹可继续查看。点击“色块针法”重试。',true);this.onState();return;
      }
      throw e;
    }finally{if(token===this.sequence){this.working=false;this.onState()}}
    if(token!==this.sequence)return;
    this.install(data);this.selected=this.regions.reduce((a,b)=>a.area_mm2>b.area_mm2?a:b,this.regions[0])?.id;
    this.selectedIds=[this.selected];this.multi=false;
    this.form();this.status(`${Object.keys(this.overrides).length} 个局部设置 · ${this.cuts.length} 次分区调整。修改后会保存为新排针方案。`);this.onState();
  }
  install(data){
    this.analysisPending=false;this.analysisError=null;
    this.width=data.width;this.height=data.height;this.thread=data.thread||this.context().thread;this.colors=data.colors;this.regions=data.regions;
    const sorted=[...this.regions].sort((a,b)=>a.color-b.color||b.area_mm2-a.area_mm2);
    $('#local-region').replaceChildren(...sorted.map((r,i)=>new Option(`${i+1}. ${this.colors[r.color].name} · ${r.recommendation?.label||'自动'} · ${area(r.area_mm2)} mm²${r.parent_key?' · 分区':''}`,r.id)));
    this.onCatalog?.();
  }
  setMulti(value){
    if(this.context().locked||this.working||this.pending||this.drawing)return;
    this.multi=value;this.selectedIds=this.selected?[this.selected]:[];this.cancelDraw(false);this.form();
    this.status(value?'继续点击相邻的同色色块，再点“合并选中的区域”。不同的局部参数需要先统一。':'点击一个色块设置针法，或画线进一步拆分。');
  }
  select(id,{additive=false}={}){
    if(this.context().locked||this.working)return;
    if(this.pending){this.status('请先应用当前设置，或点击“放弃本次修改”，再选择其他区域。',true);$('#local-region').value=this.selected;return}
    if(this.multi||additive){
      this.multi=true;
      if(!this.selectedIds.length&&this.selected)this.selectedIds=[this.selected];
      if(this.selectedIds.includes(id)){if(this.selectedIds.length>1)this.selectedIds=this.selectedIds.filter(k=>k!==id)}
      else if(this.selectedIds.length<32)this.selectedIds.push(id);
      else{this.status('一次最多合并 32 个区域。',true);return}
      if(!this.selectedIds.includes(this.selected))this.selected=this.selectedIds[0];
    }else{this.selected=id;this.selectedIds=[id]}
    this.cancelDraw(false);this.form();this.onMode('local');
  }
  form(){
    const r=this.selectedRegion();if(!r)return;
    const s=this.overrides[r.id]||{};$('#local-region').value=r.id;
    $('#local-selection').textContent=`${this.selectedIds.length>1?'主选区：':''}${this.colors[r.color].name} · ${area(r.area_mm2)} mm²`;
    $('#local-method').value=s.method||'auto';$('#local-underlay').value=s.underlay||'auto';$('#local-role').value=s.sewing_role||'';
    for(const [key,selector] of Object.entries(fields))$(selector).value=s[key]??'';
    $('#local-angle').placeholder=`自动 ${r.default_angle}°`;this.formGuide=clone(s.guide||[]);this.pending=false;
    this.recommendation();
    this.updateFields();this.paint();this.onState();
  }
  recommendation(){
    const r=this.selectedRegion();if(!r)return;
    const recommendation=r.recommendation,settings=this.overrides[r.id]||{};
    const actual=this.context().hasPlan?(this.preview.data?.meta.planned_objects||[]).filter(o=>(o.region_keys||[o.region_key]).includes(r.id)&&o.stitch_type!=='轮廓底针'):[];
    const names=[...new Set(actual.filter(o=>o.spread_pass!=='support').map(o=>o.spread_pass?'随展开方向分区填针':o.stitch_type))];
    const node=$('#local-recommendation');node.replaceChildren();
    const title=document.createElement('strong');title.textContent=names.length?`本版实际：${names.join(' + ')}`:`自动推荐：${recommendation?.label||'排针时判断'}`;
    const note=document.createElement('p');note.className='muted note';note.textContent=recommendation?.reason||'生成工程后核对实际针法。';
    const band=actual.find(o=>o.inlaid_satin_band);
    if(band)note.textContent=`两侧都有先绣的底色。本版将落针向色块内收 ${band.surface_rail_inset_mm.toFixed(2)} mm，同侧落针间距 ${band.peak_spacing_mm.toFixed(2)} mm。`;
    if(actual.some(o=>o.detail_edge_protection))note.textContent+=' 本版已收敛细部扩边，减少把花纹间隙挤合；仍需试绣核对。';
    const review=this.preview.data?.meta.quality_review?.stitch_suitability;
    const direction=review?.regions[r.id];
    if(names.length && direction?.samples){
      note.textContent+=` 已在机器文件中比对 ${direction.samples} 个同色位置的针向。`;
    }
    const mismatch=review?.issues.find(item=>item.region_key===r.id);
    if(names.length&&mismatch)note.textContent+=' '+mismatch.reason;
    const status=document.createElement('small');status.textContent=settings.source==='ai'?'已采用 AI 建议 · 可手动修改':Object.keys(settings).length?'已应用手动设置':'跟随普通制版规则';
    node.append(title,note,status);
    const fit=reviewRegion(r,this.context().thread,settings);
    if(fit.rank){const warning=document.createElement('p');warning.className='note'+(fit.level==='risk'?' local-error':'');warning.textContent=`${fit.label}：${fit.reasons.join('；')}。${fit.action}`;node.append(warning)}
    const trial=narrowSatinTrial(r,this.preview.data?.meta,actual,settings);
    if(trial){
      const reason=document.createElement('p');reason.className='note';
      reason.textContent=`原稿在这里是一条约 ${recommendation.width_mm} mm 的窄色带，当前只沿中线走针。可试用跨越两侧的缎面，再核对边缘和转弯。`;
      const button=document.createElement('button');button.className='button';button.type='button';
      button.id='local-trial-satin';button.textContent='试用窄缎面参数';
      button.onclick=()=>{
        if(this.context().locked||this.pending||this.drawing||this.multi)return;
        $('#local-method').value='satin';$('#local-satin-spacing').value=trial.pitch.toFixed(2);$('#local-pull').value=trial.pull.toFixed(2);
        $('#local-method').oninput();this.status('已填入窄缎面参数。应用到选区后重新生成针迹，即可复查。');
      };
      node.append(reason,button);
    }
  }
  updateFields(){
    const method=$('#local-method').value,stopped=this.context().locked||this.working,running=['run','running'].includes(method);
    $('#local-method option[value="spread"]').disabled=this.selectedRegion()?.recommendation?.structure!=='spread';
    $('#local-angle-field').hidden=method!=='fill';$('#local-guide-field').hidden=method!=='guided';
    $('#local-fill-spacing').disabled=stopped||method==='satin'||running;
    $('#local-satin-spacing').disabled=stopped||['fill','guided','spread'].includes(method)||running;
    $('#local-underlay').disabled=stopped||running;
    $('#local-max-stitch').disabled=stopped||running;$('#local-pull').disabled=stopped||running;
    $('#local-fill-spacing-field').hidden=method==='satin'||running;$('#local-satin-spacing-field').hidden=['fill','guided','spread'].includes(method)||running;
    $('#local-underlay-field').hidden=running;$('#local-compensation-fields').hidden=running;
    $('#local-running-fields').hidden=!running;$('#local-bean-field').hidden=method!=='running';
    for(const selector of ['#local-run-length','#local-run-tolerance','#local-run-repeats'])$(selector).disabled=stopped||!running;
    $('#local-bean-repeats').disabled=stopped||method!=='running';
    $('#local-underlay-note').textContent=running?'细线沿中心线排针，不铺面状底针。':method==='satin'?'轻底针为中心走针；加强底针按列宽增加轮廓和之字支撑。':'填针：轻底针为轮廓支撑，加强底针为轮廓加交叉打底。';
    $('#local-draw-guide').textContent=this.formGuide?.length===3?'重画方向曲线 · 三个点':'画方向曲线 · 三个点';
  }
  availability(){
    const stopped=this.context().locked||this.working,ready=!!this.selectedRegion();
    for(const e of document.querySelectorAll('#local-editor input,#local-editor select,#local-editor button,#local-region'))e.disabled=stopped||!ready;
    $('#local-undo').disabled=stopped||!this.history.length;
    $('#local-cancel-edit').hidden=!this.pending&&!this.drawing;
    this.updateFields();
    if(this.selectedIds.length>1)for(const el of document.querySelectorAll('#local-editor input,#local-editor select,#local-apply,#local-reset-region,#local-draw-guide,#local-cut'))el.disabled=true;
    for(const selector of ['#local-single','#local-multi'])$(selector).disabled=stopped||this.pending||!!this.drawing||!ready;
    $('#local-single').setAttribute('aria-pressed',String(!this.multi));$('#local-multi').setAttribute('aria-pressed',String(this.multi));
    $('#local-merge').disabled=stopped||this.pending||!!this.drawing||this.selectedIds.length<2;
    $('#local-selection-count').textContent=`已选 ${this.selectedIds.length||1} 块`;
    const trial=$('#local-trial-satin');if(trial)trial.disabled=stopped||this.pending||!!this.drawing||this.multi;
  }
  paint(){
    const r=this.selectedRegion(),method=$('#local-method').value;
    const v=$('#local-angle').value;let angle=method==='fill'?(v===''?r?.default_angle:Number(v)):null;
    this.preview.setLocal({regions:this.regions,selected:this.selected,selectedIds:this.selectedIds,angle,
      guide:method==='guided'?this.formGuide:[],dots:this.dots,drawing:this.drawing,width:this.width,height:this.height});
  }
  read(){
    const method=$('#local-method').value,s={};if(method!=='auto')s.method=method;
    if($('#local-role').value)s.sewing_role=$('#local-role').value;
    const underlay=$('#local-underlay').value;if(!['run','running'].includes(method)&&underlay!=='auto')s.underlay=underlay;
    for(const [key,selector] of Object.entries(fields)){
      const input=$(selector);if(input.disabled||input.value===''||(key==='angle'&&method!=='fill'))continue;
      if(!input.checkValidity()||!Number.isFinite(Number(input.value)))throw new Error('请填写范围内的局部参数。');
      s[key]=Number(input.value);
    }
    if(method==='guided'){if(this.formGuide?.length!==3)throw new Error('请先在图案上画出方向曲线。');s.guide=clone(this.formGuide)}
    return s;
  }
  apply(){
    if(this.context().locked||!this.selected)return false;
    try{const s=this.read();this.remember();if(Object.keys(s).length)this.overrides[this.selected]=s;else delete this.overrides[this.selected];this.commit('局部设置已应用。点击“更新绣花工程”生成真实针迹。');return true}
    catch(e){this.status(e.message,true);return false}
  }
  applyAdvice(suggestions){
    if(this.context().locked||this.pending||this.drawing)return false;
    this.remember();let count=0;
    for(const item of suggestions){
      if(!this.regions.some(r=>r.id===item.id)||Object.hasOwn(this.overrides,item.id))continue;
      this.overrides[item.id]=clone(item.settings);count++;
    }
    this.aiContext=this.physicalKey();this.commit(`已采用 ${count} 个 AI 工艺建议。可逐块微调，更新工程后检查真实针迹。`);return true;
  }
  async applySplits(advice){
    if(this.context().locked||this.pending||this.drawing)return false;
    this.working=true;this.onState();
    try{
      const data=await this.fetchCatalog(advice.region_cuts);this.remember();
      this.cuts=clone(advice.region_cuts);this.overrides=clone(advice.object_overrides);this.install(data);
      if(Object.values(this.overrides).some(s=>s.source==='ai'))this.aiContext=this.physicalKey();
      this.selected=advice.accepted[0]?.children[0]||this.regions[0]?.id;
      this.selectedIds=[this.selected];this.multi=false;
      this.onMode('local');this.commit('分区建议已采用。点选子区可手动调整；可撤销整次分区。');return true;
    }catch(e){this.status(e.message,true);return false}
    finally{this.working=false;this.onState()}
  }
  commit(text){this.cancelDraw(false);this.pending=false;this.form();this.onChange();this.status(text);this.onState()}
  begin(kind){
    if(this.context().locked||!this.selected||this.selectedIds.length>1)return;
    if(kind==='cut'&&this.pending){this.status('请先应用当前参数，再进行分区。',true);return}
    this.cancelDraw(false);this.drawing=kind;this.dots=[];this.onMode('local');this.paint();
    $('#local-cancel-draw').hidden=false;
    $('#local-hint').textContent=kind==='guide'?'请依次点击：起点 → 经过点 → 终点。':'请点击分区线上的两个点，直线会延伸穿过选区。';this.onState();
  }
  cancelDraw(repaint=true){this.drawing=null;this.dots=[];$('#local-cancel-draw').hidden=true;$('#local-hint').textContent=this.multi?'继续点选要合并的区域，再点“合并选中的区域”；同色且相邻才可合并。':'滑过只描边，点击才选中。分区是为了分别设置针法和针向，不改变图案配色。';if(repaint)this.paint();this.onState()}
  async merge(){
    if(this.context().locked||this.working||this.pending||this.drawing||this.selectedIds.length<2)return;
    const ids=[...this.selectedIds].sort(),chosen=this.regions.filter(r=>ids.includes(r.id));
    if(new Set(chosen.map(r=>r.color)).size!==1){this.status('请选择同色区域；合并不会改变原图配色。',true);return}
    const setting=this.overrides[ids[0]]||{};
    if(ids.some(id=>settingsKey(this.overrides[id]||{})!==settingsKey(setting))){this.status('这些区域的局部设置不同。请先统一参数或恢复自动，再合并，避免覆盖已有设置。',true);return}
    this.working=true;this.onState();
    try{
      const edits=[...this.cuts,{merge_regions:ids}],data=await this.fetchCatalog(edits);
      const merged=data.regions.find(r=>!this.regions.some(old=>old.id===r.id)&&JSON.stringify(r.merged_from)===JSON.stringify(ids));
      if(!merged)throw new Error('未找到有效合并区域，原分区保留。');
      this.remember();for(const id of ids)delete this.overrides[id];
      if(Object.keys(setting).length)this.overrides[merged.id]=clone(setting);
      this.cuts=data.region_cuts;this.install(data);this.selected=merged.id;this.selectedIds=[merged.id];this.multi=false;
      this.commit(`已将 ${ids.length} 块合为一个工艺区，外形和孔洞保留。可继续拆分或撤销，更新工程后生效。`);
    }catch(e){this.status(e.message,true)}finally{this.working=false;this.onState()}
  }
  async point(dot){
    if(!this.drawing||this.context().locked||this.working)return;
    if(dot.some(v=>v<0||v>1)){this.status('请在图案尺寸范围内点选。',true);return}
    this.dots.push(dot.map(v=>Math.round(v*1e6)/1e6));this.paint();
    if(this.drawing==='guide'&&this.dots.length===3){
      this.formGuide=clone(this.dots);this.pending=true;this.cancelDraw();this.updateFields();this.status('方向曲线已画好，点击“应用到选区”。');return;
    }
    if(this.drawing!=='cut'||this.dots.length!==2)return;
    const cuts=[...this.cuts,{region_id:this.selected,points:clone(this.dots)}];this.working=true;this.onState();
    try{
      const data=await this.fetchCatalog(cuts);this.remember();const previous=this.overrides[this.selected];
      const children=data.regions.filter(r=>r.parent_key===this.selected);delete this.overrides[this.selected];
      if(previous)for(const r of children){const s=clone(previous);if(s.method==='guided'){delete s.method;delete s.guide}this.overrides[r.id]=s}
      this.cuts=cuts;this.install(data);this.selected=children[0]?.id;this.selectedIds=[this.selected];this.multi=false;this.commit('已拆成独立工艺区。可以分别选择针向，再更新工程。'+(previous?.method==='guided'?' 原方向曲线已清除，请为子区重新画线。':''));
    }catch(e){this.cancelDraw();this.status(e.message,true)}
    finally{this.working=false;this.onState()}
  }
  async restore(value){
    this.working=true;this.onState();
    try{const data=await this.fetchCatalog(value.region_cuts);this.cuts=value.region_cuts;this.overrides=value.object_overrides;this.install(data);this.selected=this.regions.some(r=>r.id===value.selected)?value.selected:this.regions[0]?.id;this.selectedIds=value.selectedIds?.filter(id=>this.regions.some(r=>r.id===id))||[this.selected];this.multi=!!value.multi;this.commit('已还原局部设置，重新制作工程后生效。')}
    catch(e){this.status(e.message,true)}finally{this.working=false;this.onState()}
  }
  undo(){if(this.history.length&&!this.context().locked)this.restore(this.history.pop())}
  reset(){if(this.context().locked)return;this.remember();this.restore({region_cuts:[],object_overrides:{},selected:null})}
  resize(width,height){
    if(!this.width)return;
    if(width===this.width&&this.thread===this.context().thread&&!this.analysisError){this.recommendation();return}
    this.analysisPending=true;this.analysisError=null;const factor=width/this.width;
    for(const r of this.regions){r.path=r.path.replace(/-?\d+(?:\.\d+)?/g,v=>((Number(v)-5)*factor+5).toFixed(3));r.area_mm2*=factor*factor;r.bbox=r.bbox.map(v=>(v-5)*factor+5)}
    this.width=width;this.height=height;this.paint();
    clearTimeout(this.resizeTimer);const token=++this.sequence;
    this.resizeTimer=setTimeout(async()=>{
      try{const data=await this.fetchCatalog();if(token!==this.sequence)return;this.install(data);this.form()}
      catch(e){if(token===this.sequence){this.analysisPending=false;this.analysisError=e.message;this.status('自动针法分析未更新：'+e.message,true);this.onCatalog?.()}}
    },300);
  }
}
