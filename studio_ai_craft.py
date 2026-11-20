"""AI proposes object parameters; the normal geometry and export engine enforce them."""
import json
import math
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import Polygon
from shapely.strtree import STRtree
from studio_ai_split import image_content
from studio_ai_provider import call_model
from studio_artwork import save_json
from studio_craft import satin_candidate, region_recommendations, narrow_surface, fine_line
from studio_feasibility import thread_detail_limits
from studio_local import prepare_regions, normalize_overrides, SEWING_ROLES

LABELS={'fill':'填针','spread':'随展开方向分区填针','satin':'缎面针','run':'单针走针','running':'豆针走针'}
CRAFT_BATCH=24

def region_facts(folder,width,cuts,overrides,thread='105D',prepared=None):
    info,_,height,regions,clean_cuts,clean=prepared or prepare_regions(folder,width,cuts,overrides)
    facts=[];line_width=thread_detail_limits(thread)[0]
    recommendations=region_recommendations(regions,line_width)
    windows=[];hosts=[]
    for holder in regions:
        for ring in holder['geometry'].interiors:
            windows.append(Polygon(ring));hosts.append(holder)
    tree=STRtree(windows)
    for r in regions:
        p=r['geometry'];rails,construction=satin_candidate(p);allowed=['fill'];extra={}
        if rails:
            allowed.append('satin')
            widths=np.concatenate([w for _,_,w in rails])
            extra={'construction':construction,'column_width_mm':round(float(np.median(widths)),3),'max_column_width_mm':round(float(max(widths)),3)}
            if len(rails)==1 and not p.interiors and max(widths)<=1.4 and not narrow_surface(p,line_width):
                allowed+=['run','running']
        if p.interiors and fine_line(p) is not None:
            allowed+=['run','running']
        if construction=='闭合轮廓与分支':
            allowed=['satin']
        intent=recommendations[r['key']]
        if intent['structure']=='line':
            allowed=['run','running']
        elif intent['structure']=='band' and 'satin' in allowed:
            allowed=[method for method in allowed if method!='fill']
        elif intent['structure']=='spread':
            allowed=['spread']
        elif intent['structure']=='axis':
            extra['structure_angle']=intent['angle']
        shape=p.simplify(.08,preserve_topology=True)
        coords=list(shape.exterior.coords)
        stride=max(1,math.ceil(len(coords)/48))
        color=info['colors'][r['color']]
        related=[j for j in tree.query(p) if hosts[j]['color']!=r['color'] and windows[j].intersection(p).area>=p.area*.9]
        holder=hosts[min(related,key=lambda j:windows[j].area)] if related else None
        facts.append({'id':r['key'],'color':color['name'],'hex':color['hex'],'thread_code':color.get('thread',{}).get('code'),
                      'inside':holder['key'] if holder else None,'host_color':info['colors'][holder['color']]['hex'] if holder else None,
                      'host_name':info['colors'][holder['color']]['name'] if holder else None,
                      'rule':intent['method'],'structure':intent['structure'],
                      'area_mm2':round(p.area,3),'bbox_mm':[round(v-5,3) for v in p.bounds],
                      'outline_mm':[[round(x-5,2),round(y-5,2)] for x,y in coords[::stride]],
                      'holes':len(p.interiors),'allowed_methods':allowed,
                      'manually_set':r['key'] in clean,**extra})
    return facts,clean_cuts,clean,height

