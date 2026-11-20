import {AnnotationCanvas,fitMark} from './annotation-canvas.js';
const $=s=>document.querySelector(s);
const labels=['图一','图二','图三','图四','图五','图六','图七','图八'];
export class ArtEditor{
  constructor({request,message,onBusy}){
    this.request=request;this.message=message;this.onBusy=onBusy;this.refs=[];this.marks=[];this.locked=false;this.key='';this.drafts=new Map();this.uploading=false;this.selectedMark='';this.markTool='add';
    $('#art-advanced').onclick=()=>this.expand();
    document.querySelectorAll('[data-close-dialog]').forEach(b=>b.onclick=()=>$('#'+b.dataset.closeDialog).close());
    $('#art-advanced-dialog').addEventListener('close',()=>{
      $('#art-card-home').append($('#art-card'));
      this.annotator.cancel();
      if (!this.locked && $('#art-advanced').getClientRects().length) $('#art-advanced').focus();
    });
    $('#edit-prompt').addEventListener('input',()=>{this.mentions();this.saveDraft()});
    $('#edit-prompt').addEventListener('keydown',e=>{if(e.key==='Escape')$('#art-mentions').hidden=true});
    $('#edit-prompt').addEventListener('blur',()=>setTimeout(()=>{$('#art-mentions').hidden=true},160));
    $('#art-upload').onchange=e=>this.upload(e.target.files).catch(e=>this.message(e.message,true));
    $('#art-mark-image').onload=()=>this.drawMarks();
    this.annotator=new AnnotationCanvas($('#art-mark-canvas'),{
      getState:()=>({marks:this.marks,selectedId:this.selectedMark,tool:this.markTool,disabled:this.locked,radius:Number($('#art-radius').value)/100}),
      onSelect:id=>{this.selectedMark=id;this.syncMarkControls()},
      onTool:tool=>{this.markTool=tool;this.syncMarkControls()},
      onCommit:marks=>{this.marks=marks;this.renderMarks();this.saveDraft()},
      maxMarks:8,onLimit:()=>this.message('最多标注8处，可拖动已有标记调整位置。')
    });
    $('#art-mark-select').onclick=()=>{this.markTool='select';this.syncMarkControls();this.drawMarks()};
    $('#art-mark-add').onclick=()=>{this.markTool='add';this.selectedMark='';this.syncMarkControls();this.drawMarks()};
    $('#art-radius').oninput=()=>{
      const image=$('#art-mark-image');this.marks=this.marks.map(m=>m.id===this.selectedMark?fitMark({...m,radius:Number($('#art-radius').value)/100},image.naturalWidth||1,image.naturalHeight||1):m);
      this.drawMarks();this.saveDraft();
    };
    $('#art-clear-marks').onclick=()=>{this.annotator.cancel();this.marks=[];this.selectedMark='';this.markTool='add';this.renderMarks();this.drawMarks();this.saveDraft()};
    for(const id of ['edit-max-colors','edit-detail-mode'])$('#'+id).addEventListener('change',()=>this.saveDraft());
  }
  options(){return {model:'gpt-image-2.6-flare',ratio:'1:1',resolution:'1K',quality:'medium',complexity:'medium'}}
  updateSummary(){
    const parts=[];
    const target=this.refs[0];
    if(this.refs.length!==1 || target?.kind!=='version' || target.id!==this.version?.id) parts.push('参考图 '+this.refs.length+' 张');
    if(this.marks.length) parts.push('标注 '+this.marks.length+' 处');
    if($('#edit-detail-mode').value==='simplify') parts.push('简化全图');
    if(this.version && Number($('#edit-max-colors').value)!==(Number(this.version.max_colors)||9)) parts.push('最多 '+$('#edit-max-colors').value+' 色');
    const summary=$('#art-draft-summary');
    summary.textContent=parts.length?parts.join(' · ')+' →':'';
    summary.hidden=!parts.length;
  }
  saveDraft(){
    if(!this.key)return;
    this.drafts.set(this.key,{refs:structuredClone(this.refs),marks:structuredClone(this.marks),prompt:$('#edit-prompt').value,max:$('#edit-max-colors').value,detailMode:$('#edit-detail-mode').value});
    this.updateSummary();
  }
  load(project,version){
    this.project=project;this.version=version;const key=project.id+':'+version.id;if(key===this.key)return;
    this.annotator.cancel();this.selectedMark='';this.markTool='add';this.key=key;const draft=this.drafts.get(key);
    this.refs=draft?.refs||[{kind:'version',id:version.id,url:version.flat_url,title:'当前平面稿'}];this.marks=(draft?.marks||[]).map(m=>({...m,id:m.id||crypto.randomUUID()}));this.markTool=this.marks.length?'select':'add';
    $('#edit-prompt').value=draft?.prompt||'';$('#edit-detail-mode').value=draft?.detailMode||'preserve';
    $('#edit-max-colors').value=draft?.max||version.max_colors||9;this.renderRefs();this.renderMarks();this.updateSummary();
  }
  setLocked(value){this.locked=value;for(const el of document.querySelectorAll('#art-card button,#art-card select,#art-card textarea,#art-reference-dialog button,#art-reference-dialog input,#art-marks textarea,#art-marks button,#art-mark-tools button,#art-radius,#art-clear-marks'))el.disabled=value;const add=$('.art-ref-add');if(add)add.disabled=value||this.refs.length>=8;$('#art-advanced').disabled=value||!this.refs.length;this.syncMarkControls();this.drawMarks()}
  payload(){
    if(!this.refs.length)throw new Error('请至少选择一张要修改的参考图。');
    if(this.marks.some(m=>!m.text.trim()))throw new Error('请填写每一处圈选的修改要求，或删除不需要的标记。');
    return {image_settings:this.options(),references:this.refs.map(({kind,id})=>({kind,...(id?{id}:{})})),marks:this.marks};
  }
  submitted(){this.drafts.delete(this.key);if($('#art-advanced-dialog').open)$('#art-advanced-dialog').close()}
  insert(index){
    const t=$('#edit-prompt'),text=t.value,pos=t.selectionStart;const match=text.slice(0,pos).match(/@[^\s@]{0,5}$/);
    t.setRangeText('@'+labels[index]+' ',match?pos-match[0].length:pos,t.selectionEnd,'end');t.focus();$('#art-mentions').hidden=true;this.saveDraft();
  }
  mentions(){const t=$('#edit-prompt'),box=$('#art-mentions');box.hidden=!/@[^\s@]{0,5}$/.test(t.value.slice(0,t.selectionStart));box.replaceChildren();if(box.hidden)return;this.refs.forEach((r,i)=>{const b=document.createElement('button');b.type='button';b.role='option';b.textContent=labels[i]+' · '+r.title;b.onmousedown=e=>e.preventDefault();b.onclick=()=>this.insert(i);box.append(b)})}
  renderRefs(){
    const box=$('#art-references');box.replaceChildren();
    this.refs.forEach((r,i)=>{
      const chip=document.createElement('div');chip.className='art-ref-chip';chip.draggable=true;
      chip.ondragstart=e=>{e.dataTransfer.setData('text/plain',String(i));this.dragIndex=i};chip.ondragover=e=>e.preventDefault();chip.ondrop=e=>{e.preventDefault();const from=this.dragIndex;if(!Number.isInteger(from)||from===i)return;const [moved]=this.refs.splice(from,1);this.refs.splice(i,0,moved);this.clearTargetMarks();this.renderRefs();this.saveDraft();this.dragIndex=null};
      const b=document.createElement('button');b.className='art-ref-insert';b.type='button';b.title=r.title;b.setAttribute('aria-label','引用'+labels[i]+'：'+r.title);b.onclick=()=>this.insert(i);
      b.onkeydown=e=>{if(e.altKey&&['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();const to=i+(e.key==='ArrowLeft'?-1:1);if(to>=0&&to<this.refs.length){[this.refs[i],this.refs[to]]=[this.refs[to],this.refs[i]];this.clearTargetMarks();this.renderRefs();this.saveDraft()}}};
      const im=document.createElement('img');im.src=r.url;im.alt='';const name=document.createElement('span');name.textContent=labels[i];b.append(im,name);
      const remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.setAttribute('aria-label','移除'+labels[i]);remove.onclick=()=>{this.refs.splice(i,1);this.clearTargetMarks();this.renderRefs();this.saveDraft()};chip.append(b,remove);box.append(chip);
    });
    const add=document.createElement('button');add.type='button';add.textContent='＋';add.className='art-ref-add';add.setAttribute('aria-label','添加参考图片');add.onclick=()=>this.choose();add.disabled=this.refs.length>=8||this.locked;box.append(add);
    $('#art-mark-panel').hidden=!this.refs[0];
    if(this.refs[0])$('#art-mark-image').src=this.refs[0].url;
    else $('#art-mark-image').removeAttribute('src');
    this.setLocked(this.locked);
  }
  clearTargetMarks(){if(this.marks.length){this.annotator.cancel();this.marks=[];this.selectedMark='';this.markTool='add';this.renderMarks();this.drawMarks();this.message('参考顺序已改变，旧圈选已清除，请在新的图一上重新标记。')}}
  choose(){
    const p=this.project,candidates=[];
    if(p.source_url)candidates.push({kind:'original',url:p.original_file?`/assets/${p.id}/${p.original_file}`:p.source_url,title:'上传原图'});
    p.versions.forEach((v,i)=>candidates.push({kind:'version',id:v.id,url:v.flat_url,title:'版本 '+(i+1)}));
    (p.references||[]).forEach(r=>candidates.push({kind:'upload',id:r.id,url:r.url,title:r.name}));
    const box=$('#art-reference-candidates');box.replaceChildren();
    candidates.forEach(r=>{const b=document.createElement('button');b.className='art-candidate';const im=document.createElement('img');im.src=r.url;im.alt='';const title=document.createElement('span');title.textContent=r.title;b.append(im,title);b.disabled=this.refs.some(x=>x.kind===r.kind&&x.id===r.id);b.onclick=()=>{if(this.refs.length>=8)return;this.refs.push(r);this.renderRefs();this.saveDraft();$('#art-reference-dialog').close()};box.append(b)});
    $('#art-reference-dialog').showModal();
  }
  async upload(files){
    if(this.locked)return;const selected=[...files];if(this.refs.length+selected.length>8)throw new Error('最多8张参考图，请先移除不需要的图片。');
    for(const f of selected)if(!['image/png','image/jpeg','image/webp'].includes(f.type)||f.size>10*1024*1024)throw new Error('每张参考图需为10 MB以内的PNG、JPG或WebP。');
    this.uploading=true;this.setLocked(true);this.onBusy?.();
    try{for(const file of selected){const data=await new Promise((ok,no)=>{const r=new FileReader();r.onload=()=>ok(r.result);r.onerror=()=>no(new Error('图片读取失败。'));r.readAsDataURL(file)});const ref=await this.request(`/api/projects/${this.project.id}/references`,{image_data:data,name:file.name});(this.project.references??=[]).push(ref);this.refs.push({kind:'upload',id:ref.id,url:ref.url,title:ref.name})}this.renderRefs();this.saveDraft();$('#art-reference-dialog').close();this.message('参考图已添加；图一作为编辑主体，其余图可在提示词中引用。')}
    finally{this.uploading=false;this.setLocked(false);$('#art-upload').value='';this.onBusy?.()}
  }
  expand(){
    if(this.locked||!this.refs.length)return;
    $('#art-advanced-host').append($('#art-card'));
    $('#art-advanced-dialog').showModal();
    this.drawMarks();
  }
  syncMarkControls(){
    const selected=this.marks.find(m=>m.id===this.selectedMark);
    $('#art-mark-select').setAttribute('aria-pressed',String(this.markTool==='select'));
    $('#art-mark-add').setAttribute('aria-pressed',String(this.markTool==='add'));
    $('#art-mark-add').disabled=this.locked||this.marks.length>=8;
    $('#art-radius-label').textContent=selected?'标记 '+(this.marks.indexOf(selected)+1)+' 大小':'新标记大小';
    if(selected)$('#art-radius').value=selected.radius*100;
    $('#art-mark-status').textContent=this.markTool==='add'?'点击图片添加一个标记':selected?'拖动框内移动 · 拖动四角缩放':'点击已有标记，选中后调整';
    document.querySelectorAll('.art-mark-row').forEach(row=>row.classList.toggle('selected',row.dataset.markId===this.selectedMark));
  }
  renderMarks(){
    const box=$('#art-marks');box.replaceChildren();
    this.marks.forEach((m,i)=>{
      const row=document.createElement('div');row.className='art-mark-row';row.dataset.markId=m.id;
      const label=document.createElement('label');label.textContent='标记 '+(i+1);
      const input=document.createElement('textarea');input.rows=2;input.maxLength=1000;input.value=m.text;input.placeholder='这里要怎么改？';input.setAttribute('aria-label','标记'+(i+1)+'的修改要求');
      input.onfocus=()=>{this.selectedMark=m.id;this.markTool='select';this.syncMarkControls();this.drawMarks()};
      input.oninput=()=>{this.marks=this.marks.map(x=>x.id===m.id?{...x,text:input.value}:x);this.saveDraft()};
      const remove=document.createElement('button');remove.textContent='删除';remove.className='text-button';remove.setAttribute('aria-label','删除标记'+(i+1));
      remove.onclick=()=>{this.marks=this.marks.filter(x=>x.id!==m.id);if(this.selectedMark===m.id)this.selectedMark='';if(!this.marks.length)this.markTool='add';this.renderMarks();this.drawMarks();this.saveDraft()};
      label.append(input);row.append(label,remove);box.append(row);
    });
    this.syncMarkControls();
  }
  drawMarks(){this.annotator?.draw()}
}
