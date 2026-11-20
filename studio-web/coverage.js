// Only needle-down moves contribute coverage. A jump is never drawn as thread.
export function stitchSegments(commands,visit){
  let last=null;
  for(const point of commands){
    if(point[2]===0&&last&&(point[0]!==last[0]||point[1]!==last[1]))visit(last,point,point[3]);
    last=point;
  }
}

export function summarizeCoverage(expected,covered,mmPerPixel,colorCount,top=null){
  const colors=Array.from({length:colorCount},()=>({total:0,bare:0,colorGap:0,occluded:0}));
  let total=0,bare=0,colorGap=0,occluded=0;
  for(let i=0;i<expected.length;i++){
    const color=expected[i]-1;if(color<0)continue;
    total++;colors[color].total++;
    if(!covered[i]){bare++;colors[color].bare++}
    else if(!(covered[i]&(1<<color))){colorGap++;colors[color].colorGap++}
    else if(top&&top[i]!==expected[i]){occluded++;colors[color].occluded++}
  }
  const area=mmPerPixel*mmPerPixel;
  return {total,bare,colorGap,occluded,occludedMm2:occluded*area,occludedPercent:total?100*occluded/total:0,areaMm2:total*area,bareMm2:bare*area,colorGapMm2:colorGap*area,
    barePercent:total?100*bare/total:0,colorGapPercent:total?100*colorGap/total:0,
    colors:colors.map(c=>({...c,bareMm2:c.bare*area,colorGapMm2:c.colorGap*area,occludedPercent:c.total?100*c.occluded/c.total:0,
      barePercent:c.total?100*c.bare/c.total:0,colorGapPercent:c.total?100*c.colorGap/c.total:0}))};
}