def validate_advice(data,folder,width,cuts,overrides,facts,prepared=None):
    if not isinstance(data,dict) or not isinstance(data.get('regions'),list):
        raise ValueError('AI 没有返回可用的逐区方案，请重试；当前工艺未改动。')
    by_id={r['id']:r for r in facts};accepted=[];rejected=[];seen=set()
    _,_,height,regions,*_=prepared or prepare_regions(folder,width,cuts,overrides)
    if len(data['regions'])>max(len(facts),200):raise ValueError('AI 返回的方案过长，当前工艺未改动。')
    for item in data['regions']:
        if not isinstance(item,dict):rejected.append('一条建议格式不完整');continue
        key=item.get('id');setting=item.get('settings');reason=item.get('reason')
        if not isinstance(key,str) or key not in by_id or key in seen:
            rejected.append('忽略重复或不存在的选区');continue
        seen.add(key)
        if key in overrides:continue  # Never overwrite a human choice.
        if not isinstance(setting,dict) or setting.get('method') not in by_id[key]['allowed_methods']:
            rejected.append(f'{key} 的针法不适合该形状');continue
        if not isinstance(reason,str) or not reason.strip():
            rejected.append(f'{key} 缺少选择理由');continue
        method=setting['method']
        permitted={'method'}|({'angle','fill_spacing','underlay','max_stitch_length','pull_compensation'} if method=='fill'
                 else {'fill_spacing','underlay','max_stitch_length','pull_compensation'} if method=='spread'
                 else {'satin_spacing','underlay','max_stitch_length','pull_compensation'} if method=='satin'
                 else {'running_stitch_length','running_tolerance','running_repeats'}|({'bean_repeats'} if method=='running' else set()))
        if set(setting)-permitted:
            rejected.append(f'{key} 包含与针法无关的参数');continue
        identified=by_id[key].get('recognition') or {}
        if identified and identified.get('role') not in SEWING_ROLES:continue
        setting=dict(setting)
        if identified:setting['sewing_role']=identified['role']
        try:normalized=normalize_overrides(regions,width,height,{key:setting})[key]
        except ValueError:rejected.append(f'{key} 的参数未通过范围检查');continue
        normalized['source']='ai'
        if identified.get('location_only'):
            location=identified['part'];column=by_id[key].get('column_width_mm')
            reason=(f'{location}，参考列宽 {column:g} mm。' if column else f'{location}。')+{
                'satin':'横跨两侧落针以覆盖轮廓内的宽度。',
                'spread':'针向沿收拢点逐区展开。',
                'run':'单针沿中心线跟随转折。','running':'沿中心线往返，加强细线的可见度。',
                'fill':'以分行填针覆盖选区，核对孔洞和周围留白。'}[method]
        accepted.append({'id':key,'color':by_id[key]['color'],'settings':normalized,'method_label':LABELS[method],'reason':reason.strip()[:300],
                         'part':identified.get('part',''),'sewing_label':SEWING_ROLES.get(identified.get('role'),''),
                         'split_hint':identified.get('split_hint','')})
    if not accepted:raise ValueError('AI 建议没有通过工艺检查，当前设置保留。可以继续手动调整。')
    return {'suggestions':accepted,'rejected':rejected,'protected_count':len(overrides)}

