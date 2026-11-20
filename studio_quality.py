"""Read-back needle review against the intended regions, not a beauty render."""
import math
from collections import defaultdict
import cv2
import numpy as np
from shapely.geometry import LineString, Polygon, Point
from shapely.ops import nearest_points
from shapely.strtree import STRtree
from studio_craft import FABRICS, fine_line, region_recommendations, satin_candidate, small_round_spot, stroke_center
from studio_local import guide_coordinates
from studio_structure import converging_surface
from studio_feasibility import detail_metrics, approximate_width


def inspect_feature_witnesses(p, ci, target, top, mask, precision, line, thread):
    """Recheck a conservative artwork warning against the exported surface.

    A digitized coloured inlay is not a hole in fabric. Require visible
    colour down its centre and room between *every* pair of openings. Check
    each vulnerable tip separately on both sides of its drawn edge, so a
    large, well-covered face cannot conceal a missing corner. Real cloth
    eyelets below two thread widths and tightly spaced stripes stay flagged.
    """
    if p.geom_type!='Polygon' or p.area<2 or len(p.interiors)>20:return None
    f=detail_metrics(p);fraction=f['narrow_fraction'][thread]; known_hole=f['smallest_hole_mm']
    check_width=known_hole is not None and known_hole<2*line
    if f['width_mm']<3*line or fraction>.24 or not(check_width or fraction>.04):return None
    holes=[Polygon(h) for h in p.interiors];holes_ok=bool(holes); separations=[]; counts=0
    for i,hole in enumerate(holes):
        w=approximate_width(hole)
        separations.append(hole.distance(p.exterior))
        separations.extend(hole.distance(other) for other in holes[i+1:])
        if min(separations)<1.9*line:return None
        core=mask(hole.buffer(-min(line*.65,w*.30),quad_segs=4))
        if int(core.sum())<6:return None
        expected=target[core];surface=top[core]
        if (expected==ci).mean()>.03:return None
        if w<2*line:
            # The narrow mark must actually be a stitchable continuous stroke
            # of another ink, not transparent cloth, dust or a halo.
            if w<line or fine_line(hole) is None or (expected==0).mean()>.01:return None
        if (surface==expected).mean()<.92:return None
        counts+=1
    pitch=.7 if thread=='75D' else 1.
    each=p.difference(p.buffer(-pitch/2,quad_segs=4).buffer(pitch/2+.05,quad_segs=4))
    parts=[a for a in getattr(each,'geoms',(each,)) if a.geom_type=='Polygon' and a.area>=line**2*.30]
    if len(parts)>64:return None
    interior=mask(p.buffer(-.35*line,quad_segs=4))
    outside=~mask(p.buffer(.40*line,quad_segs=4))&(target!=ci)
    good=top==ci
    distance=cv2.distanceTransform((~good).view(np.uint8),cv2.DIST_L2,5)*precision
    retained=[];worst_error=0.;rounded_tips=0
    for part in parts:
        body=mask(part)&(target==ci);probe=body&interior
        if not probe.any():probe=body
        if not probe.any():return None
        here=distance[probe]; keep=float(good[probe].mean());worst_error=max(worst_error,float(here.max()))
        # Maximum displacement matters as much as mean colour recovery. A
        # missing narrow branch is never paid for by a correctly sewn base.
        if here.max()>.78*line:return None
        if keep<.88:
            # A tiny contour remainder can have just a handful of interior
            # samples. Require actual ink in the feature and bound displacement
            # on the whole remainder, including its tip, in millimetres.
            if (part.area>1.25*line**2 or good[body].mean()<.5 or keep<.5
                    or here.max()>min(.05,precision)+1e-6 or distance[body].max()>.5*line):
                return None
            rounded_tips+=1
        across=mask(part.buffer(1.5*line,quad_segs=4))&outside
        if across.any() and good[across].mean()>.12:return None
        retained.append(keep)
    if fraction>.04 and not retained:return None
    return {'checked_narrow':bool(retained),'checked_inlays':holes_ok,'thread':thread,
            'tested_tips':len(retained),'tested_openings':counts,'rounded_tips':rounded_tips,
            'weakest_tip_percent':round(100*min(retained),1) if retained else None,
            'max_tip_deviation_mm':round(worst_error,3),
            'min_clearance_mm':round(min(separations),3) if separations else None}


