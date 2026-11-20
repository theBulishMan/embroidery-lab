const $=s=>document.querySelector(s);
const stable=value=>JSON.stringify(value,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
export class SplitEditor{
  constructor({snapshot,context,request,watch,busy,message,local,preview,mode}){
    Object.assign(this,{snapshot,context,request,watch,busy,message,local,preview,mode});this.advice=null;
    $('#ai-split-start').onclick=()=>this.start(true);$('#split-free-start').onclick=()=>this.start(false);$('#ai-split-apply').onclick=()=>this.apply();
  }
  available(){
    const c=this.context(),disabled=c.locked||c.pending||!c.valid;
    $('#ai-split-panel').hidden=!c.visible;
    $('#ai-split-start').disabled=disabled;$('#split-free-start').disabled=disabled;$('#ai-split-prompt').disabled=c.locked;
    const valid=this.advice&&stable(this.advice.snapshot)===stable(this.snapshot());
    $('#ai-split-apply').disabled=disabled||!valid;
    if(this.advice&&!valid){$('#ai-split-status').textContent='图案、尺寸或局部工艺已变化，请重新分析。';this.preview.splitSuggestions=null}
  }
  async start(useAI=true){
    if(this.context().locked||this.context().pending||(useAI&&!this.context().useAI))return;
    this.advice=null;this.preview.splitSuggestions=null;$('#ai-split-results').replaceChildren();$('#ai-split-apply').hidden=true;
    this.busy(true,'正在分析适合拆分的位置');$('#ai-split-status').textContent=useAI?'对照原图分析中，不会重画当前图案…':'正在本地分析窄颈和形状转折，不调用 AI…';
    try{await this.watch(await this.request('/api/projects/'+this.snapshot().project_id+'/split-advice',{...this.snapshot(),instruction:$('#ai-split-prompt').value,use_ai:useAI}))}
    catch(e){this.message(e.message,true);$('#ai-split-status').textContent=e.message;this.busy(false)}
  }
  clearProposal(){
    this.advice=null;this.preview.splitSuggestions=null;
    $('#ai-split-status').textContent='';$('#ai-split-results').replaceChildren();$('#ai-split-apply').hidden=true;
  }
  show(advice){
    this.advice=advice;const rows=$('#ai-split-results');rows.replaceChildren();
    $('#ai-split-status').textContent=advice.accepted.length?`${advice.before_count} → ${advice.after_count} 个绣区 · 轮廓与孔洞检查通过 · ${advice.source==='local'?'本地免费':advice.usage.cached?'已复用结果，本次无 AI 请求':advice.usage.elapsed_s+' 秒'}`:`本次未找到需要采用的拆区 · ${advice.source==='local'?'本地免费':advice.usage.cached?'已复用结果，本次无 AI 请求':advice.usage.elapsed_s+' 秒'}`;
    for(const item of advice.accepted){
      const row=document.createElement('div');row.className='ai-craft-row';
      const button=document.createElement('button');button.className='text-button';button.textContent=`${item.candidate_id} · ${item.color}拆成 ${item.children.length} 区 · 点选查看`;
      button.onclick=()=>{this.local.select(item.region_id);this.preview.render()};
      const text=document.createElement('p');text.textContent=item.reason;row.append(button,text);rows.append(row);
    }
    if(advice.detail_notes.length){
      const head=document.createElement('strong');head.textContent=advice.source==='local'?'需要核对的细节':'需要核对的细节（AI 提示）';rows.append(head);
      for(const note of advice.detail_notes){const p=document.createElement('p');p.className='split-warning';p.textContent=note;rows.append(p)}
    }
    if(advice.rejected.length){const p=document.createElement('p');p.className='muted note';p.textContent=advice.rejected.join('；');rows.append(p)}
    $('#ai-split-apply').hidden=!advice.accepted.length;
    this.preview.splitSuggestions={items:advice.accepted,width:advice.snapshot.width,height:advice.catalog.height};
    this.mode('layers');this.preview.render();this.available();
  }
  async apply(){
    if(this.context().locked||this.context().pending||!this.advice||stable(this.advice.snapshot)!==stable(this.snapshot()))return;
    const value=this.advice;this.advice=null;this.preview.splitSuggestions=null;
    if(await this.local.applySplits(value)){
      $('#ai-split-apply').hidden=true;$('#ai-split-status').textContent=`已拆成 ${value.after_count} 个可点选绣区。可手动改针法，或更新工程检查实际针迹。`;
    }else{this.advice=value;this.available()}
  }
}
