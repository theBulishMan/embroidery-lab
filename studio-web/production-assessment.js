// A transparent product heuristic, never a sewing success probability or
// production certificate. Missing/stale evidence must not turn into a zero risk.
export const ASSESSMENT_RULES={version:'production-v2-structure',craftWeight:35,coverageWeight:15,shapeWeight:25,detailWeight:15,fileWeight:10,
  coveragePenaltyPerPercent:2,bareLimit:2,colorLimit:5,bareReject:10,colorReject:20,trialScore:80,maxStitch:6.1};
const clamp=(v,lo=0,hi=1)=>Math.max(lo,Math.min(hi,v));
const finite=v=>typeof v==='number'&&Number.isFinite(v);
const count=v=>Number.isInteger(v)&&v>=0;
const pct=v=>finite(v)&&v>=0&&v<=100;

export function repairForRegion(id, quality, coverage, name, detail) {
  if (!id || !quality || coverage?.state !== 'ready') return null;
  const gap = quality.gap_bridges?.find(g => g.first === id || g.second === id);
  const defect = quality.issues?.find(i => i.region_key === id);
  const measured = coverage.regions.find(r => r.id === id);
  if (quality.unstitched_regions?.includes(id)) {
    return {title: name + '有细节未落针', note: '按当前尺寸，这个细节没有独立针迹。若必须保留，回平面稿加宽细节，或放大成品再排。', artwork: true};
  }
  const suitability=quality.stitch_suitability?.issues.find(item=>item.region_key===id);
  if(suitability)return {title:name+'需要调整针法或针向',note:suitability.reason};
  if (gap) {
    return {title: name + '的两块花纹可能连在一起', point: gap.point_mm,
      note: `原稿在这里留了 ${gap.gap_mm.toFixed(2)} mm，按当前线宽，落针后留白可能被盖住。放大核对；需要分开时，可在平面稿加宽这道留白，或增加成品尺寸。`, artwork: true};
  }
  if (defect) {
    return {title: name + '的形状需要复核', note: defect.reasons.join('、') + '。核对当前选区的针向、补偿和前后绣序，再重新排针。'};
  }
  if (detail?.level === 'risk') {
    return {title: name + '有细节接近当前线宽', note: detail.reasons.join('。') + '。可以加宽原稿的细线或孔隙，也可以增大成品尺寸再排。', artwork: true};
  }
  if (!measured || measured.bareMm2 + measured.colorMm2 <= 0) return null;
  if (measured.bareMm2 >= measured.colorMm2) {
    return {title: name + '里有露底位置', note: `此块有 ${measured.bareMm2.toFixed(2)} mm² 预计露底。若空隙在填针行之间，可小幅缩小行距。若集中在交界，先核对相邻两块的搭接。`};
  }
  if (measured.occludedMm2 > measured.colorMm2 / 2) {
    return {title: name + '被后绣的颜色盖住', note: `其中 ${measured.occludedMm2.toFixed(2)} mm² 原本有这色绣线。先核对相邻色的绣序和压边；加密底下这块通常无助于显色。`};
  }
  return {title: name + '有位置缺少本色', note: '对照平面稿，检查针迹有没有铺到这块的边缘。窄条可尝试缎面，细线可沿中心走针。'};
}

export function measuredRegion(coverage, id) {
  if (!coverage?.measuredRegionIds?.includes(id)) return null;
  const r = coverage.regions.find(region => region.id === id);
  return {bare: r?.bareMm2 ?? 0, color: r?.colorMm2 ?? 0};
}

export function narrowBorderTrial(plan, objects, coverage) {
  if (!plan || plan.width>35 || plan.fabric!=='twill' || !['75D','105D'].includes(plan.thread) || !coverage) return null;
  const pitch=plan.thread==='75D'?.24:.28;
  const eligible=new Set(objects.filter(o=>o.role==='最后包边' && o.width_mm>=.55 && o.width_mm<=1.6 &&
    o.peak_spacing_mm>pitch && !plan.object_overrides?.[o.region_key]).map(o=>o.region_key));
  const affected=coverage.regions.find(r=>eligible.has(r.id) && r.bareMm2+r.colorMm2>=.35);
  return affected?{id:affected.id,pitch}:null;
}

