import assert from 'node:assert/strict';
import {narrowSatinTrial} from './studio-web/local-editor.js';
import {reviewRegion,reviewDetails} from './studio-web/detail-fit.js';
const region=(width,extra={})=>({id:'one',detail_metrics:{width_mm:width,smallest_hole_mm:null,narrow_fraction:{},...extra},recommendation:{method:'running'}});
const line=region(.35),before=JSON.stringify(line);
assert.equal(reviewRegion(line,'75D').level,'adapt');
assert.match(reviewRegion(line,'75D').label,/走针保留/);
assert.match(reviewRegion(line,'75D',{method:'fill'}).label,/调整工艺/);
assert.equal(reviewRegion(region(.12),'75D').level,'risk');
assert.equal(reviewRegion(region(.22),'75D').level,'adapt');
assert.equal(reviewRegion(region(.22),'105D').level,'risk');
assert.equal(reviewRegion(region(8,{smallest_hole_mm:.2}),'75D').level,'risk');
assert.equal(reviewRegion(region(8,{smallest_hole_mm:1}),'75D').level,'normal');
assert.equal(reviewRegion(region(8,{narrow_fraction:{'75D':.15}}),'75D').level,'risk');
const summary=reviewDetails([line,region(.12),region(8)],'75D');
assert.equal(summary.risk.length,1);assert.equal(summary.adapt.length,1);assert.equal(summary.normal.length,1);
assert.equal(JSON.stringify(line),before,'assessment must not remove or rewrite artwork');
const band={id:'a',recommendation:{method:'satin',structure:'band',width_mm:.43},detail_metrics:{hole_count:0}};
const meta={fabric:'twill',satin_spacing_mm:.35,quality_review:{reference_width_mm:.2,issues:[{region_key:'a',interior_mismatch_percent:44.8}]}};
const actual=[{stitch_type:'三重走针'}];
assert.deepEqual(narrowSatinTrial(band,meta,actual,{}),{pitch:.28,pull:.02,loss:44.8});
assert.equal(narrowSatinTrial({...band,recommendation:{method:'running',structure:'line',width_mm:.43}},meta,actual,{}),null);
assert.equal(narrowSatinTrial({...band,id:'sibling'},meta,actual,{}),null);
assert.equal(narrowSatinTrial(band,meta,actual,{method:'running'}),null);
assert.equal(narrowSatinTrial({...band,recommendation:{method:'running',width_mm:.22}},meta,actual,{}),null);
assert.equal(narrowSatinTrial(band,meta,[{stitch_type:'填针'}],{}),null);
console.log('Detail review passed: thin lines stay available, holes and branches are flagged, size/thread affect hints without changing artwork.');

const detail=region(6,{smallest_hole_mm:.32,narrow_fraction:{'75D':.10}});
const measured={thread:'75D',checked_narrow:true,checked_inlays:true,tested_tips:2,tested_openings:5};
assert.equal(reviewDetails([detail],'75D',{}, {one:measured}).risk.length,0);
assert.equal(reviewDetails([detail],'75D',{}, {one:measured}).adapt.length,1);
for(const e of [null,{...measured,thread:'105D'},{...measured,tested_openings:0},{...measured,checked_narrow:false}]){
  assert.equal(reviewRegion(detail,'75D',{},e).level,'risk');
}
assert.equal(reviewRegion(region(.10),'75D',{},measured).level,'risk');
