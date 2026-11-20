import assert from 'node:assert/strict';
import {stitchSegments,summarizeCoverage} from './studio-web/coverage.js';
import {referenceWidth,validThreadWidth} from './studio-web/thread-profile.js';

const moves=[];
stitchSegments([[0,0,1,0],[4,0,0,0],[4,0,2,0],[20,0,1,0],[24,0,0,0],[24,0,5,1],[24,3,1,1],[25,3,0,1]],(a,b,c)=>moves.push([a.slice(0,2),b.slice(0,2),c]));
assert.deepEqual(moves,[[[0,0],[4,0],0],[[20,0],[24,0],0],[[24,3],[25,3],1]],'jumps, trims and changes must not create stitched bridges');
const stationary=[];
stitchSegments([[0,0,1,0],[0,0,0,0],[1,0,0,0]],(a,b)=>stationary.push([a[0],b[0]]));
assert.deepEqual(stationary,[[0,1]],'a stationary needle does not lay a thread segment');
// Empty artwork holes and outside canvas are not denominator area. A different
// color underneath is separately reported, never counted as bare fabric.
const result=summarizeCoverage(Uint8Array.from([0,1,1,1,2,2,0]),Uint16Array.from([0,1,0,2,2,1,0]),.05,2);
assert.equal(result.total,5);assert.equal(result.bare,1);assert.equal(result.colorGap,2);
assert.equal(result.barePercent,20);assert.equal(result.colorGapPercent,40);
assert.equal(result.colors[0].bare,1);assert.equal(result.colors[1].colorGap,1);
assert.equal(summarizeCoverage(new Uint8Array(4),new Uint16Array(4),.05,1).barePercent,0);
assert.equal(referenceWidth('75D'),.20);assert.equal(referenceWidth('105D'),.24);
for(const v of [0,.09,.46,NaN,Infinity])assert.equal(validThreadWidth(v),false);
console.log('Coverage semantics passed: no jump bridges; holes excluded; wrong color separated; widths bounded.');
const hidden=summarizeCoverage(Uint8Array.from([1,1,2]),Uint16Array.from([3,1,3]),.05,2,Uint8Array.from([2,1,2]));
assert.equal(hidden.bare,0);assert.equal(hidden.colorGap,0);assert.equal(hidden.occluded,1,'a fine detail sewn underneath another color is not visible coverage');
