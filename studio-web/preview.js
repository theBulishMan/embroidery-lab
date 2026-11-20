import {stitchEnds,commandIndexForStitch,stitchCountAtIndex,colorBlocks,colorProgress} from './stitch-timeline.js';
import {referenceWidth} from './thread-profile.js';
import {analyzeCoverage} from './coverage.js';

// A material preview still uses the machine's exact segments and thread width.
// Shade each stitch before drawing the next one, so underlay cannot shine
// through a later top stitch of the same colour. Never fill the artwork below it.
export function threadRenderSize(width,height){
  const factor=Math.min(1,2048/Math.max(width,height),Math.sqrt(3000000/(width*height)));
  return {width:Math.max(1,Math.round(width*factor)),height:Math.max(1,Math.round(height*factor))};
}
export function paintThread(ctx,a,b,rgb,width){
  const dx=b[0]-a[0],dy=b[1]-a[1],length=Math.hypot(dx,dy);
  if(length<.00001)return;
  let nx=-dy/length,ny=dx/length;
  if(nx*-.6+ny*-.8<0){nx=-nx;ny=-ny}
  const cx=(a[0]+b[0])/2,cy=(a[1]+b[1])/2;
  const tone=(shade,shine=0)=>`rgb(${rgb.map(v=>Math.round(Math.max(0,Math.min(255,v*shade+(255-v)*shine)))).join(',')})`;
  const shine=.055+.065*Math.abs(nx*-.6+ny*-.8);
  const gradient=ctx.createLinearGradient(cx+nx*width/2,cy+ny*width/2,cx-nx*width/2,cy-ny*width/2);
  gradient.addColorStop(0,tone(.78));gradient.addColorStop(.16,tone(.98));
  gradient.addColorStop(.38,tone(1,shine));gradient.addColorStop(.62,tone(1));
  gradient.addColorStop(.86,tone(.91));gradient.addColorStop(1,tone(.72));
  ctx.globalAlpha=1;ctx.lineWidth=width;ctx.strokeStyle=gradient;
  ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.lineTo(b[0],b[1]);ctx.stroke();
  // Subtle longitudinal filaments stay inside the physical thread footprint.
  // They are material shading, not additional stitches or extra coverage.
  ctx.lineWidth=width*.055;ctx.strokeStyle=tone(1,.24);ctx.globalAlpha=.20;
  for(const offset of [-.17,.18]){
    ctx.beginPath();ctx.moveTo(a[0]+nx*width*offset,a[1]+ny*width*offset);
    ctx.lineTo(b[0]+nx*width*offset,b[1]+ny*width*offset);ctx.stroke();
  }
  ctx.globalAlpha=1;
}
export async function renderThreadSurface({commands,colors,lineWidth,width,height,scale},makeCanvas,{cancelled=()=>false,progress=()=>{},yieldFrame=()=>new Promise(resolve=>setTimeout(resolve,8))}={}){
  const canvas=makeCanvas(width,height),ctx=canvas.getContext('2d');
  ctx.clearRect(0,0,width,height);ctx.setTransform(scale,0,0,scale,scale,scale);
  ctx.lineCap='round';ctx.lineJoin='round';
  let last=null,count=0,slice=performance.now(),started=slice,reported=slice;
  for(let i=0;i<commands.length;i++){
    if(cancelled())return null;
    const point=commands[i];
    if(last&&point[2]===0){paintThread(ctx,last,point,colors[point[3]].rgb,lineWidth);count++}
    last=point;
    if((i&63)===63&&performance.now()-slice>=5){
      if(performance.now()-reported>180){progress((i+1)/commands.length);reported=performance.now()}
      await yieldFrame();slice=performance.now();
    }
  }
  return {canvas,stitches:count,elapsedMs:performance.now()-started};
}

