"""Generic color regions -> editable Ink/Stitch project -> verified commands."""
from pathlib import Path
import json,math,csv,subprocess,zipfile,collections,copy,itertools,time,re
import numpy as np
from PIL import Image,ImageDraw
from shapely.geometry import Polygon,LineString,box
from shapely.ops import unary_union,nearest_points
from shapely.strtree import STRtree
from lxml import etree as ET
import pyembroidery as pe
from studio_artwork import APP,SVG,INK,STITCH,artwork_canvas,poly_path,svg_root,write_svg,save_json
from studio_craft import FABRICS,fill_angle,narrow_rails,curved_rails,rail_quality,ring_rails,frame_rails,rail_coverage,satin_underlay,satin_short_distance,outer_borders,covered_stitch_region
from studio_routing import connection_candidate, order_visible_layers
from studio_local import prepare_regions,guide_coordinates,merge_equivalent_regions,SEWING_ROLES
from studio_craft import satin_candidate, fine_line, inset_details, machine_fill_geometry, narrow_surface, directional_fill_margin, inlaid_satin_band, protect_fill_recesses, supported_fill_edge, branched_ring_rails, one_sided_satin_rails
from studio_craft import small_round_spot, split_exposed_bands, restore_inlay_underlap, registered_tatami
from studio_feasibility import detail_metrics, fill_edge_allowance, thread_detail_limits
from studio_quality import review_needle_geometry
from studio_craft import round_group_directions, protect_cloth_openings, recommend_stitch, region_recommendations, stroke_center
from studio_structure import converging_surface, spreading_sections
from studio_engine_process import run_engine, EngineResourceError
from studio_clearance import StrokeGaps
from studio_runtime import ENGINE

VERSION='generic-flat-v32-registered-fill'

# Fill spacing is between ADJACENT rows. Satin spacing is peak-to-peak.
# These starting presets require a sew-out with the actual thread and fabric.
PRESETS={'75D':{'normal':(.20,.35),'dense':(.18,.32),'detail':(.20,.20)},
         '105D':{'normal':(.22,.40),'dense':(.20,.36),'detail':(.22,.26)}}

def preview_thread_width(thread):
    # Illustrative width only, independent of density; not a D-to-mm conversion.
    return thread_detail_limits(thread)[0]

def param(el,key,val):el.set(f'{{{STITCH}}}{key}',str(val).lower() if isinstance(val,bool) else str(val))

def set_export_origin(root,x,y):
    # A fixed origin keeps the read-back machine coordinates registered to the
    # artwork. Normalising each export to its bounds loses pull-compensation
    # offsets and can turn a registration error into a false coverage warning.
    defs=ET.SubElement(root,f'{{{SVG}}}defs')
    symbol=ET.SubElement(defs,f'{{{SVG}}}symbol',id='inkstitch_origin')
    ET.SubElement(symbol,f'{{{SVG}}}path',d='M-1,0 H1 M0,-1 V1',fill='none',stroke='#777',**{'stroke-width':'.1'})
    marker=ET.SubElement(root,f'{{{SVG}}}use',id='export-origin',x=str(x),y=str(y))
    marker.set('{http://www.w3.org/1999/xlink}href','#inkstitch_origin')
    marker.set(f'{{{INK}}}label','机器文件原点 · 图案中心')

def movement_metrics(pattern):
    """Measure exported moves, excluding the machine's unknown initial position."""
    travel=sewing=0.;last=None;started=False
    for x,y,raw in pattern.stitches:
        cmd=raw&255
        if started and last is not None:
            distance=math.hypot(x-last[0],y-last[1])/10
            if cmd==pe.JUMP:travel+=distance
            elif cmd==pe.STITCH:sewing+=distance
        if cmd==pe.STITCH:started=True
        last=(x,y)
    return {'travel_mm':round(travel,2),'sewing_mm':round(sewing,2)}

def nearby_order(bundles,centers=False):
    """Keep colors, overlapping objects and outline-last constraints intact.

    This is a candidate object order, not a stitch reordering. The engine still
    plans each object's underlay, surface, lock stitches and entry/exit points.
    Exported travel must improve before the candidate can replace the baseline.
    """
    result=[];cursor=None
    for _,items in itertools.groupby(bundles,key=lambda b:(b['color'],b.get('stage','body'),b.get('sewing_role'))):
        group=list(items)
        remaining=list(range(len(group)));done=set()
        dependencies={i:{j for j in range(i) if
            group[j]['geometry'].distance(group[i]['geometry'])<.3 or
            (group[i]['outline'] and not group[j]['outline'])}
            for i in remaining}
        while remaining:
            available=[i for i in remaining if dependencies[i]<=done]
            chosen=available[0] if cursor is None else min(available,key=lambda i:(cursor.distance(group[i]['geometry'].representative_point() if centers else group[i]['geometry']),i))
            item=group[chosen];result.append(item);done.add(chosen);remaining.remove(chosen)
            # Estimate the next entry from the previous boundary. Actual entry
            # and exit are recomputed by Ink/Stitch after the order is applied.
            cursor=item['geometry'].representative_point() if cursor is None or centers else nearest_points(cursor,item['geometry'])[1]
    return result

def export_engine(root,out,update=None):
    out.mkdir(parents=True,exist_ok=True)
    ET.ElementTree(root).write(str(out/'embroidery.svg'),encoding='utf-8',xml_declaration=True)
    cmd=[str(ENGINE),'--extension=zip','--format-dst=True','--format-pes=True','--format-threadlist=True','--custom-file-name=design',str(out/'embroidery.svg')]
    with (out/'engine.zip').open('wb') as output,(out/'engine.log').open('w') as err:
        try:process=run_engine(cmd,stdout=output,stderr=err,folder=out,timeout=300,update=update)
        except subprocess.TimeoutExpired:
            raise ValueError('复杂细节排针超过5分钟，工程未完成。请先核对细小碎块、分区或尺寸；现有图案保留。')
    if process.returncode or not zipfile.is_zipfile(out/'engine.zip'):raise ValueError('这张稿的部分细节暂时无法自动排针，请简化小细节或调整尺寸。')
    with zipfile.ZipFile(out/'engine.zip') as z:
        for name in z.namelist():
            if Path(name).name!=name:raise ValueError('导出文件路径异常。')
            (out/name).write_bytes(z.read(name))
    dst=pe.read(str(out/'design.dst'));pes=pe.read(str(out/'design.pes'))
    def points(pattern):return np.array([[x,y] for x,y,c in pattern.stitches if c&255==pe.STITCH])
    a,b=points(dst),points(pes)
    if not len(a) or a.shape!=b.shape or not np.allclose(a-a.min(0),b-b.min(0),atol=.001):raise ValueError('机器文件核对未通过，请调整参数重试。')
    # Also compare color-block needle counts. Equal geometry alone would not
    # detect a color change moved to an incorrect needle position.
    def blocks(pattern):
        counts=[0]
        for _,_,raw in pattern.stitches:
            if raw&255==pe.COLOR_CHANGE:counts.append(0)
            elif raw&255==pe.STITCH:counts[-1]+=1
        return counts
    if blocks(dst)!=blocks(pes):raise ValueError('机器文件换色位置核对未通过。')
    previous=None
    for x,y,raw in dst.stitches:
        if raw&255==pe.STITCH and previous is not None and math.hypot(x-previous[0],y-previous[1])>61:
            raise ValueError('存在过长的落针，请调整版面后重新排针。')
        previous=(x,y)
    return dst,pes

