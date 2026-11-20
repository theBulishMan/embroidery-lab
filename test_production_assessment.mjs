import assert from 'node:assert/strict';
import {assessProduction, measuredRegion, repairForRegion} from './studio-web/production-assessment.js';
const valid=()=>({hasPlan:true,quality:{checked_regions:4,issues:[],reference_width_mm:.2,
  stitch_suitability:{checked_regions:4,direction_regions:3,issues:[]}},
  coverage:{state:'ready',lineWidth:.2,areaMm2:400,barePercent:0,colorGapPercent:0,occludedPercent:0},
  stats:{sew_geometry_dst_pes_equal:true,stitch_count:1000,max_stitch_mm:3.2,physically_sewn:false},
  details:{normal:[{},{},{},{}],adapt:[],risk:[]},totalRegions:4});
const check=(change)=>{const input=valid();change(input);return assessProduction(input)};
let result=assessProduction(valid());assert.equal(result.score,100);assert.equal(result.status,'trial');assert.equal(result.canTrial,true);
assert.match(result.production,/量产未确认/,'a perfect geometry score is not production approval');
assert.equal(assessProduction().score,null);
for(const mutate of [x=>delete x.quality,x=>x.quality.checked_regions=0,x=>delete x.stats,x=>delete x.stats.sew_geometry_dst_pes_equal,
  x=>x.coverage.barePercent=NaN,x=>x.coverage.areaMm2=0,x=>x.details.normal.pop(),x=>x.coverage.occludedPercent=undefined,
  x=>x.coverage={state:'error'},x=>x.quality.checked_regions=1.5,x=>delete x.quality.stitch_suitability,
  x=>x.quality.stitch_suitability.checked_regions=3]){
  result=check(mutate);assert.equal(result.score,null);assert.equal(result.canTrial,false,'missing evidence cannot pass');
}
result=check(x=>x.coverage={state:'loading'});assert.equal(result.status,'pending');assert.equal(result.score,null);
result=check(x=>x.stale=true);assert.equal(result.status,'stale');assert.equal(result.score,null);
result=check(x=>x.detailsReady=false);assert.equal(result.score,null);
const appropriate=check(x=>x.coverage.barePercent=1.2);
assert.equal(appropriate.score,100,'meeting coverage is enough');
const fullButWrong=check(x=>x.quality.stitch_suitability.issues.push({region_key:'stroke',reason:'曲线被填成色面'}));
assert.equal(fullButWrong.canTrial,false);assert.ok(appropriate.score>fullButWrong.score);
result=check(x=>x.coverage.lineWidth=.44);assert.equal(result.widthMismatch,true);assert.equal(result.canTrial,false);assert.equal(result.score,null,'wider render cannot inflate a mixed-width assessment');
result=check(x=>x.quality.omitted_motifs=[{count:15,area_mm2:2,bbox_mm:[1,2,6,5]}]);
assert.equal(result.canTrial,false);assert.ok(result.score<80);assert.match(result.reasons.join(' '),/15 处/,'major ornaments lost during resizing must block a score even on perfect fitted geometry');
result=check(x=>x.coverage.barePercent=2);assert.equal(result.status,'adjust');assert.equal(result.score,79,'hard risk overrides high average');
result=check(x=>{x.coverage.colorGapPercent=2;x.coverage.occludedPercent=3});assert.equal(result.status,'adjust');assert.equal(result.score,79);
result=check(x=>{x.quality.checked_regions=1000;x.quality.issues=[{region_key:'important'}]});assert.equal(result.score,79);assert.equal(result.canTrial,false,'one localized failure cannot be washed out by many good objects');
result=check(x=>{x.details.normal.pop();x.details.risk.push({})});assert.equal(result.status,'adjust');assert.equal(result.score,79);
for(const change of [x=>x.stats.sew_geometry_dst_pes_equal=false,x=>x.stats.max_stitch_mm=7,x=>x.stats.stitch_count=0,x=>x.coverage.barePercent=10,x=>x.coverage.occludedPercent=20]){
  result=check(change);assert.equal(result.status,'blocked');assert.ok(result.score<=39);assert.equal(result.canTrial,false);
}
result=check(x=>{x.quality.checked_regions=33;x.quality.issues=Array(11).fill({});x.totalRegions=x.quality.stitch_suitability.checked_regions=59;x.details={normal:Array(53).fill({}),risk:Array(6).fill({}),adapt:[]};Object.assign(x.coverage,{barePercent:3.03,colorGapPercent:1.93,occludedPercent:8.31})});
assert.equal(result.score,78);assert.equal(result.status,'adjust');assert.equal(result.reasons.length,4);
const initial=valid(),snapshot=JSON.stringify(initial);assessProduction(initial);assert.equal(JSON.stringify(initial),snapshot,'evaluation must not alter artwork or evidence');
const coverage={state:'ready',regions:[{id:'petal',bareMm2:0.12,colorMm2:0.6,occludedMm2:0.5}],measuredRegionIds:['petal','body']};
const quality={issues:[],gap_bridges:[{first:'petal',second:'petal2',gap_mm:0.18,point_mm:[5,7]}]};
assert.deepEqual(measuredRegion(coverage,'body'),{bare:0,color:0});
assert.equal(measuredRegion(coverage,'absent'),null);
assert.deepEqual(repairForRegion('petal',quality,coverage,'蓝色').point,[5,7]);
assert.match(repairForRegion('petal',{issues:[]},coverage,'蓝色').title,/后绣/);
assert.match(repairForRegion('petal',{...quality,unstitched_regions:['petal']},coverage,'蓝色').title,/未落针/);
assert.match(repairForRegion('body',{issues:[]},coverage,'蓝色',{level:'risk',reasons:['局部留白很窄']}).note,/局部留白很窄/);
assert.equal(repairForRegion('body',{issues:[]},coverage,'蓝色'),null);
console.log('Production assessment passed: explicit score, risk gates, unknown/stale evidence, width mismatch, and no automatic production approval.');