def bridged_gaps(regions,expected,surface,precision,line_width):
    """Probe the intended gap between neighbouring, separate parts of one colour."""
    gaps=[];shapes=[r['geometry'] for r in regions];tree=STRtree(shapes)
    for i,shape in enumerate(shapes):
        ci=regions[i]['color']+1
        for j in sorted(tree.query(shape,predicate='dwithin',distance=2.5*line_width)):
            if j<=i or regions[j]['color']+1!=ci:continue
            other=shapes[j];distance=shape.distance(other)
            if distance<max(precision*1.5,line_width*.22):continue
            a,b=nearest_points(shape,other)
            points=np.rint(np.linspace(a.coords[0],b.coords[0],11)[2:-2]/precision).astype(int)
            x,y=points.T
            intended_gap=expected[y,x]!=ci
            if intended_gap.sum()<4:continue
            if np.mean(surface[y,x][intended_gap]==ci)>=.7:
                gaps.append({'first':regions[i]['key'],'second':regions[j]['key'],'color':ci-1,
                             'gap_mm':round(distance,3),'point_mm':np.mean([a.coords[0],b.coords[0]],axis=0).round(3).tolist()})
    return gaps


def direction_probes(shape, kind, width, intent, guide):
    """Independent probes on the original geometry, before pull or underlap."""
    if kind in ('line','guide'):
        center=guide if kind=='guide' else stroke_center(shape)
        if center is None:return
        step=min(.23,center.length/8)
        for d in np.linspace(step,center.length-step,min(160,max(12,math.ceil(center.length/.25)))):
            a,b=(np.asarray(center.interpolate(d+offset).coords[0]) for offset in (-step,step))
            point=center.interpolate(d)
            if kind=='line' or shape.contains(point):yield point.coords[0],b-a,0
    elif kind=='band':
        sections,_=satin_candidate(shape)
        for left,right,_ in sections or []:
            stride=max(1,len(left)//50)
            for a,b in zip(np.asarray(left)[1:-1:stride],np.asarray(right)[1:-1:stride]):
                if np.linalg.norm(a-b)<2.2*width:continue
                for s in (.35,.65):yield a+s*(b-a),b-a,0
    elif kind=='spread':
        root,start,sweep,radius=converging_surface(shape)
        for i,t in enumerate(np.linspace(.07,.93,15)):
            vector=np.array([math.cos(start+sweep*t),math.sin(start+sweep*t)])
            for distance in (.36,.49,.63,.76,.85):
                point=root+radius*distance*vector
                if shape.contains(Point(point)):yield point,vector,i//5
    elif kind=='axis':
        theta=math.radians(-intent['angle'])
        vector=np.array([math.cos(theta),math.sin(theta)])
        x0,y0,x1,y1=shape.bounds
        for x in np.linspace(x0,x1,10)[1:-1]:
            for y in np.linspace(y0,y1,10)[1:-1]:
                if shape.contains(Point(x,y)):yield (x,y),vector,0


def review_structure(regions, meta, intents, surface, angles, precision, line_width):
    by_key=defaultdict(set)
    attached=defaultdict(list)
    for item in meta['planned_objects']:
        if item.get('role') not in ('细线走针','面针','随曲线面针','尖端填针','随展开方向填针','最后包边','窄条缎面针'):
            continue
        for key in item.get('region_keys') or [item.get('region_key')]:
            by_key[key].add(item['stitch_type'])
            if item.get('attached_band_outline_mm'):
                attached[key].append(Polygon(item['attached_band_outline_mm']))
    results={};issues=[]
    for region in regions:
        key=region['key'];shape=region['geometry'];intent=intents[key];kind=intent['structure']
        guide=None;setting=meta.get('object_overrides',{}).get(key,{})
        if kind in ('surface','spread','axis','spot') and setting.get('method')=='guided' and setting.get('guide'):
            guide=LineString(guide_coordinates(setting['guide'],meta['design_width_mm'],meta['design_height_mm']))
            kind='guide'
        sewn=by_key[key];reason=None
        if kind=='line' and not sewn.intersection({'单针走针','三重走针','豆针走针'}):
            reason='原稿是连续细线，当前面针没有按线条中心行进。请试用走针并核对转弯。'
        elif kind=='band' and '缎面针' not in sewn:
            reason='窄带有两条可跟随的边，当前针法没有形成横跨两侧的缎面。请核对列宽与转向。'
        elif kind in ('surface','spread') and sewn and sewn.issubset({'单针走针','三重走针','豆针走针'}):
            reason='这里有连续的面宽，中心走针无法表现整片。可按结构填针或拆开设置。'
        entry={'structure':kind,'methods':sorted(sewn)}
        errors=defaultdict(list);missed=0
        probes=direction_probes(shape,kind,line_width,intent,guide)
        if kind=='surface' and attached[key]:
            kind='compound';entry.update(structure=kind,attached_bands=len(attached[key]))
            probes=((pt,v,i) for i,band in enumerate(attached[key])
                    for pt,v,_ in direction_probes(band,'band',line_width,intent,None))
        for point,vector,group in probes:
            x,y=np.rint(np.asarray(point)/precision).astype(int)
            if not (0<=y<surface.shape[0] and 0<=x<surface.shape[1]):continue
            angle=int(angles[y,x])-1
            if surface[y,x]!=region['color']+1 or angle<0:
                missed+=1;continue
            theta=math.degrees(math.atan2(vector[1],vector[0]))
            errors[group].append(abs((angle-theta+90)%180-90))
        samples=sum(errors.values(),[])
        if len(samples)>=6:
            tolerance={'line':35,'band':40,'compound':40,'spread':24,'axis':30,'guide':35}[kind]
            groups=[float(np.mean(np.asarray(values)<=tolerance)) for values in errors.values()]
            entry.update(samples=len(samples),missing_samples=missed,aligned_percent=round(100*float(np.mean(np.asarray(samples)<=tolerance)),1),
                         median_error_deg=round(float(np.median(samples)),1))
            if reason is None:
                if kind=='spread' and (len(groups)<3 or min(groups)<.55 or entry['aligned_percent']<72):
                    reason='落针方向没有随两侧逐渐展开。可恢复自动分区转向，或按原稿指定纹理。'
                elif kind=='compound' and (len(groups)!=len(attached[key]) or min(groups)<.6 or
                                           any(len(v)<6 for v in errors.values())):
                    reason='连在大色块上的细长分支没有足够的横向落针，请核对局部针向。'
                elif kind in ('line','band') and entry['aligned_percent']<60:
                    reason=('落针没有沿线条的转折连贯行进。' if kind=='line' else '多处可见针迹没有横跨窄带两侧。')+'请核对局部针向。'
                elif kind in ('axis','guide') and entry['aligned_percent']<70:
                    reason='实际针向偏离手绘方向曲线。' if kind=='guide' else '实际针向偏离这组色面各自的伸展方向。'
        elif kind in ('spread','guide','axis','compound') or (kind in ('line','band') and missed>=6):
            reason=reason or '可读回的同色针向不足，请核对这块是否被遮住或没有落针。'
        results[key]=entry
        if reason:issues.append({'region_key':key,'color':region['color'],'reason':reason})
    return {'checked_regions':len(results),'direction_regions':sum('samples' in v for v in results.values()),
            'issues':issues,'regions':results}


def review_needle_geometry(view, regions, source_classes=None, intents=None):
    precision=max(.03,max(view['width'],view['height'])/2400)
    shape=(int(np.ceil(view['height']/precision))+2,int(np.ceil(view['width']/precision))+2)
    line_width=view['meta']['preview_thread_width_mm']
    if intents is None:intents=region_recommendations(regions,line_width)
    if source_classes is not None:
        # Adjacent vector contours are traced independently. A sliver between
        # two opaque colours is not an intended opening in the confirmed art.
        # Sample the same pixel centres and uniform scale as extract_geometries.
        meta=view['meta']
        source_height,source_width=source_classes.shape
        pixel=meta['design_width_mm']/max(1,source_width-1)
        cx,cy=meta['export_origin_mm']
        def source_axis(length, size, center):
            offset=center-(size-1)*pixel/2
            coords=np.rint((np.arange(length)*precision-offset)/pixel).astype(np.int32)
            return np.clip(coords,0,size-1),(coords>=0)&(coords<size)
        src_x,valid_x=source_axis(shape[1],source_width,cx)
        src_y,valid_y=source_axis(shape[0],source_height,cy)
    def window(p,margin=0.):
        x0,y0,x1,y1=(np.asarray(p.bounds)+[-margin,-margin,margin,margin])/precision
        return (slice(max(0,int(np.floor(y0))-1),min(shape[0],int(np.ceil(y1))+2)),
                slice(max(0,int(np.floor(x0))-1),min(shape[1],int(np.ceil(x1))+2)))
    def points(coords,region):
        return np.rint(np.asarray(coords)/precision).astype(np.int32)-np.array([region[1].start,region[0].start],np.int32)
    def mask_for(geometry,region):
        mask=np.zeros(expected[region].shape,np.uint8)
        parts=list(geometry.geoms) if hasattr(geometry,'geoms') else [geometry]
        for p in parts:
            if p.is_empty or p.geom_type!='Polygon':continue
            cv2.fillPoly(mask,[points(p.exterior.coords,region)],1)
            for hole in p.interiors:cv2.fillPoly(mask,[points(hole.coords,region)],0)
        return mask.astype(bool)
    expected=np.zeros(shape,np.uint8)
    for region in regions:
        p=region['geometry'];roi=window(p)
        expected[roi][mask_for(p,roi)]=region['color']+1
    surface=np.zeros(shape,np.uint8);angles=np.zeros(shape,np.uint8);last=None
    min_x=min_y=float('inf');max_x=max_y=-float('inf')
    thickness=max(1,round(line_width/precision))
    for x,y,command,color in view['commands']:
        current=(round(x/precision),round(y/precision))
        if command==0:
            min_x=min(min_x,x);min_y=min(min_y,y);max_x=max(max_x,x);max_y=max(max_y,y)
            if last is not None:
                cv2.line(surface,last,current,color+1,thickness,lineType=cv2.LINE_8)
                dx,dy=current[0]-last[0],current[1]-last[1]
                if dx or dy:
                    angle=round(math.degrees(math.atan2(dy,dx)))%180
                    cv2.line(angles,last,current,angle+1,thickness,lineType=cv2.LINE_8)
        last=current
    issues=[];checked=0;below_resolution=[];witnesses={}
    thread=view['meta'].get('thread_spec')
    prove=(view['meta'].get('fabric')=='twill' and thread in ('75D','105D') and len(regions)<=160)
    for region in regions:
        p=region['geometry'];ci=region['color']+1
        center=stroke_center(p) if intents[region['key']]['structure']=='line' else None
        roi=window(p,2*line_width if prove else 0.);target=expected[roi];top=surface[roi]
        # Ignore normal edge allowance when judging lost interior shape.
        if center is not None:
            core=np.zeros(target.shape,np.uint8)
            cv2.polylines(core,[points(center.coords,roi)],False,1,1)
            core=core.astype(bool)
        else:
            core=mask_for(p.buffer(-line_width*.6,quad_segs=4),roi)
        if p.area<2 and center is None and core.sum()*precision**2<line_width**2:
            below_resolution.append(region['key'])
            continue
        checked+=1
        missing=float(np.count_nonzero(core & (top!=ci))/max(1,core.sum()))
        # Test negative space inside the motif's envelope. Other intended
        # same-colour objects are excluded, as are transparent background gaps.
        gaps=p.convex_hull.difference(p).buffer(-line_width*.5,quad_segs=4)
        gap_mask=mask_for(gaps,roi)&(target!=0)&(target!=ci)
        gap_area=float(gap_mask.sum()*precision**2)
        blocked=float(np.count_nonzero(gap_mask & (top==ci))/max(1,gap_mask.sum()))
        # A transparent enclosed cutout should show cloth even when no other
        # thread colour has been assigned to the opening.
        cutouts=np.zeros(target.shape,bool)
        parts=p.geoms if p.geom_type=='MultiPolygon' else (p,)
        for part in parts:
            for ring in part.interiors:
                cutouts|=mask_for(Polygon(ring).buffer(-line_width*.5,quad_segs=4),roi)
        cutouts &= target==0
        if source_classes is not None and cutouts.any():
            rows,cols=roi
            opaque=source_classes[np.ix_(src_y[rows],src_x[cols])]!=255
            opaque&=valid_y[rows,None]
            opaque&=valid_x[None,cols]
            cutouts&=~opaque
        cutout_blocked=float(np.count_nonzero(cutouts & (top!=0))/max(1,cutouts.sum()))
        reasons=[]
        if missing>.18:reasons.append('主体内部缺色或被后绣颜色遮住')
        if prove and center is None and small_round_spot(p,line_width):
            disk=mask_for(p,roi)
            wanted=disk&(target==ci)
            if wanted.any() and (top[wanted]==ci).mean()<.85 and missing<=.18:
                reasons.append('小圆点边缘缺色，实际针迹未形成完整圆点')
            # Allow half a strand beyond the artwork, and skip the outside
            # check if another same-colour object could account for that ink.
            if not np.any((target==ci)&~disk):
                rim=mask_for(p.buffer(1.5*line_width,quad_segs=4),roi)&~mask_for(p.buffer(.5*line_width,quad_segs=4),roi)
                if rim.any() and (top[rim]==ci).mean()>.20:
                    reasons.append('小圆点外侧有过多同色针迹，可能挤占周围留白或邻色')
        if gap_area>.35 and blocked>.35:reasons.append('花纹间隙被针迹挤占，可能糊成一块')
        if cutouts.sum()*precision**2>.35 and cutout_blocked>.35:reasons.append('应露出底布的镂空被针迹盖住')
        if reasons:issues.append({'region_key':region['key'],'color':region['color'],'area_mm2':round(p.area,3),
                                 'interior_mismatch_percent':round(missing*100,1),'gap_blocked_percent':round(blocked*100,1),
                                 'cutout_blocked_percent':round(cutout_blocked*100,1),
                                 'reasons':reasons})
        if prove and not reasons:
            evidence=inspect_feature_witnesses(p,ci,target,top,lambda g:mask_for(g,roi),precision,line_width,thread)
            if evidence:witnesses[region['key']]=evidence
    meta=view['meta'];envelope=None
    if min_x<=max_x and meta.get('export_origin_mm') and meta.get('design_width_mm') and meta.get('design_height_mm'):
        cx,cy=meta['export_origin_mm'];width=meta['design_width_mm'];height=meta['design_height_mm']
        sides={'左':cx-width/2-min_x,'右':max_x-cx-width/2,'上':cy-height/2-min_y,'下':max_y-cy-height/2}
        fabric=FABRICS.get(meta.get('fabric'),FABRICS['twill'])
        pulls=[o.get('pull_compensation_mm',0) or 0 for o in meta.get('planned_objects',[])]
        # Inspect beyond the chosen compensation, half a thread, and DST rounding.
        allowance=round(max(fabric['pull_fill'],fabric['pull_satin'],*pulls)+line_width/2+.05,3)
        envelope={'width_mm':round(max_x-min_x,2),'height_mm':round(max_y-min_y,2),
                  'outside_mm':{side:round(max(0,distance),2) for side,distance in sides.items()},
                  'review_allowance_mm':allowance,'needs_review':max(sides.values())>allowance+1e-6}
    unstitched=meta.get('unstitched_regions',[])
    bridges=bridged_gaps(regions,expected,surface,precision,line_width)
    suitability=review_structure(regions,meta,intents,surface,angles,precision,line_width) if 'planned_objects' in meta else None
    return {'status':'needs_review' if issues or unstitched or bridges or (suitability and suitability['issues']) or (envelope and envelope['needs_review']) else 'trial_ready',
            'stitch_suitability':suitability,
            'needle_envelope':envelope,'checked_regions':checked,'issues':issues,
            'below_resolution_regions':below_resolution,'unstitched_regions':unstitched,'gap_bridges':bridges,
            'precision_mm':round(precision,4),'reference_width_mm':line_width,'physically_sewn':False,
            'feature_witnesses':witnesses,
            'note':'按真实落针检查局部形状与间隙；几何预判不模拟线材压扁、拉缩及底布变形，未实绣。'}