def verified_passes(root, threadlist, colors, region_areas):
    """Use the engine's actual thread list; a sub-resolution pass can emit no needles."""
    groups=[]
    for node in root:
        ci=node.get('data-thread-color')
        if ci is None:continue
        ci=int(ci)
        if not groups or groups[-1]['color']!=ci:
            groups.append({'color':ci,'stages':[],'roles':[],'keys':set()})
        g=groups[-1]
        for field,value in [('stages',node.get('data-stage','body')),('roles',node.get('data-sewing-role'))]:
            if value and value not in g[field]:g[field].append(value)
        g['keys'].update(filter(None,(node.get('data-region-keys') or node.get('data-region-key','')).split(',')))
    emitted=re.findall(r'^\s*\d+\s+[^\n]*\((#[\da-fA-F]{6})\)\s*$',threadlist,re.M)
    emitted=[value.lower() for value in emitted]
    result=[];omitted=[]
    for g in groups:
        ci=g['color'];hex_value=colors[ci]['hex'].lower()
        if len(result)<len(emitted) and emitted[len(result)]==hex_value:
            result.append(g)
        elif result and result[-1]['color']==ci:
            prior=result[-1]
            for field in ('stages','roles'):prior[field].extend(v for v in g[field] if v not in prior[field])
            prior['keys'].update(g['keys'])
        elif g['keys'] and all(region_areas.get(k,math.inf)<.15 for k in g['keys']):
            omitted.extend(sorted(g['keys']))
        else:
            raise ValueError('工程配线单与实际绣序不符，请检查选区和线色。')
    if len(result)!=len(emitted) or not result:raise ValueError('工程配线单未能核对实际绣序。')
    return result,omitted


