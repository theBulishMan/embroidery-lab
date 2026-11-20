// Machine commands include jumps, trims and color changes. UI positions count
// only needle penetrations, so the slider and single-step buttons agree.
export function stitchEnds(commands) {
  const ends=[];
  commands.forEach((p,i)=>{if(p[2]===0)ends.push(i+1)});
  return ends;
}

export function commandIndexForStitch(ends, commandLength, count) {
  const n=Math.max(0,Math.min(ends.length,Math.trunc(Number(count)||0)));
  if(n===0)return 0;
  return n===ends.length?commandLength:ends[n-1];
}

export function stitchCountAtIndex(ends,index) {
  let lo=0,hi=ends.length;
  while(lo<hi){const mid=(lo+hi)>>1;if(ends[mid]<=index)lo=mid+1;else hi=mid}
  return lo;
}

// Separate color blocks from palette entries: a later return to the same
// thread is a new sewing pass, not a new thread color.
export function colorBlocks(commands) {
  const blocks=[];let count=0;
  for(const point of commands){
    if(point[2]!==0)continue;
    let block=blocks.at(-1);
    if(!block||block.color!==point[3]){
      block={color:point[3],start:count,end:count};blocks.push(block);
    }
    count++;block.end=count;
  }
  return blocks;
}

export function colorProgress(blocks,count){
  if(!blocks.length)return {block:0,blocks:0,color:0,colorCount:0,colorTotal:0};
  let index=blocks.findIndex(b=>count<=b.end);
  if(index<0)index=blocks.length-1;
  const b=blocks[index];
  return {block:index+1,blocks:blocks.length,color:b.color,
    colorCount:Math.max(0,Math.min(b.end-b.start,count-b.start)),colorTotal:b.end-b.start};
}