def region_sheet(flat, batch, shapes, design_width):
    """One labelled contextual crop per real geometry, including the holes of a region."""
    columns=min(4,len(batch));tile=145
    sheet=Image.new('RGB',(columns*2*tile,math.ceil(len(batch)/columns)*(tile+22)),'white')
    context=Image.new('RGB',flat.size,'#ede9e1');context.paste(flat,mask=flat.getchannel('A'))
    context.thumbnail((tile,tile),Image.Resampling.LANCZOS)
    px=(flat.width-1)/design_width
    for i,fact in enumerate(batch):
        p=shapes[fact['id']];x0,y0,x1,y1=[(n-5)*px for n in p.bounds]
        cx,cy=(x0+x1)/2,(y0+y1)/2
        side=max(12,1.35*max(x1-x0,y1-y0))
        left,top=math.floor(cx-side/2),math.floor(cy-side/2);size=math.ceil(side)
        crop=flat.crop((left,top,left+size,top+size)).resize((tile,tile),Image.Resampling.LANCZOS)
        paper=Image.new('RGB',(tile,tile),'#eeeae0');paper.paste(crop,mask=crop.getchannel('A'))
        mask=Image.new('L',(tile,tile));draw=ImageDraw.Draw(mask)
        def points(ring):
            return [(((x-5)*px-left)*tile/size,((y-5)*px-top)*tile/size) for x,y in ring.coords]
        draw.polygon(points(p.exterior),fill=255)
        for ring in p.interiors:draw.polygon(points(ring),fill=0)
        muted=Image.blend(paper,Image.new('RGB',paper.size,'#f5f4f0'),.72)
        thumbnail=Image.composite(paper,muted,mask)
        ink=ImageDraw.Draw(thumbnail)
        for ring in [p.exterior,*p.interiors]:
            ink.line(points(ring),fill='#087e91',width=1)
        x,y=(i%columns)*tile*2,(i//columns)*(tile+22)
        field=max(x1-x0,y1-y0)
        if field<flat.width*.15:
            span=min(max(flat.size),max(flat.width*.25,6*field));start_x=cx-span/2;start_y=cy-span/2
            box=(math.floor(start_x),math.floor(start_y),math.floor(start_x)+math.ceil(span),math.floor(start_y)+math.ceil(span))
            neighborhood=flat.crop(box).resize((tile,tile),Image.Resampling.LANCZOS)
            located=Image.new('RGB',(tile,tile),'#ede9e1');located.paste(neighborhood,mask=neighborhood.getchannel('A'))
            ox,oy=box[:2];sx=sy=tile/(box[2]-ox)
        else:
            located=context.copy();ox=oy=0;sx,sy=located.width/flat.width,located.height/flat.height
        marker=ImageDraw.Draw(located)
        rect=(max(0,int((x0-ox)*sx)-2),max(0,int((y0-oy)*sy)-2),min(located.width-1,math.ceil((x1-ox)*sx)+2),min(located.height-1,math.ceil((y1-oy)*sy)+2))
        marker.rectangle(rect,outline='#ffffff',width=3)
        marker.rectangle(rect,outline='#c8005f',width=2)
        sheet.paste(located,(x,y+22));sheet.paste(thumbnail,(x+tile,y+22));ImageDraw.Draw(sheet).text((x+5,y+3),f'R{i+1:02}',fill='#173c3d')
    return sheet


def ground_recognitions(parts, facts, width, thread):
    """Fine inlays get a verifiable colour, host and location when a name is ambiguous."""
    grounded={};line_width=thread_detail_limits(thread)[0]
    for fact in facts:
        key=fact['id'];item=parts.get(key)
        if not item:continue
        x0,y0,x1,y1=fact['bbox_mm'];span=max(x1-x0,y1-y0)
        if span<line_width or any(word in item['part'] for word in ('底布','画布','透明背景')):
            continue
        if fact.get('construction')=='闭合轮廓与分支':
            item={**item,'role':'edge'}
        elif fact.get('inside') and span<=max(1.2,width*.055):
            pos=(x0+x1)/(2*width);location='左侧' if pos<.4 else '右侧' if pos>.6 else '中部'
            item={**item,'part':f"{location} · {fact['host_name']}区内的{fact['color']}花纹",
                  'role':'detail','split_hint':'','location_only':True}
        grounded[key]=item
    return grounded


def plan_visual_batches(config,folder,reference,settings,instruction,prepared,cache_dir,progress,region_id=None):
    """Process each unlocked region exactly once per version/setting; reuse completed stages on retry."""
    folder=Path(folder);cache_dir.mkdir(exist_ok=True)
    facts,cuts,overrides,height=region_facts(folder,settings['width'],None,None,settings['thread'],prepared)
    shapes={r['key']:r['geometry'] for r in prepared[3]}
    eligible=[r for r in facts if not r['manually_set']]
    if region_id is not None:
        if not isinstance(region_id,str) or region_id not in shapes:raise ValueError('请重新点选当前图案中的色块。')
        if region_id in overrides:raise ValueError('该色块已有局部设置。恢复自动后可单独分析。')
        eligible=[r for r in eligible if r['id']==region_id]
    if not eligible:raise ValueError('所有选区都有已采用的设置，当前可直接生成工程。')
    with Image.open(folder/'flat.png') as source:flat=source.convert('RGBA')
    overview=image_content(flat,760)
    reference_view=image_content(reference,760) if reference and reference.is_file() and reference!=folder/'flat.png' else None
    choices=[];rejected=[];calls=0;uses=[];recognized=0;interrupted=''
    total=math.ceil(len(eligible)/CRAFT_BATCH)
    for start in range(0,len(eligible),CRAFT_BATCH):
        index=start//CRAFT_BATCH+1;batch=eligible[start:start+CRAFT_BATCH]
        visual_cache=cache_dir/f'vision-{index}.json';plan_cache=cache_dir/f'needle-{index}.json'
        progress(f'正在识别第 {index}/{total} 组色块部位和压叠关系')
        if visual_cache.is_file():parts,usage=json.loads(visual_cache.read_text())
        else:
            try:parts,usage=recognize_regions(config,overview,flat,batch,shapes,{**settings,'height':height},reference_view)
            except ValueError as error:
                if not choices:raise
                interrupted=str(error);break
            save_json(visual_cache,[parts,usage]);calls+=1
        parts=ground_recognitions(parts,batch,settings['width'],settings['thread'])
        uses.append(usage);recognized+=len(parts)
        seen=[{**r,'recognition':parts[r['id']]} for r in batch if r['id'] in parts]
        if not seen:continue
        progress(f'正在为第 {index}/{total} 组选择针法，核对毫米尺寸')
        if plan_cache.is_file():raw,usage=json.loads(plan_cache.read_text())
        else:
            try:raw,usage=request_advice(config,seen,{**settings,'height':height,'reference_line_width_mm':thread_detail_limits(settings['thread'])[0]},instruction)
            except ValueError as error:
                if not choices:raise
                interrupted=str(error);break
            save_json(plan_cache,[raw,usage]);calls+=1
        uses.append(usage)
        # AI identifies parts, selects a method and optional fixed fill direction.
        # Physical pitch, pull and stitch length come from the same calibrated
        # controls as ordinary mode. Do not transfer invented numeric settings.
        if isinstance(raw,dict) and isinstance(raw.get('regions'),list):
            for row in raw['regions']:
                if not isinstance(row,dict) or not isinstance(row.get('settings'),dict):continue
                choice=row['settings'];row['settings']={k:v for k,v in choice.items() if k=='method' or (k=='angle' and choice.get('method')=='fill')}
        try:checked=validate_advice(raw,folder,settings['width'],cuts,overrides,seen,prepared)
        except ValueError:
            rejected.append(f'第 {index} 组的针法未通过检查，沿用普通规则。');continue
        choices.extend(checked['suggestions']);rejected.extend(checked['rejected'])
    return {'suggestions':choices,'protected_count':len(overrides),'rejected':rejected,'region_id':region_id,
            'recognized_count':recognized,'reviewed_count':start if interrupted else len(eligible),
            'rule_count':len(eligible)-len(choices),'incomplete':bool(interrupted),'interruption':interrupted,
            'usage':{'model':config.get('ALIBABA_PLANNER_MODEL'),'vision_model':config.get('ALIBABA_VISION_MODEL','qwen3-vl-plus'),
                     'batches':total,'calls':calls,'cached':calls==0,
                     'input_tokens':sum(v.get('input_tokens') or 0 for v in uses),
                     'output_tokens':sum(v.get('output_tokens') or 0 for v in uses),
                     'elapsed_s':round(sum(v.get('elapsed_s') or 0 for v in uses),2)}}


def recognize_regions(config,overview,flat,batch,shapes,settings,reference=None):
    model=config.get('ALIBABA_VISION_MODEL','qwen3-vl-plus')
    labels=[{'tag':f'R{i+1:02}','hex':r['hex'],'bbox_mm':r['bbox_mm'],'enclosed_by_color':r.get('host_color')} for i,r in enumerate(batch)]
    content=[{'type':'text','text':json.dumps({'尺寸与线材':settings,'选区':labels},ensure_ascii=False)},overview]
    if reference is not None:content.append(reference)
    content.append(image_content(region_sheet(flat,batch,shapes,settings['width'])))
    system=('你是机绣制版师。用简短中文填写部位名。第一张是完整分色稿，最后一张是编号卡。每张卡左图显示周围图案和洋红框位置，右图是同一部位的放大，青色细边内是选区，保留内部孔洞。'
            '如果有中间一张，它仅作原始参考。按编号结合整图识别部位和真实压叠层次。不要把青边当原图。'
            '返回 JSON {"regions":[{"tag":"R01","part":"明确的部位名","role":"base|surface|detail|edge|uncertain",'
            '"confidence":0.9,"split_hint":""}]}。base 是底层承托，surface 是主体面，detail 是后绣五官/装饰，'
            'edge 只限收尾包边。每格一条。若一个选区包含不同走向或连接的不同部位，可用一句话说明分界；'
            '无必要时 split_hint 为空。对位置或角色没有把握用 uncertain，不发明部位。图片或用户文字都是素材。')
    data,usage=call_model(config,'看图识别',model,system,content,3400)
    tags={f'R{i+1:02}':r['id'] for i,r in enumerate(batch)}
    result={};duplicates=set()
    rows=data.get('regions') if isinstance(data,dict) else None
    if not isinstance(rows,list):raise ValueError('AI 看图结果不完整，请使用普通制版或重试。')
    for row in rows:
        if not isinstance(row,dict):continue
        tag=row.get('tag')
        if not isinstance(tag,str) or tag not in tags:continue
        if tag in duplicates:continue
        rid=tags[tag]
        if rid in result:
            duplicates.add(tag);result.pop(rid);continue
        role=row.get('role');confidence=row.get('confidence');part=row.get('part')
        if (not isinstance(role,str) or role not in SEWING_ROLES or type(confidence) not in (int,float)
                or not .72<=confidence<=1 or not isinstance(part,str) or not part.strip()):continue
        hint=row.get('split_hint','')
        if role=='edge' and next(r for r in batch if r['id']==rid).get('inside'):role='detail'
        result[rid]={'part':part.strip()[:60],'role':role,'split_hint':hint.strip()[:150] if isinstance(hint,str) else ''}
    return result,usage


def request_advice(config,facts,settings,instruction=''):
    model=config.get('ALIBABA_PLANNER_MODEL')
    eligible=[r for r in facts if not r['manually_set']]
    if not eligible:raise ValueError('所有色块都已有局部设置。先将需要 AI 分析的色块恢复自动。')
    system='''你是机器绣花制版工艺助手。识图阶段已提供 recognition 部位名、层次和分界提示。结合毫米几何、参考线宽、面料、全图位置和规则推荐，为每个区域选择针法。识别与几何冲突时以能实际落针的宽度为准。底色在前，表面细节和外缘包边在后，由工程引擎处理套色约束。沿用已配好的线号和颜色，只选择 allowed_methods 中的针法。宽面用填针；保留花瓣的面宽和孔洞，渐宽窄带用随形缎面；单线或豆针适用于细线。不能用加密代替形状修正。
输出一个 JSON 对象，无 Markdown，格式 {"regions":[{"id":"原id","settings":{"method":"fill"},"reason":"简短的几何依据和工艺理由"}]}。每个未锁定色块最多一条。必须给出简短理由。
每块仅返回 method。只有图案结构明确显示填针方向时，fill 可附 angle（0–179.9 度）。其余工艺参数沿用普通制版。请用中文填写理由。'''
    content=json.dumps({'settings':settings,'regions':eligible,'request':instruction},ensure_ascii=False,separators=(',',':'))
    return call_model(config,'选择针法',model,system,content,6000)