def make_plan(version_folder,out,width,thread,spacing,update=lambda x:None,*,satin_spacing=None,fabric='twill',region_cuts=None,object_overrides=None,adaptive_fill=False):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    update('正在读取色块并分析细节尺寸')
    profile=FABRICS[fabric];inset_mm=profile['inset']
    satin_spacing=PRESETS[thread]['normal'][1] if satin_spacing is None else satin_spacing
    underlay_spacing=round(max(.6,spacing*3),3)
    info,geo,height,regions,region_cuts,object_overrides=prepare_regions(version_folder,width,region_cuts,object_overrides)
    objects=[{'id':r['key'],'region_key':r['key'],'color':r['color'],'path':poly_path(r['geometry']),'area_mm2':round(r['geometry'].area,3),'bbox':list(r['geometry'].bounds)} for r in regions]
    view=artwork_canvas(info,width,height,thread,spacing,objects)
    view['meta'].update(engine_version=VERSION,satin_spacing_mm=satin_spacing,adaptive_fill=adaptive_fill,
                        fabric=fabric,fabric_name=profile['name'],
                        underlay_row_spacing_mm=underlay_spacing,
                        preview_thread_width_mm=preview_thread_width(thread))
    view['meta'].update(region_cuts=region_cuts,object_overrides=object_overrides)
    sewing_regions=merge_equivalent_regions(regions,object_overrides)
    if any(not group for group in geo):raise ValueError('部分色块太小，请增大尺寸或先简化细节。')
    root=svg_root(view['width'],view['height']);planned=[];warnings=[];bundles=[];object_shapes={};local={}
    origin=[5+width/2,5+height/2];set_export_origin(root,*origin)
    view['meta'].update(command_registration='explicit-design-center',export_origin_mm=origin)
    rail=lambda coords:'M'+' L'.join(f'{x:.3f},{y:.3f}' for x,y in coords)
    line_width_mm=view['meta']['preview_thread_width_mm']
    visible_intents=region_recommendations(regions,line_width_mm)
    for region,obj in zip(regions,view['meta']['separated_objects']):
        shape=region['geometry']
        intent=visible_intents[region['key']]
        obj['structure']=intent['structure']
        if intent['structure']=='line':
            center=stroke_center(shape)
            if center is not None:obj['center_path']=rail(center.coords)
    def running(coords,ci,pid,role,bean=False,trim=False):
        detail=role=='细线走针'
        length=local.get('running_stitch_length',1.6 if bean else 2) if detail else 2
        tolerance=local.get('running_tolerance',.08) if detail else .08
        repeats=local.get('running_repeats',1) if detail else 1
        bean_count=local.get('bean_repeats',1) if bean else 0
        el=ET.SubElement(root,f'{{{SVG}}}path',id=pid,d=rail(coords),fill='none',stroke=info['colors'][ci]['hex'],
                         **{'stroke-width':'.15','stroke-dasharray':'1 1','data-thread-color':str(ci),'data-role':role})
        el.set(f'{{{INK}}}label',f'{info["colors"][ci]["name"]} · {role}')
        for k,v in {'stroke_method':'running_stitch','running_stitch_length_mm':length,
                    'running_stitch_tolerance_mm':tolerance,'repeats':repeats,'bean_stitch_repeats':bean_count,'trim_after':trim}.items():param(el,k,v)
        kind=('三重走针' if bean_count==1 else '豆针走针') if bean else ('单针走针' if detail else '轮廓底针')
        planned.append({'id':pid,'color':ci,'stitch_type':kind,'role':role,'underlay':not detail,
                        'running_stitch_length_mm':length,'running_stitch_tolerance_mm':tolerance,'repeats':repeats,'bean_stitch_repeats':bean_count})
        return el
    def fill(p,ci,pid,underlay=False,angle=None,visible=None,spread_pass=None,lock_direction=False):
        angle=local.get('angle',fill_angle(p) if angle is None else angle)
        row=local.get('fill_spacing',spacing);bottom=local.get('underlay','auto')
        if spread_pass:
            bottom='none'
            if spread_pass=='support':row=max(.8,row*4)
            elif (adaptive_fill and fabric=='twill' and 'fill_spacing' not in local
                  and row==PRESETS[thread]['normal'][0] and angle%90!=0):
                row=round(row-.02,2)
        # A tenth-millimetre machine grid can open gaps between oblique rows,
        # or between horizontal rows whose pitch is not a whole grid step.
        # Reserve 0.02 mm only on broad, automatic surfaces at regular density.
        broad_auto = not spread_pass and adaptive_fill and fabric=='twill' and not local and row==PRESETS[thread]['normal'][0]
        guarded = False
        aligned_base = False
        if broad_auto and visible is not None:
            target = protect_fill_recesses(p, visible, preview_thread_width(thread))
            guarded = target is not p
            p = target
            aligned_base = bool(angle%90!=0 and visible.interiors and visible.area >= width * height * .16
                                and 2 * visible.area / visible.length >= 2)
            # A wide concave face in free fill has no mandated direction. Parallel
            # rows on a machine axis avoid periodic gaps from 0.1 mm quantization.
            loose_curve = (angle%90 != 0 and not visible.interiors and visible.area >= 18
                           and visible.area < .78*visible.convex_hull.area and 2*visible.area/visible.length >= 1.0)
            if not lock_direction and (guarded or aligned_base or loose_curve):
                angle = 0 if not loose_curve else (round(angle/90)*90)%180
            elif not lock_direction and region['key'] in grouped_angles:
                angle = grouped_angles[region['key']]
        if broad_auto and p.area>=4 and 2*p.area/max(p.length,.01)>=1.0:
            if guarded or aligned_base or angle%90!=0 or abs(row*10-round(row*10))>.01:
                # Reserve one extra hundredth on a compact, rounded shape in free-fill mode.
                # The 0.1 mm export grid displaces endpoints at varying row angles.
                bounds=p.bounds; axes=(bounds[2]-bounds[0],bounds[3]-bounds[1])
                compact=(thread=='75D' and angle%90!=0 and p.area>=10 and min(axes)>.7*max(axes)
                         and len(p.interiors)<=1 and p.area>=.82*p.convex_hull.area)
                row=round(row-(.03 if compact else .02),2)
        stitch_length = local.get('max_stitch_length', 3.2)
        staggers = 4
        grid = None
        if (broad_auto and not lock_direction and visible is not None and visible.area >= 12
                and 2 * visible.area / visible.length >= 1):
            grid = registered_tatami(angle, row, stitch_length)
            if grid:
                angle, row, stitch_length, staggers = grid
        edge_allowance=fill_edge_allowance(p,thread,profile['pull_fill'] if underlay else profile['pull_fill']/2,local.get('pull_compensation'))
        if spread_pass:
            # The sections already meet along their exact shared edges. Only the
            # external outline may be expanded (once, before partitioning).
            edge_allowance.update(expansion_mm=0, pull_compensation_mm=0)
        if guarded:
            edge_allowance.update(expansion_mm=0, pull_compensation_mm=0, recess_protected=True)
        p=machine_fill_geometry(p,view['meta']['preview_thread_width_mm'],row)
        cross=underlay if bottom=='auto' else bottom=='full'
        edge=underlay if bottom=='auto' else bottom in ['light','full']
        contour=False
        if edge:
            inset=p.buffer(-inset_mm)
            if inset.geom_type=='Polygon' and inset.area>1:
                running(list(inset.exterior.coords),ci,pid+'-underlay','轮廓底针');contour=True
        parent=root;guided=local.get('method')=='guided'
        if guided:
            parent=ET.SubElement(root,f'{{{SVG}}}g',id=pid+'-guided',**{'data-thread-color':str(ci)})
            coords=guide_coordinates(local['guide'],width,height)
            guide=ET.SubElement(parent,f'{{{SVG}}}path',id=pid+'-direction',d=rail(coords),fill='none',stroke='#287f9e',style='marker-start:url(#inkstitch-guide-line-marker)',**{'stroke-width':'.2'})
            guide.set(f'{{{INK}}}label','方向曲线（不刺绣）')
            if not root.xpath('//*[@id="inkstitch-guide-line-marker"]'):
                defs=root.find(f'{{{SVG}}}defs')
                if defs is None:defs=ET.SubElement(root,f'{{{SVG}}}defs')
                marker=ET.SubElement(defs,f'{{{SVG}}}marker',id='inkstitch-guide-line-marker',markerWidth='3',markerHeight='3',refX='1',refY='1')
                ET.SubElement(marker,f'{{{SVG}}}circle',cx='1',cy='1',r='.7',fill='#287f9e')
            object_shapes[pid+'-guided']=p
        # A single wide opening tolerates native pull. Protect narrow inlays
        # and perforated motifs from the engine's row-to-polygon smoothing.
        delicate_holes=len(p.interiors)>1 or any(min(h.bounds[2]-h.bounds[0],h.bounds[3]-h.bounds[1])<2 for h in p.interiors)
        vector_margin = not guided and 'pull_compensation' not in local and (
            (adaptive_fill and fabric=='twill') or delicate_holes or edge_allowance['detail_edge_protection'])
        if vector_margin:
            needle_shape = directional_fill_margin(p, angle, edge_allowance['pull_compensation_mm'])
            applied = edge_allowance['pull_compensation_mm'] if needle_shape is not p else 0
            edge_allowance.update(vector_pull_mm=applied, pull_compensation_mm=0)
            p = needle_shape
        if broad_auto and visible is not None and index:
            near = tree.query(visible, predicate='dwithin', distance=line_width_mm)
            earlier = [objects[j][0]['geometry'] for j in near if j < index and object_colors[j] != ci]
            needle_shape = supported_fill_edge(p, visible, earlier, line_width_mm)
            if needle_shape is not p:
                p = needle_shape
                edge_allowance.update(expansion_mm=0, supported_edge=True)
        if broad_auto and visible is not None and visible.interiors:
            clipped = protect_cloth_openings(p, visible, objects, index, tree, line_width_mm,
                                            edge_allowance['expansion_mm'])
            if clipped is not p:
                p = clipped
                edge_allowance['cloth_openings_protected'] = True
        if broad_auto:
            # Multiple margins (pixel contour, directional pull, engine expansion and
            # 0.1-mm export grid) can add up at the four physical size limits. Keep
            # the fill's editable vector within its remaining allowance; internal
            # colour joins and manual compensation keep their own geometry.
            free=max(0,profile['pull_fill']-edge_allowance['expansion_mm'])
            # A dominant exposed surface reaches the requested silhouette on
            # several sides. Reserve the export grid at those limits instead
            # of adding a second outward margin at an already compensated edge.
            large_shell=(visible is not None and angle%90!=0 and not visible.interiors
                         and visible.area>=.20*width*height and near_cloth(visible))
            if large_shell:free=0
            limit=(5-free,5-free,5+width+free,5+height+free)
            bounds=p.bounds
            if any((bounds[0]<limit[0], bounds[1]<limit[1], bounds[2]>limit[2], bounds[3]>limit[3])):
                clipped=p.intersection(box(*limit))
                if clipped.geom_type=='Polygon' and not clipped.is_empty and clipped.is_valid and len(clipped.interiors)==len(p.interiors) and p.area-clipped.area<p.area*.018:
                    p=clipped;edge_allowance['bounded_outer_fill']=True
                    if large_shell:edge_allowance['expansion_mm']=0
        if stroke_gaps is not None and 'pull_compensation' not in local:
            clipped = stroke_gaps.clip(p, ci, edge_allowance['expansion_mm'] + edge_allowance['pull_compensation_mm'])
            if clipped is not p:
                if broad_auto and visible is not None and 0 < len(visible.interiors) <= 64 and pale_surfaces:
                    inlays = [objects[j][0]['geometry'] for j in tree.query(visible)
                              if j > index and j in pale_surfaces and object_colors[j] != ci]
                    restored = restore_inlay_underlap(p, clipped, visible, inlays, line_width_mm)
                    if restored is not clipped:
                        edge_allowance['sewn_inlay_underlap'] = True
                        clipped = restored
                p = clipped
                edge_allowance['stroke_gap_protected'] = True
        el=ET.SubElement(parent,f'{{{SVG}}}path',id=pid,d=poly_path(p),fill=info['colors'][ci]['hex'],**{'fill-rule':'evenodd','data-thread-color':str(ci),'data-role':'随曲线填针' if guided else '榻榻米填针'})
        el.set(f'{{{INK}}}label',f'{info["colors"][ci]["name"]} · 填针')
        expansion=edge_allowance['expansion_mm'];pull=edge_allowance['pull_compensation_mm']
        for k,v in {'auto_fill':True,'fill_method':'guided_fill' if guided else 'auto_fill','guided_fill_strategy':1,'angle':angle,'row_spacing_mm':row,'max_stitch_length_mm':stitch_length,'running_stitch_length_mm':2.2,'skip_last':False,'staggers':staggers,'gap_fill_rows':2 if cross else 0,'underpath':True,'underlay_underpath':True,'fill_underlay':cross,'fill_underlay_row_spacing_mm':round(max(.6,row*3),3),'fill_underlay_angle':(angle+90)%180,'fill_underlay_inset_mm':inset_mm,'fill_underlay_max_stitch_length_mm':3.5,'expand_mm':expansion,'pull_compensation_mm':pull,'trim_after':True}.items():param(el,k,v)
        if broad_auto and edge_allowance.get('supported_edge') and detail_metrics(visible)['narrow_fraction'][thread]>.04:
            # At a narrow inset, extra rows beyond a section can sew over the
            # exposed neighbouring colour. The backing pass already provides overlap.
            param(el,'gap_fill_rows',0)
            param(el,'running_stitch_tolerance_mm',min(.08,line_width_mm/3))
            edge_allowance['inset_gap_rows']=0
        planned.append({'id':pid,'color':ci,'stitch_type':'填针','role':'随曲线面针' if guided else '面针','area_mm2':round(p.area,3),'underlay':cross,'contour_underlay':contour,'row_spacing_mm':row,'angle':angle,'guided':guided,**edge_allowance,'max_stitch_length_mm':stitch_length,'staggers':staggers})
        if grid:
            planned[-1]['registered_rows'] = True
        if spread_pass:
            support=spread_pass=='support'
            role='展开面底针' if support else '尖端填针' if spread_pass=='hub' else '随展开方向填针'
            param(el,'trim_after',spread_pass=='last')
            el.set('data-role',role);el.set(f'{{{INK}}}label',role)
            planned[-1].update(role=role,spread_pass=spread_pass,stitch_type='填针底针' if support else '填针',underlay=support)
        object_shapes[pid]=p

    def spread_fill(source,p,ci,pid):
        field=converging_surface(source)
        if not field:return False
        row=local.get('fill_spacing',spacing)
        pull=local.get('pull_compensation',0)
        target=p.buffer(pull,quad_segs=4) if pull else p
        sections=spreading_sections(target,field,row)
        if not sections and target is not source and not pull:
            target=source;sections=spreading_sections(source,field,row)
        if not sections:return False
        support=target.buffer(-inset_mm)
        choice=local.get('underlay','auto')
        if support.geom_type=='Polygon' and support.area>1 and choice!='none':
            running(support.exterior.coords,ci,pid+'-edge-underlay','轮廓底针')
            if choice=='full' or (choice=='auto' and target.area>12):
                fill(support,ci,pid+'-support',angle=(sections[0][1]+90)%180,spread_pass='support')
        for i,(part,angle) in enumerate(sections):
            kind='hub' if i==0 else 'last' if i==len(sections)-1 else 'panel'
            fill(part,ci,pid+f'-section{i+1}',angle=angle,spread_pass=kind)
        return True
    def satin(p,ci,pid,outline=False,small_surface=False,check_inlay=False,registered_ribbon=False,attached_band=False):
        small_surface=small_surface or attached_band
        round_spot=(adaptive_fill and not local and fabric=='twill' and intent['structure']=='spot'
                    and satin_spacing==PRESETS[thread]['normal'][1] and small_round_spot(p,line_width_mm))
        if round_spot:
            # Reserve part of a strand at both the rail edges and round ends.
            inset=p.buffer(-.25*line_width_mm,quad_segs=4)
            if inset.geom_type=='Polygon' and not inset.is_empty:p=inset
            else:round_spot=False
        sections,construction=satin_candidate(p)
        if sections is None:return False
        aa,bb,widths=sections[0];median=float(np.median(widths))
        if median<.30:return False
        if local.get('method') in {'run','running'}:
            if p.interiors or len(sections)!=1 or float(max(widths))>1.4:return False
            running((np.asarray(aa)+np.asarray(bb))/2,ci,pid,'细线走针',bean=local['method']=='running',trim=True);return True
        if median<.55 and len(sections)==1 and not p.interiors and local.get('method')!='satin' and not small_surface:
            running((np.asarray(aa)+np.asarray(bb))/2,ci,pid,'细线走针',bean=True,trim=True);return True
        coverage=unary_union([rail_coverage(a,b) for a,b,_ in sections])
        # Some broad curved accent strokes have straight-column rails. On a
        # sewn concave side, full pull compensation can fill the neighbouring
        # colour even though the unexpanded rails fit the outline exactly.
        recessed=False
        if (check_inlay and adaptive_fill and not local and fabric=='twill' and len(sections)==1
                and not p.interiors and construction=='直向窄带' and 1.2<=median<=3.5):
            line=view['meta']['preview_thread_width_mm']
            recess=p.convex_hull.difference(p).buffer(-.5*line,quad_segs=4)
            if recess.area>5*line**2:
                earlier=[objects[j][0]['geometry'] for j in tree.query(recess) if j<index and object_colors[j]!=ci]
                supported=recess.intersection(unary_union(earlier)) if earlier else None
                recessed=(supported is not None and supported.area>=.48*recess.area and
                          coverage.buffer(profile['pull_satin']+.5*line,quad_segs=4).intersection(supported).area>.32*supported.area)
        gaps=p.difference(coverage.buffer(.12)).buffer(.1).intersection(p)
        parts=list(gaps.geoms) if hasattr(gaps,'geoms') else [gaps]
        parts=[g for g in parts if g.geom_type=='Polygon' and g.area>.2]
        for i,part in enumerate(parts):
            if not satin(part,ci,pid+f'-join{i+1}'):
                fill(part,ci,pid+f'-join{i+1}')
        role='最后包边' if outline else '窄条缎面针'
        for i,(aa,bb,widths) in enumerate(sections):
            section_id=pid if len(sections)==1 else pid+f'-edge{i+1}';median=float(np.median(widths))
            line_width=view['meta']['preview_thread_width_mm']
            inlaid=(check_inlay and not outline and not small_surface and len(sections)==1 and not p.interiors
                    and fabric=='twill' and 'satin_spacing' not in local and 'pull_compensation' not in local
                    and satin_spacing in {preset[1] for preset in PRESETS[thread].values()}
                    and inlaid_satin_band(aa,bb,widths,line_width,tree,object_colors,index,
                                          compact=adaptive_fill and not local))
            closed_rim=(construction=='闭合轮廓与分支' and fabric=='twill'
                        and satin_spacing==PRESETS[thread]['normal'][1] and 'pull_compensation' not in local)
            if small_surface or inlaid or closed_rim or recessed:
                # The machine places needle centres. Reserve part of the thread
                # footprint at a supported colour boundary or around a tiny motif.
                compact_inlay=(inlaid and adaptive_fill and not local and p.area<10)
                surface_inset=(.48 if compact_inlay else .45 if small_surface or recessed else .25)*line_width
                if attached_band:surface_inset=.3*line_width
                aa=np.asarray(aa);bb=np.asarray(bb)
                offset=(bb-aa)*np.minimum(.38,surface_inset/np.maximum(widths,.0001))[:,None]
                aa=aa+offset;bb=bb-offset
            # The inner rail of an enclosing frame rests over previously sewn
            # colour. A half-strand landing setback keeps the fill visible;
            # neither the free, outside rail nor a genuine cloth hole moves.
            backed_rim=False
            if (outline and construction=='轮廓随形' and len(p.interiors)==1 and adaptive_fill and not local
                    and fabric=='twill' and .6<=median<=1.7 and satin_spacing==PRESETS[thread]['normal'][1]):
                aperture=Polygon(p.interiors[0])
                earlier=[j for j in tree.query(aperture) if j<index]
                supporting=[objects[j][0]['geometry'] for j in earlier]
                contrasting=[objects[j][0]['geometry'] for j in earlier if object_colors[j]!=ci]
                if (contrasting and aperture.intersection(unary_union(contrasting)).area>.50*aperture.area
                        and aperture.intersection(unary_union(supporting)).area>.80*aperture.area):
                    aa=np.asarray(aa);bb=np.asarray(bb)
                    bb=bb+(aa-bb)*np.minimum(.22,.5*line_width/np.maximum(widths,.001))[:,None]
                    backed_rim=True
            sided=None
            if (adaptive_fill and not local and fabric=='twill' and not outline and len(sections)==1
                    and not p.interiors and not (small_surface or inlaid or recessed or registered_ribbon)
                    and satin_spacing==PRESETS[thread]['normal'][1]):
                sided=one_sided_satin_rails(aa,bb,widths,line_width,profile['pull_satin'],tree,object_colors,index)
                if sided is not None:aa,bb=sided
            protected_rails=None
            if stroke_gaps is not None and 'pull_compensation' not in local:
                protected_rails=stroke_gaps.clip_rails(aa,bb,ci,profile['pull_satin'])
                if protected_rails is not None:aa,bb=protected_rails
            el=ET.SubElement(root,f'{{{SVG}}}path',id=section_id,d=rail(aa)+' '+rail(bb),fill='none',stroke=info['colors'][ci]['hex'],**{'stroke-width':'.15','data-thread-color':str(ci),'data-role':role})
            el.set(f'{{{INK}}}label',f'{info["colors"][ci]["name"]} · {role}')
            bottom=satin_underlay(median,float(np.quantile(widths,.75)),profile['supportive'])
            choice=local.get('underlay','auto')
            if choice!='auto':
                bottom.update(center_walk_underlay=choice in ['light','full'],contour_underlay=choice=='full' and median>=1.2,zigzag_underlay=choice=='full' and median>=1.2)
            auto_pull=profile['pull_satin']
            peak=satin_spacing
            if small_surface:
                auto_pull=0
                if not attached_band or satin_spacing==PRESETS[thread]['normal'][1]:
                    peak=max(.18,min(peak,round(PRESETS[thread]['normal'][1]*.8,2)))
            elif registered_ribbon:
                auto_pull=min(auto_pull,.04)
                peak=min(peak,round(1.1*line_width,2))
            elif inlaid or closed_rim or backed_rim or recessed:
                auto_pull=0
                peak=min(peak,round(1.1*line_width,2))
                # A supported, tapered ribbon changes width rapidly. Keep its
                # same-rail peak spacing within one strand without packing the tip.
                if (inlaid and adaptive_fill and not local and satin_spacing == PRESETS[thread]['normal'][1]
                        and p.area >= 2 and median >= 4 * line_width
                        and min(widths[0], widths[-1]) <= line_width and max(widths) > 1.25 * median):
                    peak = min(peak, line_width)
            elif (stage=='border' and adaptive_fill and fabric=='twill' and not local and width<=35
                  and .55<=median<=1.6 and peak==PRESETS[thread]['normal'][1]):
                auto_pull=0
                peak=.24 if thread=='75D' else .28
            elif not outline and not profile['supportive'] and median<=1.35 and (local.get('method','auto')=='auto' or local.get('source')=='ai'):
                auto_pull=min(auto_pull,median*.045)
            # Exposed rails cannot borrow coverage from neighbouring fills. On
            # stable patch cloth limit the distance between successive peaks,
            # including on the outer rail of a bend. Honour manual/custom pitch.
            exposed = (adaptive_fill and fabric=='twill' and not local and
                       satin_spacing==PRESETS[thread]['normal'][1] and
                       len(sections)==1 and construction!='闭合轮廓与分支' and len(p.interiors)<=1 and
                       (outline or near_cloth(p)))
            if exposed:
                peak = min(peak, round(1.10*line_width, 2))
                if attached_band:peak=min(peak,line_width)
            # A normal peak interval leaves just two or three visible stitches
            # on a millimetre dot. Keep the circular footprint on stable cloth.
            if round_spot:
                peak=min(peak,round(1.05*line_width,2))
                auto_pull=0
            peak=local.get('satin_spacing',peak);short_distance=satin_short_distance(peak)
            pull=local.get('pull_compensation',auto_pull)
            for k,v in {'satin_column':True,'zigzag_spacing_mm':peak,'pull_compensation_mm':pull,'short_stitch_inset':15,'short_stitch_distance_mm':short_distance,**bottom,'trim_after':True,'max_stitch_length_mm':local.get('max_stitch_length',5.8)}.items():param(el,k,v)
            object_shapes[section_id]=rail_coverage(aa,bb)
            planned.append({'id':section_id,'color':ci,'stitch_type':'缎面针','role':role,'construction':construction,'rail_quality':rail_quality(aa,bb),'area_mm2':round(object_shapes[section_id].area,3),'width_mm':round(median,2),'peak_spacing_mm':peak,'short_stitch_distance_mm':short_distance,'width_range_mm':[round(float(min(widths)),2),round(float(max(widths)),2)],'underlay_types':[name for key,name in [('center_walk_underlay','中心底针'),('contour_underlay','轮廓底针'),('zigzag_underlay','之字底针')] if bottom[key]]})
            planned[-1].update(pull_compensation_mm=round(pull,4),narrow_surface=bool(small_surface))
            if protected_rails is not None:planned[-1]['stroke_gap_protected']=True
            if small_surface or inlaid or closed_rim or recessed:planned[-1]['surface_rail_inset_mm']=round(surface_inset,4)
            if sided is not None:planned[-1]['supported_side_only']=True
            if registered_ribbon:planned[-1]['source_ribbon']=True
            if attached_band:planned[-1]['attached_band_outline_mm']=np.asarray(p.exterior.coords).tolist()
            if recessed:planned[-1]['supported_satin_recess']=True
            if inlaid:planned[-1]['inlaid_satin_band']=True
            if backed_rim:planned[-1]['backed_inner_rail_mm']=round(.5*line_width,3)
        return True
    import cv2
    src_classes=np.load(Path(version_folder)/'classes.npy'); pixel=width/max(1,info['width_px']-1)
    stroke_gaps=StrokeGaps(src_classes,info['colors'],pixel,line_width_mm) if adaptive_fill else None
    edge_dist=cv2.distanceTransform(np.pad((src_classes!=255).astype(np.uint8),1),cv2.DIST_L2,3)
    def near_cloth(shape, min_share=.32):
        ring=shape.exterior
        pts=np.array([ring.interpolate(i/32,normalized=True).coords[0] for i in range(32)])
        xy=np.rint((pts-5)/pixel).astype(int)+1
        return (edge_dist[np.clip(xy[:,1],0,edge_dist.shape[0]-1),np.clip(xy[:,0],0,edge_dist.shape[1]-1)]*pixel<=max(.16,2*pixel)).mean()>=min_share
    borders,background=outer_borders(geo)
    order=[i for i in range(len(geo)) if i!=background]
    if background is not None:order.insert(0,background)
    highlights=inset_details(geo,order)
    # A drawn ring with attached branches finishes over the colour in its
    # opening. Mark only its actual region so manual cuts retain their roles.
    finish_rims=set()
    artwork_index=STRtree([r['geometry'] for r in sewing_regions])
    for region in sewing_regions:
        settings=object_overrides.get(region['key'],{}); shape=region['geometry']
        if settings.get('method','auto') not in {'auto','satin'} or settings.get('sewing_role','edge')!='edge':continue
        branch=branched_ring_rails(shape) is not None
        # A narrow ring around a filled window must finish *after* both fills,
        # including the colour inside its opening. An empty hole has no such dependency.
        rim=False
        if not branch and adaptive_fill and not settings and len(shape.interiors)==1:
            window=Polygon(shape.interiors[0]); shell_area=window.area+shape.area
            if window.area>.52*shell_area and .35<2*shape.area/shape.length<4:
                children=(sewing_regions[j] for j in artwork_index.query(window))
                rim=any(r['color']!=region['color'] and window.covers(r['geometry'].representative_point())
                        and window.intersection(r['geometry']).area>.48*window.area for r in children)
                rim=rim and satin_candidate(shape)[1]=='轮廓随形'
        if branch or rim:finish_rims.add(region['key'])
    # A line beside two different fills may not belong to a single hole.
    # Sew its visible pass after the fills supporting it, including pale lines.
    top_lines={r['key'] for r in sewing_regions
               if object_overrides.get(r['key'],{}).get('method','auto') in {'auto','run','running'}
               and fine_line(r['geometry']) is not None}
    for ci,group in enumerate(geo):
        if min(info['colors'][ci]['rgb'])<210:continue
        for n,p in enumerate(group):
            if p.area<2 and any(q.interiors and Polygon(q.exterior).contains(p) for cj,others in enumerate(geo) if cj!=ci and max(info['colors'][cj]['rgb'])<100 for q in others):highlights.add((ci,n))
    objects=[]
    for stage in ['body','highlight','border']:
        for ci in order:
            for region in [r for r in sewing_regions if r['color']==ci]:
                n=region['source_index']
                actual='border' if (ci,n) in borders or region['key'] in finish_rims else 'highlight' if (ci,n) in highlights or region['key'] in top_lines else 'body'
                if actual==stage:objects.append((region,stage))
    layer_roles={key:s['sewing_role'] for key,s in object_overrides.items() if 'sewing_role' in s}
    objects=order_visible_layers(objects,top_lines,profile['overlap']+.04,layer_roles)
    overlaps=[]
    grouped_angles = (round_group_directions(sewing_regions)
                      if adaptive_fill and fabric=='twill' and spacing==PRESETS[thread]['normal'][0]
                      and len(sewing_regions)<=160 else {})
    tree=STRtree([r['geometry'] for r,_ in objects])
    object_colors=np.asarray([r['color'] for r,_ in objects])
    pale_surfaces = set()
    if adaptive_fill and fabric == 'twill':
        for j, (part, _) in enumerate(objects):
            if min(info['colors'][part['color']]['rgb']) < 237:
                continue
            method = object_overrides.get(part['key'], {}).get('method', 'auto')
            intent = visible_intents.get(part['key']) or recommend_stitch(part['geometry'], line_width_mm)
            if method in {'fill', 'guided', 'satin', 'spread'} or (method == 'auto' and intent['structure'] != 'line'):
                pale_surfaces.add(j)
    for index,(region,stage) in enumerate(objects):
        if index%20==0:update(f'正在安排底针与面针 {index+1} / {len(objects)} 区')
        ci=region['color'];n=region['source_index'];p=region['geometry'];local=object_overrides.get(region['key'],{})
        pid=f'c{ci+1}-region{n+1}'+region['key'][len(region['base_key']):]
        if '-m' in region['base_key']:pid=region['key']
        method=local.get('method','auto')
        intent=visible_intents.get(region['key']) or recommend_stitch(region['geometry'],line_width_mm)
        detail=fine_line(p) if method in {'auto','run','running'} else None
        if detail is None and method in {'auto','run','running'} and intent['structure']=='line':
            detail=(stroke_center(p),intent['width_mm'])
        automatic_choice=method=='auto' or local.get('source')=='ai'
        small_surface=automatic_choice and method in ('auto','satin') and detail is None and narrow_surface(p,view['meta']['preview_thread_width_mm'])
        if stage!='border' and detail is None:
            # Only regions near this shape can supply underlap or close its
            # holes. Avoid rebuilding a union of the whole image per object.
            indices=tree.query(p.buffer(profile['overlap']+.04).envelope)
            later=[objects[j][0]['geometry'] for j in indices if j>index and
                   (objects[j][0]['color']!=ci or objects[j][0]['base_key']==region['base_key'])]
            p,lap=covered_stitch_region(p,later,profile['overlap'])
            if lap['added_area_mm2']>.01:overlaps.append({'id':pid,'color':ci,**lap})
        if p.area<.15:warnings.append('有较小色块，建议放大检查并试绣。')
        before=len(root);planned_before=len(planned)
        ribbon_line=detail is not None and method=='auto' and intent['structure']=='band'
        # An underlap around a narrow stripe can turn its straight rails into
        # a curved or forked column. Keep the visible ribbon and use a small
        # allowance on this early pass, before the adjacent colours go down.
        source_shape=region['geometry'];registered_ribbon=False
        if (adaptive_fill and fabric=='twill' and not local and stage!='border' and not detail
                and satin_spacing==PRESETS[thread]['normal'][1] and not source_shape.interiors
                and source_shape.area<30 and p.area>1.15*source_shape.area
                and source_shape.area>=.98*source_shape.convex_hull.area):
            rails,kind=satin_candidate(source_shape)
            if kind=='直向窄带' and len(rails)==1:
                widths=rails[0][2];lo,hi=np.quantile(widths,[.15,.85])
                registered_ribbon=(2.6*line_width_mm<=lo<=hi<=1.35 and hi<1.18*lo
                                   and source_shape.length>=8*hi)
        if method in ('auto','spread') and intent['structure']=='spread' and spread_fill(source_shape,p,ci,pid):
            pass
        elif method=='spread':
            raise ValueError('这块无法稳定分出展开方向。可拆分选区，或画一条方向曲线。')
        elif intent['structure']=='axis' and method in ('auto','fill') and 'angle' not in local:
            fill(p,ci,pid,underlay=p.area>12,angle=intent['angle'],visible=source_shape,lock_direction=True)
        elif registered_ribbon and satin(source_shape,ci,pid,check_inlay=True,registered_ribbon=True):
            pass
        elif ribbon_line and satin(region['geometry'],ci,pid,small_surface=True):
            for item in planned[planned_before:]:item['original_fine_band']=True
        elif detail is not None:
            center,width_mm=detail
            use_bean=width_mm>=.30 if method=='auto' else method=='running'
            running(center.coords,ci,pid,'细线走针',bean=use_bean,trim=True)
            planned[-1].update(width_mm=round(width_mm,3),construction='原稿细线中心')
            object_shapes[pid]=p
        elif method in ['fill','guided']:
            fill(p,ci,pid,underlay=p.area>12 and 2*p.area/max(p.length,.01)>1.1)
        elif not satin(p,ci,pid,outline=stage=='border',small_surface=small_surface,check_inlay=True):
            # A hidden overlap can turn a valid, narrow column into a fork.
            # Keep the original visible rails in that case.
            source_shape=region['geometry']
            keep_rails=method in ('satin','run','running') or (method=='auto' and intent['structure']=='band')
            restored=keep_rails and not p.equals(source_shape) and satin(source_shape,ci,pid,outline=stage=='border',small_surface=small_surface,check_inlay=True)
            if not restored:
                if method!='auto' and local.get('source')!='ai':raise ValueError(f'{info["colors"][ci]["name"]}选区不适合所选针法，请使用填针，或先分成更简单的窄条。')
                if local.get('source')=='ai':
                    warnings.append(f'{region["key"]} 的建议针法与实际排针轮廓不合，已回到规则填针。')
                    local={}
                split=None
                if (adaptive_fill and fabric=='twill' and automatic_choice and not local
                        and len(sewing_regions)<=160 and intent['structure']=='surface'
                        and detail_metrics(source_shape)['narrow_fraction'][thread]>.04):
                    split=split_exposed_bands(source_shape,p,line_width_mm,lambda g:near_cloth(g,.70))
                body,bands=split if split else (p,[])
                fill(body,ci,pid,underlay=body.area>12 and 2*body.area/max(body.length,.01)>1.1,visible=source_shape)
                for i,band in enumerate(bands):
                    satin(band,ci,pid+f'-tip{i+1}',attached_band=True)
        nodes=[node for node in list(root)[before:] if node.get('data-thread-color') is not None]
        for node in nodes:
            node.set('data-stage',stage);node.set('data-region-key',region['key'])
            node.set('data-region-keys',','.join(region['region_keys']))
            if region['key'] in layer_roles:node.set('data-sewing-role',layer_roles[region['key']])
        for entry in planned[planned_before:]:entry.update(region_key=region['key'],region_keys=region['region_keys'])
        bundles.append({'id':pid,'color':ci,'geometry':p,'outline':stage=='border','stage':stage,'sewing_role':layer_roles.get(region['key']),'nodes':nodes})
        if local.get('fill_spacing',spacing)>.30 and method in ['fill','guided']:warnings.append('局部填充行距偏大，请核对覆盖。')
    if spacing>.30:warnings.append('当前填充行距较大，实绣可能露底，可选择常规覆盖后重新排针。')
    view['meta']['planned_objects']=planned;view['meta']['warnings']=list(dict.fromkeys(warnings))
    ET.ElementTree(root).write(str(out/'embroidery.svg'),encoding='utf-8',xml_declaration=True)
    flat=write_svg(view,out/'layers.svg')
    dimensioned=copy.deepcopy(flat);dimensioned.set('width',f'{width+20}mm');dimensioned.set('height',f'{height+20}mm');dimensioned.set('viewBox',f'0 0 {width+20} {height+20}')
    g=ET.SubElement(dimensioned,f'{{{SVG}}}g',id='dimensions',fill='#3e5144',stroke='#7b867c',**{'stroke-width':'.12'})
    x0,y0=5,5;x1,y1=5+width,5+height
    for x,y,X,Y in [(x0,y1+1,x0,y1+6),(x1,y1+1,x1,y1+6),(x0,y1+5,x1,y1+5),(x1+1,y0,x1+6,y0),(x1+1,y1,x1+6,y1),(x1+5,y0,x1+5,y1)]:ET.SubElement(g,f'{{{SVG}}}line',x1=str(x),y1=str(y),x2=str(X),y2=str(Y))
    ET.SubElement(g,f'{{{SVG}}}text',x=str((x0+x1)/2),y=str(y1+9),stroke='none',**{'font-size':'2.5','text-anchor':'middle'}).text=f'{width:g} mm'
    ET.SubElement(g,f'{{{SVG}}}text',transform=f'translate({x1+9},{(y0+y1)/2}) rotate(-90)',stroke='none',**{'font-size':'2.5','text-anchor':'middle'}).text=f'{height:.1f} mm'
    ET.ElementTree(dimensioned).write(str(out/'dimensions.svg'),encoding='utf-8',xml_declaration=True)
    update('正在规划针迹并生成工程文件')
    engine_started=time.monotonic()
    dst,pes=export_engine(root,out,update)
    engine_seconds=time.monotonic()-engine_started
    baseline=movement_metrics(dst);chosen=baseline;selected='original';candidate_metrics=None
    tested_orders=set()
    # Large detailed designs retain the valid color/overlap order. Re-exporting
    # hundreds of regions for speculative travel savings multiplies latency.
    routing_orders=[nearby_order(bundles),nearby_order(bundles,centers=True)] if len(bundles)<=120 and engine_seconds<=25 else []
    for attempt,ordered in enumerate(routing_orders):
        order_ids=tuple(b['id'] for b in ordered)
        if order_ids==tuple(b['id'] for b in bundles) or order_ids in tested_orders:continue
        tested_orders.add(order_ids)
        update('正在比较同色针路，减少跨区空移')
        candidate=out/f'routing-candidate-{attempt+1}';candidate_root=copy.deepcopy(root)
        candidate_root[:]=[copy.deepcopy(node) for node in root if node.get('data-thread-color') is None]+[copy.deepcopy(node) for b in ordered for node in b['nodes']]
        try:
            routed,routed_pes=export_engine(candidate_root,candidate,update)
            candidate_metrics=movement_metrics(routed)
            def count(pattern,command):return sum(raw&255==command for _,_,raw in pattern.stitches)
            if (candidate_metrics['travel_mm']<chosen['travel_mm']-.1 and
                candidate_metrics['sewing_mm']<=baseline['sewing_mm']*1.03 and
                count(routed,pe.TRIM)<=count(dst,pe.TRIM) and
                count(routed,pe.COLOR_CHANGE)==count(dst,pe.COLOR_CHANGE)):
                for file in candidate.iterdir():
                    if file.is_file():(out/file.name).write_bytes(file.read_bytes())
                root=candidate_root;dst,pes=routed,routed_pes;chosen=candidate_metrics;selected='nearby'
                order={node.get('id'):i for i,node in enumerate(root.iter())}
                planned.sort(key=lambda item:order[item['id']])
        except EngineResourceError:
            raise
        except (ValueError,subprocess.TimeoutExpired):
            # The validated baseline is still a complete usable draft.
            pass
    connections=[];connection_check={'status':'no_covered_route'}
    update('正在检查同色衔接，区分藏线、短接与必要剪线')
    try:
        if len(bundles)>120 or engine_seconds>25:
            connected,proposals=root,[]
            connection_check={'status':'not_tested','reason':'复杂或耗时稿保留原剪线，避免反复生成整份工程'}
        else:connected,proposals=connection_candidate(root,object_shapes,ENGINE,out/'connection-proposal',update=update,
                                                      blocks_link=stroke_gaps.blocks_link if stroke_gaps is not None else None)
        if proposals:
            candidate=out/'connection-candidate';connected_dst,connected_pes=export_engine(connected,candidate,update)
            metrics=movement_metrics(connected_dst)
            def count(pattern,command):return sum(raw&255==command for _,_,raw in pattern.stitches)
            same_colors=count(connected_dst,pe.COLOR_CHANGE)==count(dst,pe.COLOR_CHANGE)
            fewer_trims=count(connected_dst,pe.TRIM)<count(dst,pe.TRIM)
            route_budget=sum(p['length_mm'] for p in proposals)*1.3+2
            valid=(same_colors and fewer_trims and metrics['travel_mm']<=chosen['travel_mm']+.1
                   and metrics['sewing_mm']<=chosen['sewing_mm']+route_budget)
            connection_check={'status':'accepted' if valid else 'kept_original',
                              'proposed':len(proposals),'trims_before':count(dst,pe.TRIM),
                              'trims_after':count(connected_dst,pe.TRIM),'before':chosen,'after':metrics}
            if valid:
                for file in candidate.iterdir():
                    if file.is_file():(out/file.name).write_bytes(file.read_bytes())
                root=connected;dst,pes=connected_dst,connected_pes;chosen=metrics;connections=proposals
                planned.extend(p for p in connections if p.get('emits_path',True))
                order={node.get('id'):i for i,node in enumerate(root.iter())}
                planned.sort(key=lambda item:order[item['id']])
    except EngineResourceError:
        raise
    except (ValueError,subprocess.TimeoutExpired,ET.XMLSyntaxError):
        connection_check={'status':'kept_original','reason':'连接候选未通过，保留原有剪线方案'}
    view['meta']['routing']={'selected':selected,'before':baseline,'after':chosen,'candidate':candidate_metrics,'initial_export_seconds':round(engine_seconds,2),
                             'connections':connection_check,
                             'object_order':[node.get('id') for node in root if node.get('data-thread-color') is not None],
                             'preserves_color_order':True,'preserves_overlap_order':True}
    update('正在核对针数、尺寸与导出文件')
    passes,omitted=verified_passes(root,(out/'design_threadlist.txt').read_text(),info['colors'],{r['key']:r['geometry'].area for r in regions})
    sequence=[g['color'] for g in passes];stage_groups=[g['stages'] for g in passes];stages=['+'.join(group) for group in stage_groups]
    view['meta']['unstitched_regions']=omitted
    if omitted:view['meta']['warnings'].append('微小选区未产生独立针迹：'+'、'.join(omitted))
    view['meta']['color_sequence']=sequence
    color_count=len(info['colors']);counts=[0]*color_count;pass_counts=[0]*len(sequence);pass_index=0;ci=sequence[0];prefix=[];commands=[];lengths=[];previous=None;bounds=dst.bounds();kinds=collections.Counter()
    for x,y,raw in dst.stitches:
        cmd=raw&255;kinds[cmd]+=1
        if cmd==pe.COLOR_CHANGE:pass_index+=1
        if pass_index>=len(sequence):raise ValueError('换色顺序核对未通过。')
        ci=sequence[pass_index]
        if cmd==pe.STITCH:
            counts[ci]+=1;pass_counts[pass_index]+=1
            if previous is not None:lengths.append(math.hypot(x-previous[0],y-previous[1])/10)
        commands.append([round(x/10+origin[0],3),round(y/10+origin[1],3),cmd,ci]);prefix.append(sum(counts));previous=(x,y)
    if pass_index+1!=len(sequence) or any(n==0 for n in counts) or any(n==0 for n in pass_counts):raise ValueError('有色块太小未能生成针迹，请增大尺寸或简化细节。')
    if lengths and max(lengths)>6.1:raise ValueError('存在过长的落针，请调整版面后重新排针。')
    stats={'stitch_count':sum(counts),'stitches_by_color':counts,'unique_thread_colors':color_count,'color_changes':kinds[pe.COLOR_CHANGE],'trims_dst':kinds[pe.TRIM],'jump_commands_dst':kinds[pe.JUMP],'dimensions_mm':[round((bounds[2]-bounds[0])/10,2),round((bounds[3]-bounds[1])/10,2)],'max_stitch_mm':round(max(lengths,default=0),3),'sew_geometry_dst_pes_equal':True,'physically_sewn':False}
    stats.update(travel_mm=chosen['travel_mm'],travel_before_mm=baseline['travel_mm'],travel_saved_mm=round(baseline['travel_mm']-chosen['travel_mm'],2),sewing_mm=chosen['sewing_mm'])
    view['meta']['sewing_steps']=[{'color':ci,'stage':stage,'stages':stage_groups[i],'label':('细节与最后包边' if 'border' in stage and stage!='border' else '最后包边' if stage=='border' else '局部嵌色与高光' if 'highlight' in stage else '底色填绣' if ci==background else '主体填绣'),'stitch_count':pass_counts[i]} for i,(ci,stage) in enumerate(zip(sequence,stages))]
    for step,group in zip(view['meta']['sewing_steps'],passes):
        if group['roles']:step['label']=' / '.join(SEWING_ROLES[role] for role in group['roles'])
    view['meta']['craft']={'profile':profile['name']+'试排','border_last':bool(borders),'background_color':background,
        'border_colors':sorted({ci for ci,_ in borders}),'fill_angles':sorted({o['angle'] for o in planned if 'angle' in o}),
        'object_types':dict(collections.Counter(o['stitch_type'] for o in planned)),
        'fill_underlay_inset_mm':inset_mm,'fill_contour_inset_mm':inset_mm,'fill_staggers':4,'fill_gap_rows':2,
        'minimum_stitch_filter_mm':.1,'underlap_mm':profile['overlap'],'overlap_objects':overlaps,
        'closed_detail_holes':sum(o['closed_detail_holes'] for o in overlaps),
        'curved_satin_objects':sum(o.get('construction')=='弯曲窄带' for o in planned),
        'guided_fill_objects':sum(o.get('guided',False) for o in planned),'local_regions':len(object_overrides),
        'region_cuts':sum('merge_regions' not in edit for edit in region_cuts),'region_merges':sum('merge_regions' in edit for edit in region_cuts),
        'merged_local_regions':len(regions)-len(sewing_regions),
        'hidden_connections':connections,
        'limitations':['根据可辨轮廓安排针向，平涂稿中看不出的毛流或织纹需指定方向','需要按实际底布、衬料和线材试绣校准']}
    stats['color_blocks']=len(sequence);stats['stitches_by_block']=pass_counts
    view.update({'stats':stats,'commands':commands,'prefix':prefix});view['width']=max(view['width'],stats['dimensions_mm'][0]+10);view['height']=max(view['height'],stats['dimensions_mm'][1]+10)
    update('正在检查实际针迹是否遮住花纹、堵住间隙')
    classes=np.load(Path(version_folder)/'classes.npy',mmap_mode='r',allow_pickle=False)
    view['meta']['quality_review']=review_needle_geometry(view,regions,classes,visible_intents)
    view['meta']['quality_review']['omitted_motifs']=info.get('fit_report',{}).get('omitted_motifs',[])
    if view['meta']['quality_review']['omitted_motifs']:
        view['meta']['quality_review']['status']='needs_review'
    save_json(out/'quality-review.json',view['meta']['quality_review'])
    save_json(out/'preview.json',view);save_json(out/'validation.json',stats)
    with (out/'threads.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(['绣序','颜色','针数','线粗','底料','填充相邻行距mm','包边峰到峰间距mm','填充打底行距mm','示范品牌','示范线号','RGB'])
        for i,ci in enumerate(sequence):
            c=info['colors'][ci];w.writerow([i+1,c['name']+' · '+view['meta']['sewing_steps'][i]['label'],pass_counts[i],thread,profile['name'],spacing,satin_spacing,underlay_spacing,'Madeira Rayon',c['thread']['code'],c['hex']])
    render(view,out/'stitch-preview.png')
    (out/'README.txt').write_text(f'绣花工程试排\n底料：{profile['name']}（试排预设）\n尺寸：{width:g} × {height:.2f} mm\n线粗选项：{thread}\n填充相邻行距：{spacing} mm；包边峰到峰间距：{satin_spacing} mm\n大色块交叉打底行距：{underlay_spacing} mm；打底与轮廓底针内缩{inset_mm:.2f} mm\n针数：{sum(counts)}；线色：{color_count}；色段：{len(sequence)}\n\n工艺：相接处先绣色块向后绣区域压入{profile['overlap']:.2f} mm，小五官下保留连续底色；透明孔保留。填充行转弯落针保留。适合的宽色块先轮廓底针、再交叉打底与错针填充。窄条优先缎面针，极细条尝试三重走针。已有包边最后绣，必要时返回用过的线色。针向按区域主方向推算，不是动物毛流识别。\n\nembroidery.svg 为可编辑针法工程。dimensions.svg 为带尺寸的平面分色图。design.dst / design.pes 已读回核对，针数包含打底和主绣，不含跳针、剪线及换色。threads.csv 按真实色段绣序排列，同色可能重复出现。\n\n75D / 105D 默认密度为可调试排预设，尚未经实际线材、底布和机型校准。示范线号需核对实体线卡。预览线宽为示意值，不是D数换算的实际直径。文件未经实物试绣，局部针法、拉缩和细节仍可能需要修版。\n')
    save_json(out/'region-settings.json',{'width_mm':width,'height_mm':height,'region_cuts':region_cuts,'object_overrides':object_overrides,'effective_objects':planned})
    with (out/'README.txt').open('a') as f:f.write(f'\n局部工艺：{len(object_overrides)} 个区域，{len(region_cuts)} 次拆分/合并调整。region_cuts 为按顺序保存的操作记录，可拆开后合并再拆分。局部参数优先于全局默认值；详情及实际对象参数见 region-settings.json。分区接头及曲线填针需实绣复核。\n')
    names=['embroidery.svg','layers.svg','dimensions.svg','design.dst','design.pes','threads.csv','stitch-preview.png','validation.json','quality-review.json','region-settings.json','README.txt']
    with zipfile.ZipFile(out/'embroidery-project.zip','w',zipfile.ZIP_DEFLATED) as z:
        for name in names:z.write(out/name,name)
    return view

def render(view,path):
    # Supersample millimetre geometry: integer pixel rounding at 16 px/mm
    # produced artificial light bands every few 0.20 mm fill rows. Physical
    # line width is unchanged and remains explicitly an illustrative value.
    antialias=4;output_scale=min(24,1600/max(view['width'],view['height']))
    w=round(view['width']*output_scale);h=round(view['height']*output_scale);scale=output_scale*antialias
    last=None;line_width=view['meta'].get('preview_thread_width_mm',preview_thread_width(view['meta']['thread_spec']))
    segments=[]
    for x,y,cmd,ci in view['commands']:
        current=(x*scale,y*scale)
        if cmd==pe.STITCH and last:
            segments.append((last,current,ci))
        last=current
    # Render strips with filter overlap, at the original resolution and thread
    # width. This replaces a ~160 MB supersampling canvas with bounded tiles.
    result=Image.new('RGB',(w,h),'#f3f0e8')
    palette=[]
    for color in view['meta']['colors']:
        c=color['rgb'];palette.append((tuple(c),tuple(round(v*.83) for v in c),tuple(round(v+(255-v)*.15) for v in c)))
    margin=line_width*scale+2
    for row in range(0,h,128):
        end=min(h,row+128);top=max(0,row-4);bottom=min(h,end+4);offset=top*antialias
        tile=Image.new('RGB',(w*antialias,(bottom-top)*antialias),'#f3f0e8');d=ImageDraw.Draw(tile)
        for a,b,ci in segments:
            if max(a[1],b[1])+margin<offset or min(a[1],b[1])-margin>bottom*antialias:continue
            c,dark,light=palette[ci]
            # Quantize before translating, as the original full canvas does.
            # Truncating negative tile coordinates would shift crossing stitches.
            points=(int(a[0]),int(a[1])-offset,int(b[0]),int(b[1])-offset)
            d.line(points,fill=dark,width=max(1,round(line_width*scale*1.09)))
            d.line(points,fill=c,width=max(1,round(line_width*scale)))
            d.line((int(a[0]-.03*scale),int(a[1]-.03*scale)-offset,int(b[0]-.03*scale),int(b[1]-.03*scale)-offset),fill=light,width=max(1,round(.035*scale)))
        reduced=tile.resize((w,bottom-top),Image.Resampling.LANCZOS)
        result.paste(reduced.crop((0,row-top,w,end-top)),(0,row))
        tile.close();reduced.close()
    result.save(path)
    result.close()
