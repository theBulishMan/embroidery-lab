import {referenceWidth} from './thread-profile.js';

// Hints only: they never remove artwork or replace a manual stitch choice.
export function reviewRegion(region,thread,settings={},observed=null){
  const facts=region.detail_metrics;
  if(!facts)return {level:'unknown',rank:0,label:'等待尺寸复核',reasons:[]};
  const width=facts.width_mm,reference=referenceWidth(thread),target=thread==='75D'?.7:1;
  const method=settings.method||region.recommendation?.method||'fill';
  const reasons=[],verified=[];
  const matches=observed?.thread===thread;
  const tipsVerified=matches&&observed.checked_narrow&&observed.tested_tips>0;
  const openingsVerified=matches&&observed.checked_inlays&&observed.tested_openings>0;
  if(width<reference)reasons.push(`细节宽约 ${width.toFixed(2)} mm，小于参考线宽 ${reference.toFixed(2)} mm，可能变粗或消失`);
  if(facts.smallest_hole_mm!=null&&facts.smallest_hole_mm<reference*2){
    if(openingsVerified)verified.push('已在导出针迹中核对嵌色和相邻间隙');
    else reasons.push(`内部小孔或分隔约 ${facts.smallest_hole_mm.toFixed(2)} mm，可能被相邻绣线挤合`);
  }
  if((facts.narrow_fraction?.[thread]||0)>.04){
    if(tipsVerified)verified.push('已逐处检查尖角保留和边缘串色');
    else reasons.push('局部有窄颈、细分支或尖角，需要放大核对');
  }
  if(reasons.length)return {level:'risk',rank:2,label:'局部细节有风险',width,reasons,action:'先尝试放大尺寸、调整针法或局部加粗／留宽间隙；仍无法表达时，再决定合并或舍弃。主体和大花纹保持。'};
  if(verified.length)return {level:'adapt',rank:1,label:'已按实际针迹核对',width,reasons:verified,action:'试绣时重点观察这些局部。'};
  if(width<target){
    const line=['run','running'].includes(method),satin=method==='satin';
    return {level:'adapt',rank:1,label:line?'可尝试用走针保留':satin?'可尝试用缎面保留':'细块需调整工艺',width,
      reasons:[`宽约 ${width.toFixed(2)} mm；${line?'保留线条表达，不等同原来的实心面':satin?'窄列可试排，仍需核对两侧针路':'当前填针未必能清楚表达，可拆开窄条后单独选针法'}`],
      action:'先调整针法并试绣，不因色块小就直接删除。'};
  }
  return {level:'normal',rank:0,label:'优先保留原图',width,reasons:['未触发当前尺寸提示，仍需真实针迹与试绣检查。']};
}

export function reviewDetails(regions,thread,overrides={},witnesses={}){
  const items=regions.map(region=>({region,...reviewRegion(region,thread,overrides[region.id],witnesses?.[region.id])}));
  return {risk:items.filter(x=>x.level==='risk'),adapt:items.filter(x=>x.level==='adapt'),
    normal:items.filter(x=>x.level==='normal'),
    inspect:items.filter(x=>x.rank).sort((a,b)=>b.rank-a.rank||(b.region.area_mm2||0)-(a.region.area_mm2||0)||(a.width??Infinity)-(b.width??Infinity))};
}
