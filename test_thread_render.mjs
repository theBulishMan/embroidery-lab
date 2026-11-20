import assert from 'node:assert/strict';
import {test} from 'node:test';
import {renderThreadSurface,threadRenderSize,Preview} from './studio-web/preview.js';

function surface(){
  const strokes=[],gradients=[];
  const ctx={clearRect(){},setTransform(){},beginPath(){this.path=[]},moveTo(...p){this.path.push(p)},lineTo(...p){this.path.push(p)},
    createLinearGradient(...axis){const g={axis,stops:[],addColorStop(at,color){this.stops.push([at,color])}};gradients.push(g);return g},
    stroke(){strokes.push({path:this.path,width:this.lineWidth,color:this.strokeStyle,alpha:this.globalAlpha})}};
  return {ctx,strokes,gradients,canvas:{getContext(){return ctx}}};
}
const colors=[{rgb:[250,220,30]},{rgb:[30,30,30]}];

test('material follows machine order including underlay and later same-colour passes',async()=>{
  const s=surface(),commands=[[1,1,1,0],[8,8,0,0],[1,5,1,0],[8,5,0,0],[8,5,5,1],[5,1,1,1],[5,8,0,1],[5,8,5,0],[2,6,1,0],[6,6,0,0],[6,6,2,0],[6,6,4,0]];
  const original=structuredClone(commands);
  const result=await renderThreadSurface({commands,colors,lineWidth:.2,width:100,height:100,scale:10},()=>s.canvas);
  assert.equal(result.stitches,4);
  assert.equal(s.gradients.length,4);
  assert.deepEqual(s.strokes.filter(x=>typeof x.color==='object').map(x=>x.path),[[[1,1],[8,8]],[[1,5],[8,5]],[[5,1],[5,8]],[[2,6],[6,6]]]);
  assert.deepEqual(commands,original,'preview must not mutate export geometry');
});

test('material strokes never widen thread or paint jumps/background',async()=>{
  const s=surface();await renderThreadSurface({commands:[[0,0,1,0],[4,0,0,0],[12,12,1,0]],colors,lineWidth:.2,width:160,height:160,scale:10},()=>s.canvas);
  assert.equal(s.gradients.length,1);
  for(const stroke of s.strokes)assert.ok(stroke.width<=.2);
  for(const stroke of s.strokes)for(const [x,y] of stroke.path){assert.ok(x>=0&&x<=4);assert.ok(Math.abs(y)+stroke.width/2<=.100001)}
  assert.equal(s.strokes[0].alpha,1,'top stitch must actually occlude previous material');
});

test('preview bitmap bounds include extreme aspect ratios',()=>{
  for(const [w,h] of [[800,900],[9000,9000],[1000,50000],[50000,1000]]){
    const size=threadRenderSize(w,h);assert.ok(size.width<=2048&&size.height<=2048);assert.ok(size.width*size.height<=3003000);
    assert.ok(Math.abs(size.width/size.height-w/h)<.1);
  }
});

test('invalidated work cannot keep painting or retain old bitmap',async()=>{
  const s=surface();const result=await renderThreadSurface({commands:[[0,0,1,0],[3,3,0,0]],colors,lineWidth:.2,width:20,height:20,scale:1},()=>s.canvas,{cancelled:()=>true});
  assert.equal(result,null);assert.equal(s.gradients.length,0);
  let stopped=0,closed=0;const preview=Object.create(Preview.prototype);
  Object.assign(preview,{threadRenderId:2,renderWorker:{terminate(){stopped++}},threadSurface:{close(){closed++}},threadRenderKey:'old',threadRenderPending:true});
  preview.invalidateThreads();assert.equal(preview.threadRenderId,3);assert.equal(stopped,1);assert.equal(closed,1);assert.equal(preview.threadSurface,null);
});