export function analyzeCoverage(data,lineWidth,makeCanvas){
  if(data.meta.command_registration!=='explicit-design-center')throw new Error('这份旧工程没有保存图案对齐信息，请重新生成针迹后检查露底。');
  if(!Number.isFinite(lineWidth)||lineWidth<.10||lineWidth>.45)throw new Error('参考线宽需要在 0.10–0.45 mm 之间。');
  const mmPerPixel=Math.max(.025,Math.max(data.width+2,data.height+2)/2400);
  const scale=1/mmPerPixel,width=Math.ceil((data.width+2)*scale),height=Math.ceil((data.height+2)*scale);
  const canvas=makeCanvas(width,height),ctx=canvas.getContext('2d',{willReadFrequently:true});
  const expected=new Uint8Array(width*height),covered=new Uint16Array(width*height);
  const shapes=data.meta.separated_objects,owners=new Uint16Array(width*height);
  const issues=shapes.map(region=>({
    id:region.region_key||region.id,color:region.color,bare:0,wrong:0,occluded:0,samples:0,point:null,distance:Infinity,
    barePoint:null,bareDistance:Infinity,
    center:region.bbox?[(region.bbox[0]+region.bbox[2])/2,(region.bbox[1]+region.bbox[3])/2]:null
  }));
  const top=new Uint8Array(width*height),blocks=[];
  const paths=Array.from({length:data.meta.colors.length},()=>new Path2D());
  stitchSegments(data.commands,(a,b,ci)=>{
    paths[ci].moveTo(a[0],a[1]);paths[ci].lineTo(b[0],b[1]);
    if(blocks.at(-1)?.color!==ci)blocks.push({color:ci,path:new Path2D()});
    const p=blocks.at(-1).path;p.moveTo(a[0],a[1]);p.lineTo(b[0],b[1]);
  });
  const reset=()=>{ctx.setTransform(1,0,0,1,0,0);ctx.clearRect(0,0,width,height);ctx.setTransform(scale,0,0,scale,0,0)};
  for(let ci=0;ci<paths.length;ci++){
    reset();
    // Colour channels carry ownership in the same pass that measures target
    // coverage. Alpha still determines the original geometric colour mask.
    for(let index=0;index<shapes.length;index++){
      const region=shapes[index];if(region.color!==ci)continue;
      const code=index+1;
      const ink=code<=65535?`rgb(${code&255},${code>>>8},${code*73%251})`:'#000';
      const shape=new Path2D(region.path);
      if(region.structure==='line' && region.center_path){
        ctx.save();ctx.clip(shape,'evenodd');
        ctx.strokeStyle=ink;ctx.lineWidth=lineWidth;ctx.lineJoin=ctx.lineCap='round';
        ctx.stroke(new Path2D(region.center_path));ctx.restore();
      }else{
        ctx.fillStyle=ink;ctx.fill(shape,'evenodd');
      }
    }
    let pixels=ctx.getImageData(0,0,width,height).data;
    for(let i=0;i<expected.length;i++)if(pixels[i*4+3]>=128){
      const p=i*4,code=pixels[p]+(pixels[p+1]<<8);
      expected[i]=ci+1;
      owners[i]=issues[code-1]?.color===ci && pixels[p+2]===code*73%251 ? code : 0;
    }
    reset();ctx.strokeStyle='#fff';ctx.lineCap='round';ctx.lineJoin='round';ctx.lineWidth=lineWidth;ctx.stroke(paths[ci]);
    pixels=ctx.getImageData(0,0,width,height).data;
    for(let i=0;i<covered.length;i++)if(pixels[i*4+3]>=128)covered[i]|=1<<ci;
  }
  // Paint in actual machine order: a later color can hide a fine motif even
  // when a thread of the expected color exists underneath it.
  reset();ctx.lineCap='round';ctx.lineJoin='round';ctx.lineWidth=lineWidth;
  for(const block of blocks){ctx.strokeStyle=`rgb(${(block.color+1)*24},0,0)`;ctx.stroke(block.path)}
  const surface=ctx.getImageData(0,0,width,height).data;
  for(let i=0;i<top.length;i++)if(surface[i*4+3]>=128)top[i]=Math.round(surface[i*4]/24);
  const summary=summarizeCoverage(expected,covered,mmPerPixel,paths.length,top);
  const overlay=new Uint8ClampedArray(width*height*4);
  for(let i=0;i<expected.length;i++)if(expected[i]){
    const bare=!covered[i],wrong=!bare&&(!(covered[i]&(1<<(expected[i]-1)))||top[i]!==expected[i]);
    const issue=issues[owners[i]-1];
    if(issue)issue.samples++;
    if(bare||wrong){
      const p=i*4;overlay[p]=bare?222:225;overlay[p+1]=bare?54:149;overlay[p+2]=bare?65:23;overlay[p+3]=220;
      if(!issue)continue;
      if(bare)issue.bare++;else issue.wrong++;
      if(wrong&&(covered[i]&(1<<(expected[i]-1))))issue.occluded++;
      const x=(i%width+.5)*mmPerPixel,y=(Math.floor(i/width)+.5)*mmPerPixel;
      const distance=issue.center?(x-issue.center[0])**2+(y-issue.center[1])**2:0;
      if(distance<issue.distance){issue.distance=distance;issue.point=[x,y]}
      if(bare&&distance<issue.bareDistance){issue.bareDistance=distance;issue.barePoint=[x,y]}
    }
  }
  const area=mmPerPixel**2;
  const regions=issues.filter(r=>r.point).sort((a,b)=>(b.bare+b.wrong)-(a.bare+a.wrong))
    .map(r=>({id:r.id,color:r.color,point:r.point,barePoint:r.barePoint,barePercent:100*r.bare/r.samples,
      bareMm2:r.bare*area,colorMm2:r.wrong*area,occludedMm2:r.occluded*area}));
  const measuredRegionIds=issues.filter(r=>r.samples>0).map(r=>r.id);
  return {...summary,mmPerPixel,width,height,lineWidth,overlay,regions,measuredRegionIds};
}
