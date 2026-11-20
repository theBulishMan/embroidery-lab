"""Version-bound, deterministic local craft settings and non-destructive cuts."""
import math
import re
import hashlib
import numpy as np
from shapely.geometry import LineString
from shapely.ops import split,unary_union
from studio_artwork import extract_geometries, poly_path
from studio_craft import fill_angle,region_recommendations
from studio_structure import converging_surface
from studio_feasibility import detail_metrics, thread_detail_limits

REGION_ID=re.compile(r'^c\d+-(?:r\d+-s\d+|m[0-9a-f]{16})(?:-[a-d]){0,64}$')
METHODS={'auto','fill','guided','spread','satin','run','running'}
SEWING_ROLES={'base':'底色','surface':'主体','detail':'表面细节','edge':'收边'}
LIMITS={'angle':(0,179.9),'fill_spacing':(.15,.60),'satin_spacing':(.18,.80),
        'pull_compensation':(0,.4),'max_stitch_length':(1,5.8),
        'running_stitch_length':(.4,4),'running_tolerance':(.01,.3),
        'running_repeats':(1,5),'bean_repeats':(1,3)}
RUN_FIELDS={'running_stitch_length','running_tolerance','running_repeats','bean_repeats'}

def number(value,low,high,label):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
        raise ValueError(f'{label}必须在 {low}–{high} 之间。')
    return round(float(value),6)

def points(value,count):
    if not isinstance(value,list) or len(value)!=count:raise ValueError(f'请在图案内选择 {count} 个控制点。')
    result=[]
    for p in value:
        if not isinstance(p,list) or len(p)!=2:raise ValueError('方向线的控制点无效。')
        result.append([number(x,0,1,'控制点') for x in p])
    return result

def physical(points,width,height):return [[5+x*width,5+y*height] for x,y in points]

def guide_coordinates(guide,width,height):
    a,m,b=np.asarray(physical(guide,width,height));control=2*m-(a+b)/2
    t=np.linspace(0,1,65)[:,None]
    return ((1-t)**2*a+2*(1-t)*t*control+t*t*b).tolist()

def prepare_regions(folder,width,cuts=None,overrides=None):
    info,geo,height,identities=extract_geometries(folder,width,with_ids=True)
    regions=[{'key':identities[ci][n],'base_key':identities[ci][n],'color':ci,'source_index':n,'geometry':p}
             for ci,group in enumerate(geo) for n,p in enumerate(group)]
    if cuts is None:cuts=[]
    if not isinstance(cuts,list) or len(cuts)>64:raise ValueError('一份方案最多拆分或合并 64 次。')
    clean_cuts=[]
    for cut in cuts:
        # The legacy region_cuts field is an ordered edit log. Keeping merge
        # operations in this same log permits merge -> cut -> merge, including
        # old projects and AI cuts, without losing edit order or stable IDs.
        if isinstance(cut,dict) and 'merge_regions' in cut:
            keys=cut['merge_regions']
            if set(cut)!={'merge_regions'} or not isinstance(keys,list) or not 2<=len(keys)<=32 or any(not isinstance(k,str) for k in keys) or len(set(keys))!=len(keys):
                raise ValueError('请选择 2–32 个不同的相邻同色区域合并。')
            keys=sorted(keys);chosen=[r for r in regions if r['key'] in keys]
            if len(chosen)!=len(keys):raise ValueError('合并的区域已改变，请重新选择。')
            if len({r['color'] for r in chosen})!=1:raise ValueError('只能合并同色区域；不同颜色需要保留独立线色。')
            joined=unary_union([r['geometry'] for r in chosen])
            if joined.geom_type!='Polygon' or not joined.is_valid:
                raise ValueError('这些区域不相邻，不能合成一个连续色块；不会自动填补中间空隙。')
            key=f'c{chosen[0]["color"]+1}-m'+hashlib.sha256(','.join(keys).encode()).hexdigest()[:16]
            merged={**chosen[0],'key':key,'base_key':key,'geometry':joined,'merged_from':keys}
            merged.pop('parent_key',None)
            index=next(i for i,r in enumerate(regions) if r['key'] in keys)
            regions=[r for r in regions if r['key'] not in keys];regions.insert(index,merged)
            clean_cuts.append({'merge_regions':keys});continue
        if not isinstance(cut,dict) or set(cut)-{'region_id','points','finite'} or not {'region_id','points'}<=set(cut):raise ValueError('分区设置无效。')
        finite=cut.get('finite',False)
        if not isinstance(finite,bool):raise ValueError('分区线方式无效。')
        key=cut['region_id'];index=next((i for i,r in enumerate(regions) if r['key']==key),None)
        if index is None:raise ValueError('分区的区域已改变，请重新选择。')
        dots=points(cut['points'],2);a,b=np.asarray(physical(dots,width,height));v=b-a;length=np.linalg.norm(v)
        if length<.5:raise ValueError('分区线太短，请拉开两个点。')
        item=regions[index];p=item['geometry'];v/=length;span=.04 if finite else max(width,height)*3
        pieces=[q for q in split(p,LineString([a-v*span,b+v*span])).geoms if q.geom_type=='Polygon']
        if not 2<=len(pieces)<=4:raise ValueError('这条线没有有效分开选区，请让它穿过色块。')
        if any(q.area<.15 or 2*q.area/max(q.length,.01)<.25 for q in pieces):
            raise ValueError('分区产生了过小或过窄的碎块，请移动分区线。')
        pieces.sort(key=lambda q:(round(q.centroid.y,4),round(q.centroid.x,4)))
        children=[{**item,'key':key+'-'+chr(97+i),'parent_key':key,'geometry':q} for i,q in enumerate(pieces)]
        regions[index:index+1]=children;clean_cut={'region_id':key,'points':dots}
        if finite:clean_cut['finite']=True
        clean_cuts.append(clean_cut)
    clean=normalize_overrides(regions,width,height,overrides)
    return info,geo,height,regions,clean_cuts,clean

