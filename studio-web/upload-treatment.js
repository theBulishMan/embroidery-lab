// The decision uses the local preview, before submitting or charging for a drawing.
// Excluding an opaque white margin prevents a small photo on a large page from
// looking like a single flat colour. This never removes white from the artwork.
export function inspectUpload(pixels,width,height){
  let clear=0,whiteEdges=0,edges=0;
  for(let y=0;y<height;y++)for(let x=0;x<width;x++){
    const i=(y*width+x)*4;
    if(pixels[i+3]<100)clear++;
    if(x===0||y===0||x===width-1||y===height-1){
      edges++;if(Math.min(pixels[i],pixels[i+1],pixels[i+2])>=242)whiteEdges++;
    }
  }
  const transparent=clear>width*height*.01,whiteCanvas=!transparent&&whiteEdges>.96*edges;
  const counts=new Uint32Array(4096);
  let total=0,uniform=0,left=width,right=-1,top=height,bottom=-1;
  for(let y=0;y<height;y++)for(let x=0;x<width;x++){
    const i=(y*width+x)*4;
    if(pixels[i+3]<=200)continue;
    const r=pixels[i],g=pixels[i+1],b=pixels[i+2];
    if(whiteCanvas&&Math.min(r,g,b)>237&&Math.max(r,g,b)-Math.min(r,g,b)<16)continue;
    left=Math.min(left,x);right=Math.max(right,x);top=Math.min(top,y);bottom=Math.max(bottom,y);
    counts[(r>>4)*256+(g>>4)*16+(b>>4)]++;total++;
    if(x+1===width||y+1===height)continue;
    const a=i+4,c=i+4*width;
    if(pixels[a+3]<=200||pixels[c+3]<=200)continue;
    if(Math.max(Math.abs(r-pixels[a]),Math.abs(g-pixels[a+1]),Math.abs(b-pixels[a+2]),
                Math.abs(r-pixels[c]),Math.abs(g-pixels[c+1]),Math.abs(b-pixels[c+2]))<=6)uniform++;
  }
  const common=Array.from(counts).sort((a,b)=>b-a).slice(0,18).reduce((sum,n)=>sum+n,0);
  const flat=total>=32&&common>=.86*total&&uniform>=.66*total;
  const aspect=right>left&&bottom>top?(bottom-top)/(right-left):null;
  return {flat,whiteCanvas,canvasAspect:height/width,subjectAspect:transparent?aspect:null,inkAspect:whiteCanvas?aspect:null};
}

export function uploadRoute(mode,info,prompt){
  if(mode!=='auto')return mode;
  return info?.flat&&!prompt.trim()?'flat':'upload';
}
