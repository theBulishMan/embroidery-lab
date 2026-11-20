"""Turn saved artwork and stitch measurements into a specific revision brief."""
import math


def review_draft(version, plan, observations, regions):
    settings = plan or version.get('preferred_settings') or {}
    width, thread = settings.get('width'), settings.get('thread')
    if not width or not thread:
        raise ValueError('请先确定本版成品尺寸和线材。')
    if not isinstance(observations, dict):
        raise ValueError('评估数据无效。')
    def percent(key):
        value = observations.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
            return None
        return round(value, 2)
    fit = version.get('fit_report', {})
    if fit.get('width') != width or fit.get('thread') != thread:
        fit = {}
    q = (plan or {}).get('quality_review') or {}
    items = []
    def add(kind, text, region=None):
        if region is not None and (not isinstance(region,str) or region not in regions):
            return
        if len(items) < 12 and not any(i['text']==text and i['region']==region for i in items):
            items.append({'kind':kind, 'text':text, 'region':region})
    motifs = fit.get('omitted_motifs', [])
    if (version.get('revision_registration') or {}).get('needs_review'):
        add('artwork','修改稿的外形或宽高比例偏离初版，需对齐原稿轮廓和成品尺寸。')
    if motifs:
        count = sum(m['count'] for m in motifs)
        add('artwork', f'{len(motifs)} 组纹样中有 {count} 处在尺寸适配时消失。把过密的同向碎纹归成清楚的主纹，保住主花纹、长线及笔画间隙。')
        locations=[]
        for motif in motifs[:4]:
            bounds=motif.get('bbox_mm',[])
            if len(bounds)!=4:continue
            x,y,X,Y=bounds
            locations.append({'point':[(x+X)/2,(y+Y)/2],'color':motif['color'],'count':motif['count']})
        items[-1]['locations']=locations
    for g in q.get('gap_bridges', [])[:3]:
        add('gap',f'两笔原隔 {g["gap_mm"]:.2f} mm，针迹挤进了留白。收回朝向间隙的落针，避开跨缝藏线。',g['first'])
    for item in (q.get('stitch_suitability') or {}).get('issues', [])[:3]:
        add('direction',item.get('reason') or '此处落针与原稿走向不合。请核对该区的针法和针向。',item['region_key'])
    for item in q.get('issues', [])[:3]:
        add('shape','、'.join(item['reasons'])+'。核对该块的针向、边缘补偿与相邻色覆盖。',item['region_key'])
    for rid in q.get('unstitched_regions',[])[:2]:
        add('shape','该区没有对应针迹。按实际宽度保留中心走线或把碎片归回主体。',rid)
    bare, wrong = percent('bare'), percent('color')
    envelope = q.get('needle_envelope') or {}
    if envelope.get('needs_review'):
        sides = envelope.get('outside_mm') or {}
        side = max(sides, key=sides.get) if sides else None
        where = f'{side}侧超出原稿 {sides[side]:.2f} mm。' if side else ''
        add('size',where+'核对外轮廓的拉缩补偿和包边宽度。')
    def valid_ids(key):
        values=observations.get(key,[])
        return [i for i in values[:6] if isinstance(i,str) and i in regions] if isinstance(values,list) else []
    if plan:
        ids=valid_ids('coverage_ids')
        if bare is not None and bare >= 2:
            add('coverage',f'预计露底 {bare:g}%。检查露底区的行距和接缝搭接。',next(iter(valid_ids('bare_ids') or ids),None))
        if wrong is not None and wrong >= 5:
            add('overlap',f'有 {wrong:g}% 的应绣区域缺少本色。检查覆盖顺序和伸进相邻色的包边。',next(iter(valid_ids('color_ids') or ids),None))
        for rid in dict.fromkeys(valid_ids('detail_ids')):
            add('detail','此处有窄颈、细笔或小孔。可绣细笔沿中心走，转角和笔画间保留空隙。',rid)
        score = percent('score')
        if not items and score is not None and score < 80:
            add('file','当前方案未通过试绣检查。请查看下方评估中的具体原因，核对机针文件后重新排针。')
    locked=(plan or {}).get('object_overrides',{})
    targets=list(dict.fromkeys(item['region'] for item in items if item['region'] and item['region'] not in locked))
    manual=bool(locked or (plan or {}).get('region_cuts'))
    route='artwork' if motifs and not manual else 'craft' if plan and targets else None
    return {'items':items,'targets':targets[:3],'route':route,'width':width,'thread':thread,
            'height':round(width*version['aspect_ratio'],3),'motifs':sum(m['count'] for m in motifs),
            'gap_bridges':len(q['gap_bridges']) if isinstance(q.get('gap_bridges'),list) else None,
            'shape_issues':len(q['issues']) if isinstance(q.get('issues'),list) else None,
            'direction_issues':len(q['stitch_suitability']['issues']) if 'issues' in (q.get('stitch_suitability') or {}) else None,
            'unstitched':len(q['unstitched_regions']) if isinstance(q.get('unstitched_regions'),list) else None,
            'bare':bare,'color':wrong,'score':percent('score') if plan else None}