export class Preview {
  constructor(canvas,onProgress){
    this.canvas=canvas;this.ctx=canvas.getContext('2d');this.viewport=canvas.closest('.preview-scroll');
    this.onProgress=onProgress;this.mode='artwork';this.layer='all';this.opacity=.5;
    this.speed=600;this.showJumps=false;this.zoom=1;this.data=null;this.index=0;
    this.playing=false;this.images={};this.sequence=0;this.ends=[];this.paletteDraft=null;
    this.local=null;this.threadWidth=null;this.material='sheen';this.fabricColor='linen';this.coverage=null;this.coverageId=0;
    this.proofEnabled=false;this.proofPosition=.5;this.proofPointer=null;
    this.pointerDown=null;this.hovered=null;this.threadRenderId=0;this.threadSurface=null;this.threadRenderKey='';
    this.hoverLabel=document.createElement('div');this.hoverLabel.className='region-hover-label';this.hoverLabel.hidden=true;
    canvas.closest('.stage').append(this.hoverLabel);
    canvas.addEventListener('pointerdown',event=>{
      if(event.button===0&&event.pointerType==='mouse'&&this.atProofDivider(event)){
        this.proofPointer=event.pointerId;this.pointerDown=null;canvas.setPointerCapture(event.pointerId);return;
      }
      this.pointerDown=event.button===0?{id:event.pointerId,x:event.clientX,y:event.clientY,moved:false}:null;
    });
    canvas.addEventListener('pointermove',event=>{
      if(this.proofPointer===event.pointerId){
        const [x]=this.coordinates(event);this.setProofPosition((x-5)/this.data.meta.design_width_mm);return;
      }
      if(event.pointerType==='mouse')canvas.classList.toggle('proof-divider',this.atProofDivider(event));
      if(this.pointerDown&&Math.hypot(event.clientX-this.pointerDown.x,event.clientY-this.pointerDown.y)>5)this.pointerDown.moved=true;
      if(event.pointerType!=='mouse'||event.buttons||!this.pickRegions||!['artwork','layers','local'].includes(this.mode)||this.local?.drawing)return;
      this.hoverPoint=this.coordinates(event);
      this.hoverClient=[event.clientX,event.clientY];
      if(this.hoverFrame)return;
      this.hoverFrame=requestAnimationFrame(()=>{
        this.hoverFrame=null;
        const id=this.hit(this.hoverPoint)?.id||null;
        this.showHoverLabel(id);
        if(id!==this.hovered){this.hovered=id;this.render()}
      });
    });
    canvas.addEventListener('pointercancel',()=>{this.pointerDown=null;this.clearHover()});
    canvas.addEventListener('lostpointercapture',()=>{this.proofPointer=null});
    canvas.addEventListener('pointerleave',()=>{this.pointerDown=null;this.clearHover()});
    canvas.addEventListener('pointerup',event=>{
      if(this.proofPointer===event.pointerId){this.proofPointer=null;canvas.releasePointerCapture(event.pointerId);return}
      const down=this.pointerDown;this.pointerDown=null;
      if(!down||down.id!==event.pointerId||down.moved||event.button!==0||Math.hypot(event.clientX-down.x,event.clientY-down.y)>5)return;
      if(!this.pickRegions||!['artwork','layers','local','result','coverage'].includes(this.mode)||!this.local||!this.s)return;
      const [x,y]=this.coordinates(event);
      if(this.local.drawing){this.onLocalPoint?.([(x-5)/this.local.width,(y-5)/this.local.height]);return}
      const hit=this.hit([x,y]);
      if(hit)this.onRegionPick?.(hit.id,{additive:event.shiftKey});
    });
    new ResizeObserver(()=>this.render()).observe(this.viewport);
  }
  stop(){this.playing=false;cancelAnimationFrame(this.frame);this.emit()}
  coordinates(event){const rect=this.canvas.getBoundingClientRect();return [(event.clientX-rect.left)*this.canvas.width/rect.width/this.s-1,(event.clientY-rect.top)*this.canvas.height/rect.height/this.s-1]}
  atProofDivider(event){
    if(!this.proofEnabled||!this.s||!this.data||!['result','coverage'].includes(this.mode))return false;
    const [x,y]=this.coordinates(event),m=this.data.meta;
    return Math.abs(x-5-m.design_width_mm*this.proofPosition)<8*this.screenPixel/this.s && y>=4 && y<=6+m.design_height_mm;
  }
  setProofPosition(value){
    this.proofPosition=Math.min(1,Math.max(0,value));this.onProofPosition?.(this.proofPosition);this.render();
  }
  focusRegion(region,point){
    const [x0,y0,x1,y1]=region.bbox,m=this.data.meta;
    const fraction=Math.max((x1-x0)/m.design_width_mm,(y1-y0)/m.design_height_mm);
    this.setZoom(fraction<.24?4:fraction<.58?2:1);
    const [x,y]=point||[(x0+x1)/2,(y0+y1)/2];
    const c=this.canvas.getBoundingClientRect(),v=this.viewport.getBoundingClientRect();
    this.viewport.scrollTo({left:this.viewport.scrollLeft+c.left-v.left+(x+1)*this.s*c.width/this.canvas.width-v.width/2,
      top:this.viewport.scrollTop+c.top-v.top+(y+1)*this.s*c.height/this.canvas.height-v.height/2,behavior:'instant'});
  }
  hit(point){
    if(!point||!this.local)return null;
    const [x,y]=point;this.ctx.save();this.ctx.setTransform(1,0,0,1,0,0);
    const hits=this.local.regions.filter(r=>x>=r.bbox[0]&&y>=r.bbox[1]&&x<=r.bbox[2]&&y<=r.bbox[3]&&this.ctx.isPointInPath(r.shape,x,y,'evenodd'));
    this.ctx.restore();return hits.sort((a,b)=>a.area_mm2-b.area_mm2)[0]||null;
  }
  showHoverLabel(id){
    const r=this.local?.regions.find(r=>r.id===id);this.hoverLabel.hidden=!r;if(!r)return;
    this.hoverLabel.textContent=`${this.data.meta.colors[r.color].name} · ${r.recommendation?.label||'色块'} · 点击选中`;
    const stage=this.canvas.closest('.stage'),rect=stage.getBoundingClientRect(),[x,y]=this.hoverClient;
    this.hoverLabel.style.left=Math.max(4,Math.min(x-rect.left+12,rect.width-this.hoverLabel.offsetWidth-4))+'px';
    this.hoverLabel.style.top=Math.max(4,Math.min(y-rect.top+15,rect.height-this.hoverLabel.offsetHeight-4))+'px';
  }
  clearHover(){cancelAnimationFrame(this.hoverFrame);this.hoverFrame=null;this.hoverLabel.hidden=true;if(this.hovered){this.hovered=null;this.render()}}
  clear(){this.stop();this.clearHover();++this.sequence;this.invalidateThreads();this.invalidateCoverage();this.data=null;this.ends=[];this.paletteDraft=null;this.local=null;this.ctx.clearRect(0,0,this.canvas.width,this.canvas.height)}
  invalidateThreads(){
    ++this.threadRenderId;this.renderWorker?.terminate();this.renderWorker=null;
    this.threadSurface?.close?.();this.threadSurface=null;this.threadRenderKey='';this.threadRenderPending=false;
  }
  threadSurfaceForView(){
    const size=threadRenderSize(this.canvas.width,this.canvas.height),lineWidth=this.threadWidth??this.data.meta.preview_thread_width_mm??referenceWidth(this.data.meta.thread_spec);
    const key=[this.sequence,lineWidth,size.width,size.height].join(':');
    if(this.threadRenderKey===key)return this.threadSurface;
    this.invalidateThreads();this.threadRenderKey=key;this.threadRenderPending=true;
    const id=this.threadRenderId,view={commands:this.data.commands,colors:this.data.meta.colors,lineWidth,...size,scale:this.s*size.width/this.canvas.width};
    this.onRenderState?.({state:'working',progress:0});
    const finish=({bitmap,elapsedMs,error})=>{
      if(id!==this.threadRenderId){bitmap?.close?.();return}
      this.renderWorker?.terminate();this.renderWorker=null;this.threadRenderPending=false;
      if(error){this.onRenderState?.({state:'error',error});return}
      this.threadSurface=bitmap;this.onRenderState?.({state:'ready',elapsedMs});
      if(this.mode==='result')this.render();
    };
    const fallback=()=>renderThreadSurface(view,(w,h)=>{const c=document.createElement('canvas');c.width=w;c.height=h;return c},{cancelled:()=>id!==this.threadRenderId,progress:p=>this.onRenderState?.({state:'working',progress:p})}).then(result=>{if(result)finish({bitmap:result.canvas,elapsedMs:result.elapsedMs})}).catch(()=>finish({error:'绣线材质暂未绘制完成，当前显示真实针路。'}));
    if(typeof Worker!=='undefined'&&typeof OffscreenCanvas!=='undefined'){
      try{
        this.renderWorker=new Worker('/coverage-worker.js',{type:'module'});
        this.renderWorker.onmessage=({data})=>{
          if(data.id!==this.threadRenderId){data.bitmap?.close?.();return}
          if(data.progress!==undefined){this.onRenderState?.({state:'working',progress:data.progress});return}
          finish(data);
        };
        this.renderWorker.onerror=()=>{this.renderWorker?.terminate();this.renderWorker=null;if(id===this.threadRenderId)fallback()};
        this.renderWorker.postMessage({id,kind:'thread-render',view});
      }catch{this.renderWorker?.terminate();this.renderWorker=null;fallback()}
    }else fallback();
    return null;
  }
  invalidateCoverage(){
    ++this.coverageId;this.worker?.terminate();this.worker=null;
    this.finishCoverage?.();this.finishCoverage=null;this.coverageWait=null;
    this.coverage=null;this.coveragePending=false;this.onCoverage?.({state:'idle'});
  }
  setThreadWidth(width){if(this.threadWidth===width)return;this.threadWidth=width;this.invalidateThreads();this.invalidateCoverage();this.render()}
  inspectCoverage(){
    if(this.inspectionDisabled||!this.data?.commands.length)return;
    if(this.coverage||this.coveragePending)return this.coverageWait;
    const wait=this.coverageWait=new Promise(resolve=>{this.finishCoverage=resolve});
    this.coveragePending=true;const id=++this.coverageId,view=this.data,lineWidth=this.threadWidth??referenceWidth(view.meta.thread_spec);
    this.onCoverage?.({state:'loading'});
    const receive=({result,error})=>{
      if(id!==this.coverageId)return;this.worker?.terminate();this.worker=null;this.coveragePending=false;
      this.finishCoverage?.();this.finishCoverage=null;
      if(error){this.coverage={error};this.onCoverage?.({state:'error',error});return}
      const layer=document.createElement('canvas');layer.width=result.width;layer.height=result.height;
      layer.getContext('2d').putImageData(new ImageData(result.overlay,result.width,result.height),0,0);delete result.overlay;
      this.coverage={...result,layer};this.onCoverage?.({state:'ready',...result});if(this.mode==='coverage')this.render();
    };
    if(typeof Worker!=='undefined'&&typeof OffscreenCanvas!=='undefined'){
      try {
        this.worker=new Worker('/coverage-worker.js',{type:'module'});
        this.worker.onmessage=e=>{if(e.data.id===id)receive(e.data)};
        this.worker.onerror=()=>receive({error:'露底检查未完成，请刷新后重试。'});
        this.worker.postMessage({id,view,lineWidth});
      }catch(e){receive({error:e.message})}
    }else setTimeout(()=>{if(id!==this.coverageId)return;try{receive({result:analyzeCoverage(view,lineWidth,(w,h)=>{const c=document.createElement('canvas');c.width=w;c.height=h;return c})})}catch(e){receive({error:e.message})}},0);
    return wait;
  }
  setLocal(value){this.local={...value,regions:value.regions.map(r=>({...r,shape:new Path2D(r.path)}))};if(this.mode==='local')this.render()}
  async setData(data,flat,source){
    // A new version may have fewer colours. Old hit areas and selections must
    // not render against the new palette while its region catalogue loads.
    this.local=null;this.clearHover();this.pointerDown=null;
    this.stop();this.invalidateThreads();this.invalidateCoverage();const sequence=++this.sequence;this.data=data;this.index=0;this.paletteDraft=null;
    this.ends=stitchEnds(data.commands);this.blocks=colorBlocks(data.commands);
    this.outlines=data.meta.separated_objects.map(o=>({...o,shape:new Path2D(o.path)}));
    this.partial=this.emptyPaths();this.full=this.extendPaths(this.emptyPaths(),data.commands.length);
    for(const [key,url] of [['artwork',flat],['original',source]]){
      if(!url){delete this.images[key];continue}
      if(this.images[key]?.src.endsWith(url))continue;
      const image=new Image();image.src=url;await image.decode().catch(()=>{});
      if(sequence!==this.sequence)return;
      this.images[key]=image;
    }
    if(!data.commands.length&&['simulation','result','coverage'].includes(this.mode))this.mode='artwork';
    this.render();this.emit();
  }
  setMode(mode){this.stop();this.clearHover();if(mode!=='result'&&this.threadRenderPending)this.invalidateThreads();this.mode=mode;this.canvas.classList.toggle('local-canvas',mode==='local');this.render()}
  setZoom(value){this.zoom=[1,2,4].includes(Number(value))?Number(value):1;this.render()}
  setPalette(colors){this.paletteDraft=colors;this.render()}
  count(){return stitchCountAtIndex(this.ends,this.index)}
  emit(){
    if(!this.data)return;
    const count=this.count();
    this.onProgress?.({index:this.index,count,total:this.ends.length,playing:this.playing,...colorProgress(this.blocks||[],count),done:count>=this.ends.length});
  }
  step(direction=1){if(this.ends.length)this.seek(this.count()+direction)}
  seek(value){
    if(!this.data)return;
    this.stop();this.index=commandIndexForStitch(this.ends,this.data.commands.length,value);this.render();this.emit();
  }
  throughColor(ci){
    if(!this.ends.length)return;
    this.setMode('simulation');const block=this.blocks.findLast(b=>b.color===ci);
    if(block)this.seek(block.end);
  }
  toggle(){
    if(!this.ends.length)return;
    if(this.playing){this.stop();return}
    if(this.count()>=this.ends.length)this.index=0;
    this.playing=true;this.lastTime=null;this.debt=0;this.emit();
    const tick=time=>{
      if(!this.playing)return;
      if(this.lastTime!==null)this.debt+=Math.min(250,time-this.lastTime)/1000*this.speed;
      this.lastTime=time;const n=Math.floor(this.debt);
      if(n){this.index=commandIndexForStitch(this.ends,this.data.commands.length,this.count()+n);this.debt-=n;this.render();this.emit()}
      if(this.count()>=this.ends.length){this.stop();return}
      this.frame=requestAnimationFrame(tick);
    };
    this.frame=requestAnimationFrame(tick);
  }
  emptyPaths(){return {end:0,last:null,needle:null,block:0,paths:this.blocks.map(()=>new Path2D()),jumps:new Path2D()}}
  extendPaths(cache,end){
    for(let i=cache.end;i<end;i++){
      const point=this.data.commands[i],last=cache.last;
      if(point[2]===5)cache.block++;
      if(last&&point[2]===0){
        const path=cache.paths[cache.block];path.moveTo(last[0]+1,last[1]+1);path.lineTo(point[0]+1,point[1]+1);cache.needle=point;
      }else if(last&&point[2]===1){cache.jumps.moveTo(last[0]+1,last[1]+1);cache.jumps.lineTo(point[0]+1,point[1]+1)}
      cache.last=point;
    }
    cache.end=end;return cache;
  }
  render(){
    if(!this.data||this.mode==='compare'||this.canvas.hidden)return;
    const d=this.data,m=d.meta,ctx=this.ctx,c=this.canvas,w=m.design_width_mm+12,h=m.design_height_mm+12;
    if(!this.viewport.clientWidth||!this.viewport.clientHeight)return;
    const cssWidth=Math.max(1,Math.min(this.viewport.clientWidth-8,(this.viewport.clientHeight-8)*w/h))*this.zoom;
    const cssHeight=cssWidth*h/w;
    c.style.width=cssWidth+'px';c.style.height=cssHeight+'px';
    const desiredWidth=Math.max(800,Math.round(cssWidth*(window.devicePixelRatio||1)));
    // Bound the visible canvas as well as the worker surface. A tall artwork
    // must not allocate an unbounded bitmap just because its width is small.
    const {width:resolution,height:pixelHeight}=threadRenderSize(desiredWidth,desiredWidth*h/w);
    if(c.width!==resolution||c.height!==pixelHeight){c.width=resolution;c.height=pixelHeight}
    ctx.setTransform(1,0,0,1,0,0);const s=resolution/w;this.s=s;this.screenPixel=resolution/cssWidth;
    ctx.fillStyle='#fafaf6';ctx.fillRect(0,0,c.width,c.height);
    if(['result','coverage','simulation'].includes(this.mode))this.fabric(s);
    if(this.mode==='artwork'){
      // Checkerboard makes retained white artwork distinguishable from empty
      // space. It is only the viewer background, never exported geometry.
      ctx.save();ctx.beginPath();ctx.rect(6*s,6*s,m.design_width_mm*s,m.design_height_mm*s);ctx.clip();
      const tile=1.8*s;for(let y=6*s,row=0;y<(6+m.design_height_mm)*s;y+=tile,row++)for(let x=6*s,col=0;x<(6+m.design_width_mm)*s;x+=tile,col++){
        ctx.fillStyle=(row+col)%2?'#f8f9f6':'#e7ebe3';ctx.fillRect(x,y,tile,tile);
      }ctx.restore();
    }
    const image=this.images[this.mode==='original'?'original':'artwork'];
    if(['artwork','overlay','original'].includes(this.mode)&&image?.naturalWidth&&!this.paletteDraft){
      if(this.mode==='original'){
        const fit=Math.min((m.design_width_mm+4)*s/image.naturalWidth,(m.design_height_mm+4)*s/image.naturalHeight);
        ctx.drawImage(image,(m.design_width_mm+14)*s/2-image.naturalWidth*fit/2,6*s,image.naturalWidth*fit,image.naturalHeight*fit);
      }else ctx.drawImage(image,6*s,6*s,m.design_width_mm*s,m.design_height_mm*s);
    }
    if(['layers','overlay'].includes(this.mode)||(this.mode==='artwork'&&this.paletteDraft)){
      ctx.save();ctx.translate(s,s);ctx.scale(s,s);
      for(const o of this.outlines){
        const active=this.layer==='all'||Number(this.layer)===o.color;
        ctx.globalAlpha=(this.mode==='overlay'?this.opacity:1)*(this.mode==='layers'&&!active ? .06 : 1);
        ctx.fillStyle=this.paletteDraft?.[o.color]||m.colors[o.color].hex;ctx.fill(o.shape,'evenodd');
      }
      ctx.restore();
    }
    if(['simulation','result','coverage'].includes(this.mode))this.stitches();
    if(this.mode==='coverage'&&this.coverage?.layer){ctx.drawImage(this.coverage.layer,s,s,this.coverage.width*this.coverage.mmPerPixel*s,this.coverage.height*this.coverage.mmPerPixel*s)}
    if(this.proofEnabled&&['result','coverage'].includes(this.mode))this.drawProof();
    if(['result','coverage'].includes(this.mode))this.inspectCoverage();
    if(this.mode==='local')this.localDrawing();
    if(this.hovered&&this.pickRegions&&['local','layers','artwork'].includes(this.mode)){
      const region=this.local?.regions.find(r=>r.id===this.hovered);
      if(region){ctx.save();ctx.translate(s,s);ctx.scale(s,s);ctx.fillStyle='#00a3d222';ctx.fill(region.shape,'evenodd');ctx.strokeStyle='#fff';ctx.lineWidth=5*this.screenPixel/s;ctx.stroke(region.shape);ctx.strokeStyle='#087d9f';ctx.lineWidth=2.3*this.screenPixel/s;ctx.stroke(region.shape);ctx.restore()}
    }
    if(this.splitSuggestions&&['local','layers','artwork'].includes(this.mode)){
      const a=this.splitSuggestions;ctx.save();ctx.translate(s,s);ctx.scale(s,s);ctx.strokeStyle='#b22684';ctx.lineWidth=2/s;ctx.fillStyle='#8b0059';ctx.font=`${13/s}px sans-serif`;
      for(const item of a.items){const dots=item.points.map(([x,y])=>[5+x*a.width,5+y*a.height]);ctx.beginPath();ctx.moveTo(...dots[0]);ctx.lineTo(...dots[1]);ctx.stroke();ctx.fillText(item.candidate_id,(dots[0][0]+dots[1][0])/2,(dots[0][1]+dots[1][1])/2)}
      ctx.restore();
    }
    if(this.mode!=='original'&&this.showDimensions!==false)this.dimensions();
  }
  drawProof(){
    const {ctx,s,canvas:c,screenPixel:pixel}=this,m=this.data.meta,x=(6+m.design_width_mm*this.proofPosition)*s;
    const y0=6*s,y1=(6+m.design_height_mm)*s;
    if(this.proofPosition>0){
      ctx.save();ctx.beginPath();ctx.rect(0,0,this.proofPosition===1?c.width:x,c.height);ctx.clip();
      ctx.fillStyle='#fafaf6';ctx.fillRect(0,0,c.width,c.height);
      ctx.translate(s,s);ctx.scale(s,s);
      for(const o of this.outlines){ctx.fillStyle=m.colors[o.color].hex;ctx.fill(o.shape,'evenodd')}
      ctx.restore();
    }
    ctx.save();ctx.beginPath();ctx.moveTo(x,y0);ctx.lineTo(x,y1);
    ctx.strokeStyle='#fff';ctx.lineWidth=3*pixel;ctx.stroke();ctx.strokeStyle='#246d65';ctx.lineWidth=1.35*pixel;ctx.stroke();
    const cy=(y0+y1)/2;ctx.fillStyle='#246d65';ctx.beginPath();ctx.arc(x,cy,7*pixel,0,2*Math.PI);ctx.fill();
    ctx.strokeStyle='#fff';ctx.lineWidth=pixel;ctx.beginPath();
    for(const side of [-1,1]){ctx.moveTo(x+side*2*pixel,cy-2*pixel);ctx.lineTo(x+side*4*pixel,cy);ctx.lineTo(x+side*2*pixel,cy+2*pixel)}
    ctx.stroke();ctx.restore();
  }
  fabric(s){
    const {ctx,canvas:c}=this,colors={linen:'#e9e3d6',gray:'#a8b0b1',dark:'#343d3c'};
    ctx.fillStyle=colors[this.fabricColor]||colors.linen;ctx.fillRect(0,0,c.width,c.height);
    if(this.mode!=='result')return;
    // Cloth texture is a background only; no solid artwork sits under stitches.
    ctx.save();ctx.lineWidth=Math.max(.4,.018*s);ctx.strokeStyle=this.fabricColor==='dark'?'#ffffff0d':'#6e695510';ctx.beginPath();
    const gap=Math.max(3,.22*s);for(let x=0;x<c.width;x+=gap){ctx.moveTo(x,0);ctx.lineTo(x,c.height)}
    for(let y=0;y<c.height;y+=gap){ctx.moveTo(0,y);ctx.lineTo(c.width,y)}ctx.stroke();ctx.restore();
  }
  localDrawing(){
    if(!this.local)return;
    const {ctx,s}=this,l=this.local;ctx.save();ctx.translate(s,s);ctx.scale(s,s);
    const selected=l.regions.find(r=>r.id===l.selected);
    for(const r of l.regions){ctx.fillStyle=this.data.meta.colors[r.color].hex;ctx.fill(r.shape,'evenodd')}
    ctx.globalAlpha=1;
    for(const r of l.regions.filter(r=>(l.selectedIds||[l.selected]).includes(r.id))){
      ctx.fillStyle='#077b9a18';ctx.fill(r.shape,'evenodd');
      ctx.strokeStyle='#fff';ctx.lineWidth=6*this.screenPixel/s;ctx.stroke(r.shape);
      ctx.strokeStyle='#087d9f';ctx.lineWidth=2.8*this.screenPixel/s;ctx.setLineDash([7*this.screenPixel/s,4*this.screenPixel/s]);ctx.stroke(r.shape);ctx.setLineDash([]);
    }
    if(selected){
      if(Number.isFinite(l.angle)&&(l.selectedIds?.length||1)===1){
        const [x0,y0,x1,y1]=selected.bbox,a=-l.angle*Math.PI/180,ux=Math.cos(a),uy=Math.sin(a),vx=-uy,vy=ux,cx=(x0+x1)/2,cy=(y0+y1)/2,extent=Math.hypot(x1-x0,y1-y0);
        ctx.save();ctx.clip(selected.shape,'evenodd');ctx.strokeStyle='#236a80';ctx.lineWidth=1.2/s;ctx.setLineDash([5/s,3/s]);ctx.beginPath();
        for(let t=-extent;t<=extent;t+=Math.max(.6,extent/9)){ctx.moveTo(cx+vx*t-ux*extent,cy+vy*t-uy*extent);ctx.lineTo(cx+vx*t+ux*extent,cy+vy*t+uy*extent)}ctx.stroke();ctx.restore();
      }
    }
    const convert=q=>[5+q[0]*l.width,5+q[1]*l.height],dots=(l.dots?.length?l.dots:l.guide||[]).map(convert);
    ctx.strokeStyle='#087d9f';ctx.fillStyle='#087d9f';ctx.lineWidth=2.5/s;ctx.setLineDash([]);
    if(dots.length>=2){ctx.beginPath();ctx.moveTo(...dots[0]);
      if(dots.length===3){const [a,m,b]=dots;ctx.quadraticCurveTo(2*m[0]-(a[0]+b[0])/2,2*m[1]-(a[1]+b[1])/2,...b)}else ctx.lineTo(...dots[1]);ctx.stroke();
    }
    dots.forEach(([x,y],i)=>{ctx.beginPath();ctx.arc(x,y,5/s,0,Math.PI*2);ctx.fill();ctx.fillStyle='#fff';ctx.font=`${8/s}px sans-serif`;ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(String(i+1),x,y);ctx.fillStyle='#087d9f'});
    ctx.restore();
  }
  dimensions(){
    const {ctx,canvas:c,data:d,s}=this,m=d.meta,x0=6*s,y0=6*s,x1=(6+m.design_width_mm)*s,y1=(6+m.design_height_mm)*s;
    const font=12*c.width/(parseFloat(c.style.width)||400);
    ctx.save();ctx.lineWidth=1.5;ctx.strokeStyle='#7a867b';ctx.fillStyle='#586555';
    ctx.font=`${font}px -apple-system,"PingFang SC",sans-serif`;ctx.textAlign='center';ctx.textBaseline='middle';
    const line=(x,y,X,Y)=>{ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(X,Y);ctx.stroke()};
    const b=y1+Math.min(3.5*s,Math.max(0,c.height-y1-font*.7-2)),r=x1+Math.min(3.5*s,Math.max(0,c.width-x1-font*.7-2));
    const tick=3*this.screenPixel;
    line(x0,y1+s,x0,b+tick);line(x1,y1+s,x1,b+tick);line(x0,b,x1,b);line(x1+s,y0,r+tick,y0);line(x1+s,y1,r+tick,y1);line(r,y0,r,y1);
    const text=(label,x,y)=>{const width=ctx.measureText(label).width;ctx.fillStyle='#fafaf6';ctx.fillRect(x-width/2-8,y-font/2-3,width+16,font+6);ctx.fillStyle='#586555';ctx.fillText(label,x,y)};
    text(`${Number(m.design_width_mm.toFixed(1))} mm`,(x0+x1)/2,b);
    ctx.translate(r,(y0+y1)/2);ctx.rotate(-Math.PI/2);text(`${m.design_height_mm.toFixed(1)} mm`,0,0);ctx.restore();
  }
  stitches(){
    const {ctx,data:d,s}=this,end=this.mode!=='simulation'?d.commands.length:this.index;
    if(this.mode==='result'&&this.material==='sheen'){
      const surface=this.threadSurfaceForView();
      if(surface){ctx.drawImage(surface,0,0,this.canvas.width,this.canvas.height);return}
    }
    let cache;
    if(end===d.commands.length)cache=this.full;
    else{if(end<this.partial.end)this.partial=this.emptyPaths();cache=this.extendPaths(this.partial,end)}
    ctx.save();ctx.scale(s,s);ctx.lineCap='round';ctx.lineJoin='round';
    // Keep illustrative thread width independent of row spacing, including old plans.
    const lineWidth=this.threadWidth??d.meta.preview_thread_width_mm??referenceWidth(d.meta.thread_spec);
    cache.paths.forEach((path,i)=>{
      const color=d.meta.colors[this.blocks[i].color];
      ctx.strokeStyle=color.hex;ctx.lineWidth=lineWidth;ctx.stroke(path);
    });
    if(this.showJumps&&this.mode==='simulation'){ctx.setLineDash([5/s,5/s]);ctx.strokeStyle='#b86c39';ctx.lineWidth=1.3/s;ctx.stroke(cache.jumps);ctx.setLineDash([])}
    if(this.mode==='simulation'&&cache.needle&&this.index<d.commands.length){
      ctx.beginPath();ctx.arc(cache.needle[0]+1,cache.needle[1]+1,6/s,0,Math.PI*2);ctx.fillStyle='#b86c39';ctx.fill();ctx.lineWidth=2/s;ctx.strokeStyle='white';ctx.stroke();
    }
    ctx.restore();
  }
}
