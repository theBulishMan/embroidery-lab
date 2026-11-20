import assert from 'node:assert/strict';
import {stitchEnds,commandIndexForStitch,stitchCountAtIndex,colorBlocks,colorProgress} from './studio-web/stitch-timeline.js';
import {Preview} from './studio-web/preview.js';

// Interleaved jumps, a trim, and a color change must not count as stitches.
const commands=[[0,0,1,0],[1,1,0,0],[1,1,2,0],[4,4,1,0],[5,5,0,0],[5,5,5,1],[6,6,1,1],[7,7,0,1],[7,7,4,1]];
const ends=stitchEnds(commands);
assert.deepEqual(ends,[2,5,8]);
for(let count=0;count<=3;count++){
  const index=commandIndexForStitch(ends,commands.length,count);
  assert.equal(stitchCountAtIndex(ends,index),count);
}
assert.equal(commandIndexForStitch(ends,commands.length,1),2);
assert.equal(commandIndexForStitch(ends,commands.length,2),5);
assert.equal(commandIndexForStitch(ends,commands.length,999),commands.length);
assert.equal(commandIndexForStitch(ends,commands.length,-1),0);
assert.equal(stitchCountAtIndex(ends,4),1);
assert.equal(commandIndexForStitch([],0,5),0);
const blocks=colorBlocks(commands);
assert.deepEqual(blocks,[{color:0,start:0,end:2},{color:1,start:2,end:3}]);
assert.deepEqual(colorProgress(blocks,2),{block:1,blocks:2,color:0,colorCount:2,colorTotal:2});
assert.equal(colorProgress(blocks,3).colorCount,1);
const repeated=colorBlocks([...commands,[8,8,0,0]]);
assert.equal(repeated.length,3);
assert.equal(colorProgress(repeated,4).color,0);
assert.equal(colorProgress(repeated,4).block,3);
assert.equal(colorProgress([],0).colorTotal,0);

// Returning to white for a highlight must paint AFTER the black eye, even
// though white was already used for the body at the start of the design.
globalThis.Path2D=class {constructor(){this.lines=[]}moveTo(x,y){this.start=[x,y]}lineTo(x,y){this.lines.push([this.start,[x,y]])}};
const renderer=Object.create(Preview.prototype);
renderer.data={commands:[[0,0,1,0],[1,0,0,0],[1,0,5,1],[2,0,1,1],[3,0,0,1],[3,0,5,0],[4,0,1,0],[5,0,0,0]],
  meta:{colors:[{hex:'#ffffff',rgb:[255,255,255]},{hex:'#222222',rgb:[34,34,34]}],thread_spec:'105D'}};
renderer.blocks=colorBlocks(renderer.data.commands);
renderer.ends=stitchEnds(renderer.data.commands);
renderer.full=renderer.extendPaths(renderer.emptyPaths(),renderer.data.commands.length);
renderer.mode='simulation';renderer.index=renderer.data.commands.length;renderer.s=1;
const painted=[];
renderer.ctx={save(){},restore(){},scale(){},stroke(path){painted.push([this.strokeStyle,path.lines])}};
renderer.stitches();
assert.deepEqual(painted.map(p=>p[0]),['#ffffff','#222222','#ffffff']);
assert.deepEqual(painted.map(p=>p[1].length),[1,1,1]);
const partial=renderer.extendPaths(renderer.emptyPaths(),5);
renderer.extendPaths(partial,renderer.data.commands.length);
assert.deepEqual(partial.paths.map(p=>p.lines),renderer.full.paths.map(p=>p.lines));
renderer.setMode=mode=>{renderer.mode=mode};renderer.seek=count=>{renderer.seeked=count};
renderer.throughColor(0);assert.equal(renderer.seeked,3);
console.log('Stitch navigation and returned-color paint order passed.');