def normalize_overrides(regions,width,height,overrides):
    if overrides is None:overrides={}
    if not isinstance(overrides,dict) or len(overrides)>len(regions):raise ValueError('局部工艺设置无效。')
    by_key={r['key']:r for r in regions};clean={}
    for key,value in overrides.items():
        if not isinstance(key,str) or not REGION_ID.fullmatch(key) or key not in by_key:
            raise ValueError('局部设置对应的区域已改变，请重新选择区域。')
        if not isinstance(value,dict) or set(value)-({'method','underlay','guide','sewing_role','source'}|set(LIMITS)):
            raise ValueError('局部工艺包含不支持的设置。')
        setting={};method=value.get('method','auto')
        if not isinstance(method,str) or method not in METHODS:raise ValueError('请选择列表中的局部针法。')
        if method=='spread' and not converging_surface(by_key[key]['geometry']):
            raise ValueError('这块没有可确定的收拢点和展开边，可画方向曲线指定针向。')
        if method!='auto':setting['method']=method
        if 'sewing_role' in value:
            if not isinstance(value['sewing_role'],str) or value['sewing_role'] not in SEWING_ROLES:raise ValueError('色块绣制层次无效。')
            setting['sewing_role']=value['sewing_role']
        if 'source' in value:
            if value['source']!='ai':raise ValueError('局部工艺来源无效。')
            setting['source']='ai'
        underlay=value.get('underlay','auto')
        if not isinstance(underlay,str) or underlay not in {'auto','none','light','full'}:raise ValueError('底针方式无效。')
        if underlay!='auto':setting['underlay']=underlay
        for field,(lo,hi) in LIMITS.items():
            if field in value:setting[field]=number(value[field],lo,hi,field)
        for field in RUN_FIELDS & setting.keys():
            if method not in {'run','running'}:raise ValueError('走针参数只能用于单针或豆针。')
        for field in {'running_repeats','bean_repeats'} & setting.keys():
            if not setting[field].is_integer():raise ValueError('往返次数必须是整数。')
            setting[field]=int(setting[field])
        if method=='run' and 'bean_repeats' in setting:raise ValueError('单针不使用豆针回针。')
        if 'angle' in setting and method!='fill':raise ValueError('固定针向用于填针，请先选择填针。')
        if 'guide' in value:
            if method!='guided':raise ValueError('方向曲线只能用于随曲线填针。')
            setting['guide']=points(value['guide'],3)
        if method=='guided':
            if 'guide' not in setting:raise ValueError('请先画出方向曲线。')
            line=LineString(guide_coordinates(setting['guide'],width,height));p=by_key[key]['geometry']
            ends=math.dist(line.coords[0],line.coords[-1])
            if ends<1 or line.length>ends*3 or not line.is_simple or line.intersection(p).length<min(1,p.length*.1):
                raise ValueError('方向曲线需要穿过选区，并避免回折或过急的弯曲。')
        if method in {'run','running'} and underlay not in {'auto','none'}:raise ValueError('细线走针不使用面状底针，请选择自动或无底针。')
        if setting:clean[key]=setting
    return clean

def catalog(folder,width,cuts=None,thread='105D'):
    info,_,height,regions,clean,_=prepare_regions(folder,width,cuts)
    line_width=thread_detail_limits(thread)[0]
    recommendations=region_recommendations(regions,line_width)
    return {'width':width,'height':height,'thread':thread,'colors':info['colors'],'region_cuts':clean,
            'regions':[{'id':r['key'],'base_key':r['base_key'],'parent_key':r.get('parent_key'),'merged_from':r.get('merged_from',[]),
                        'color':r['color'],'path':poly_path(r['geometry']),
                        'area_mm2':round(r['geometry'].area,3),'bbox':list(r['geometry'].bounds),
                        'default_angle':fill_angle(r['geometry']),
                        'detail_metrics':detail_metrics(r['geometry']),
                        'recommendation':recommendations[r['key']]} for r in regions]}

def merge_equivalent_regions(regions,overrides):
    """Remove artificial seams only when explicitly chosen craft is identical.

    Editable regions remain separate. Auto directions depend on each shape,
    so two automatic regions are deliberately not assumed equivalent.
    """
    result=[]
    for original in regions:
        item={**original,'region_keys':[original['key']]}
        setting=overrides.get(item['key'],{})
        insert_index=len(result)
        if setting.get('method')=='fill' and 'angle' in setting:
            while True:
                match=None
                for i,previous in enumerate(result):
                    if previous['base_key']!=item['base_key'] or overrides.get(previous['key'],{})!=setting:continue
                    a,b=previous['geometry'],item['geometry']
                    if a.boundary.intersection(b.boundary).length<.1:continue
                    joined=a.union(b)
                    if joined.geom_type!='Polygon' or not joined.is_valid:continue
                    match=i;item={**previous,'geometry':joined,'region_keys':previous['region_keys']+item['region_keys']};break
                if match is None:break
                insert_index=min(insert_index,match);result.pop(match)
        result.insert(insert_index,item)
    return result