export function assessProduction({hasPlan=false,stale=false,quality,coverage,stats,details,totalRegions=0,detailsReady=true}={}){
  const result={ruleVersion:ASSESSMENT_RULES.version,score:null,canTrial:false,production:'量产未确认 · 尚未实物试绣',components:[],reasons:[]};
  const wait=(status,title,next)=>({...result,status,title,next});
  if(stale)return wait('stale','参数已修改，原评估已失效','应用修改并重新生成针迹，再评估当前版本。');
  if(!hasPlan)return wait('draft','等待排针后评估','先生成真实针迹，平面稿不能直接得出生产结论。');
  if(!quality||!count(quality.checked_regions)||!Array.isArray(quality.issues)||quality.issues.length>quality.checked_regions)
    return wait('incomplete','检查资料不完整','这份工程缺少局部形状检查，请重新生成针迹。');
  if(coverage?.state==='error')return wait('incomplete','覆盖检查未完成','重试覆盖检查；本次不显示分数或可试绣结论。');
  if(coverage?.state!=='ready'||!detailsReady)return wait('pending','正在汇总评估','正在检查真实针迹覆盖和细节尺寸，完成后自动显示。');
  if(!finite(quality.reference_width_mm)||!finite(coverage.lineWidth)||Math.abs(quality.reference_width_mm-coverage.lineWidth)>.001)
    return {...wait('incomplete','参考线宽已变，需重新核对','当前覆盖检查与局部形状检查采用不同线宽，不能合成同一个评分。'),widthMismatch:true};
  if(!pct(coverage.barePercent)||!pct(coverage.colorGapPercent)||!pct(coverage.occludedPercent)||!finite(coverage.areaMm2)||coverage.areaMm2<=0||
     coverage.barePercent+coverage.colorGapPercent+coverage.occludedPercent>100.01)
    return wait('incomplete','覆盖检查数据不足','未取得有效的图案面积和颜色覆盖数据，请重试检查。');
  const risk=details?.risk?.length,adapt=details?.adapt?.length,normal=details?.normal?.length;
  if(!count(totalRegions)||totalRegions===0||![risk,adapt,normal].every(count)||risk+adapt+normal!==totalRegions||quality.checked_regions===0)
    return wait('incomplete','细节检查范围不足','部分色块尚未完成尺寸或局部形状检查，请重新评估。');
  if(!stats||typeof stats.sew_geometry_dst_pes_equal!=='boolean'||!finite(stats.stitch_count)||!finite(stats.max_stitch_mm))
    return wait('incomplete','文件核对记录不足','缺少机器文件读回结果，请重新生成针迹后评估。');
  const craft=quality.stitch_suitability;
  if(!craft||craft.checked_regions!==totalRegions||!count(craft.direction_regions)||craft.direction_regions>totalRegions||
     !Array.isArray(craft.issues)||craft.issues.length>totalRegions)
    return wait('incomplete','需要补查针法与针向','用新版工艺重排，逐块核对所选针法与实际落针方向。');
  const rules=ASSESSMENT_RULES,bare=coverage.barePercent,color=coverage.colorGapPercent+coverage.occludedPercent;
  const filesOK=stats.sew_geometry_dst_pes_equal&&stats.stitch_count>0&&stats.max_stitch_mm>0&&stats.max_stitch_mm<=rules.maxStitch;
  const shapeCount=quality.issues.length;
  const coveragePenalty=Math.max(0,bare-rules.bareLimit)+Math.max(0,color-rules.colorLimit);
  const components=[
    {label:'针法与针向',points:(1-craft.issues.length/totalRegions)*rules.craftWeight,max:rules.craftWeight,
      note:`${totalRegions} 区核对针法 · ${craft.direction_regions} 区比对落针方向 · ${craft.issues.length} 区需调整`},
    {label:'应绣处覆盖与线色',points:clamp(1-coveragePenalty*rules.coveragePenaltyPerPercent/rules.coverageWeight)*rules.coverageWeight,max:rules.coverageWeight,
      note:`未覆盖 ${bare.toFixed(2)}% · 本色异常 ${color.toFixed(2)}%`},
    {label:'局部形状',points:(1-shapeCount/quality.checked_regions)*rules.shapeWeight,max:rules.shapeWeight,
      note:`${quality.checked_regions} 区已查 · ${shapeCount} 区异常`+(quality.below_resolution_regions?.length?` · ${quality.below_resolution_regions.length} 处过细，需实样核对`:'')},
    {label:'细节尺寸',points:(normal+adapt*.5)/totalRegions*rules.detailWeight,max:rules.detailWeight,
      note:`${totalRegions} 区已查 · ${risk} 区有风险 · ${adapt} 区需适配`},
    {label:'文件检查',points:filesOK?rules.fileWeight:0,max:rules.fileWeight,
      note:filesOK?'DST / PES 落针一致 · 针长检查通过':'格式一致性或针长检查未通过'},
  ];
  const rawScore=components.reduce((s,c)=>s+c.points,0),reasons=[];
  if(craft.issues.length)reasons.push(`${craft.issues.length} 处针法或针向与图案结构不合`);
  if(quality.omitted_motifs?.length)reasons.push(`尺寸适配省略了原稿的 ${quality.omitted_motifs.reduce((sum,g)=>sum+g.count,0)} 处成组或明显花纹；请放大尺寸或重新设计细节`);
  if(!filesOK)reasons.push('机器文件或针长检查未通过');
  if(shapeCount)reasons.push(`${shapeCount} 处局部形状异常`);
  if(quality.gap_bridges?.length)reasons.push(`${quality.gap_bridges.length} 处应留出的间隙被相邻的同色针迹挤占`);
  if(quality.unstitched_regions?.length)reasons.push(`${quality.unstitched_regions.length} 个微小选区没有产生独立色段`);
  if(quality.needle_envelope?.needs_review){
    const [side,mm]=Object.entries(quality.needle_envelope.outside_mm).sort((a,b)=>b[1]-a[1])[0];
    reasons.push(`${side}侧最外落针超出图案 ${mm.toFixed(2)} mm，请复核边缘补偿`);
  }
  if(bare>=rules.bareLimit)reasons.push(`几何未覆盖 ${bare.toFixed(2)}%`);
  if(color>=rules.colorLimit)reasons.push(`本色异常 ${color.toFixed(2)}%`);
  if(risk)reasons.push(`${risk} 处细节尺寸风险`);
  if(rawScore<rules.trialScore&&!reasons.length)reasons.push('综合评分未达到试绣参考线');
  const status=!filesOK||bare>=rules.bareReject||color>=rules.colorReject?'blocked':reasons.length?'adjust':'trial';
  const score=Math.min(Math.round(rawScore),status==='blocked'?39:status==='adjust'?79:100);
  return {...result,status,score,rawScore,components,reasons,canTrial:status==='trial',
    title:status==='blocked'?'当前方案不宜上机':status==='adjust'?'先调整，再安排试绣':'具备试绣条件',
    next:status==='blocked'?'先修正文件或大范围覆盖异常，重新排针并复评后，再安排试绣。':status==='adjust'?'先定位风险部位，调整针法、间隙或尺寸；重新排针后复评。':'建议按当前尺寸、线材和底料试绣，再核对覆盖、变形和细节。',
  };
}
