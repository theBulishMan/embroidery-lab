"""Conservative same-thread connections inside later sewn objects."""
import copy
import heapq
import math
import re
import subprocess

import numpy as np
from lxml import etree as ET
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points, unary_union
from shapely.prepared import prep
from shapely.strtree import STRtree

from studio_artwork import SVG, INK, STITCH
from studio_engine_process import run_engine


def order_visible_layers(objects, top_lines, contact_mm, roles=None):
    """Place a supporting colour before its inlay or touching fine line.

    The boundary of a petal group can enclose further details. Compare outer
    silhouettes to include such nested shapes. Every edge goes from a larger
    enclosing silhouette to a smaller one, so nested colours cannot cycle.
    """
    shells = [Polygon(r['geometry'].exterior) for r, _ in objects]
    if not shells:
        return objects
    areas = [p.area for p in shells]
    tree = STRtree(shells)
    before = [set() for _ in objects]
    for i, (base, stage) in enumerate(objects):
        if stage == 'border':
            continue
        for ring in base['geometry'].interiors:
            window = Polygon(ring)
            for j in tree.query(window):
                child, child_stage = objects[j]
                if (child_stage == 'border' or child['color'] == base['color']
                        or child['geometry'].area < .18 or areas[j] + .001 >= areas[i]):
                    continue
                if window.area <= areas[j]*1.45 + .04 and window.intersection(shells[j]).area >= areas[j]*.94:
                    before[j].add(i)
    for j, (detail, stage) in enumerate(objects):
        if detail['key'] not in top_lines or stage == 'border':
            continue
        p = detail['geometry']
        for i in tree.query(p.buffer(contact_mm).envelope):
            base, base_stage = objects[i]
            if (base_stage != 'border' and base['color'] != detail['color'] and areas[i] > areas[j]
                    and base['geometry'].area > p.area*3 and base['geometry'].distance(p) < contact_mm):
                before[j].add(i)
    roles = roles or {}
    if not roles and all(all(parent < child for parent in deps) for child, deps in enumerate(before)):
        return objects

    # Only ready regions can compete for the current thread. Each palette
    # has at most nine colours, so picking the next group stays bounded.
    after = [[] for _ in objects]
    pending = [len(deps) for deps in before]
    for child, deps in enumerate(before):
        for parent in deps:
            after[parent].append(child)
    ready = {}
    def enqueue(index):
        region, stage = objects[index]
        role_rank = {'base':0, 'surface':1, 'detail':2, 'edge':3}.get(roles.get(region['key']), 1)
        group = (stage == 'border', role_rank, stage == 'highlight' and not after[index],
                 region['geometry'].area < .18, region['color'])
        heapq.heappush(ready.setdefault(group, []), (not after[index], index))
    for i, count in enumerate(pending):
        if count == 0:
            enqueue(i)
    result = []
    color = None
    while ready:
        def priority(group):
            border, role_rank, highlight, micro, ci = group
            return border, role_rank, highlight, micro and ci != color, ci != color, ready[group][0]
        group = min(ready, key=priority)
        _, i = heapq.heappop(ready[group])
        if not ready[group]:
            del ready[group]
        result.append(objects[i])
        color = objects[i][0]['color']
        for child in after[i]:
            pending[child] -= 1
            if pending[child] == 0:
                enqueue(child)
    return result


def _inside_route(region, start, end, budget):
    """A visibility graph keeps every segment inside an eroded future cover.

    Sparse visibility is deliberately conservative: if no route is found,
    retain the existing trim. No raster diagonal can cut across a hole.
    """
    safe=prep(region.buffer(1e-7))
    direct=LineString([start,end])
    if safe.covers(direct):return [start,end]
    simplified=region.simplify(.045,preserve_topology=True)
    boundary=list(simplified.exterior.coords)[:-1]
    for hole in simplified.interiors:boundary.extend(list(hole.coords)[:-1])
    if len(boundary)>450:return None
    coords=np.asarray([start,end,*boundary]);tree=cKDTree(coords)
    graph=[[] for _ in coords];pairs=set()
    _,neighbors=tree.query(coords,k=min(18,len(coords)))
    for i,near in enumerate(neighbors):
        for j in near:
            if i!=j:pairs.add(tuple(sorted((i,int(j)))))
    for i in (0,1):
        for j in range(len(coords)):
            if i!=j:pairs.add(tuple(sorted((i,j))))
    for i,j in sorted(pairs):
        segment=LineString([coords[i],coords[j]])
        if segment.length<=budget and safe.covers(segment):
            graph[i].append((j,segment.length));graph[j].append((i,segment.length))
    queue=[(0.,0)];best={0:0.};parent={}
    while queue:
        distance,i=heapq.heappop(queue)
        if distance>best[i]:continue
        if i==1:
            path=[1]
            while path[-1]!=0:path.append(parent[path[-1]])
            return coords[path[::-1]].tolist()
        for j,step in graph[i]:
            d=distance+step
            if d<=budget and d<best.get(j,math.inf):
                best[j]=d;parent[j]=i;heapq.heappush(queue,(d,j))
    return None


def hidden_route(start,end,future_cover,previous_shape,margin=.06):
    """Return a covered same-color route, or keep the trim.

    Only a <=0.45 mm lead may lie on the previously sewn object. The route's
    body must lie in future fill/satin, inset from its artwork boundary. Small
    endpoint caps account for the engine's compensated start/end positions.
    These geometry tolerances are not guarantees about real fabric opacity.
    """
    a,b=Point(start),Point(end);direct=a.distance(b)
    if direct<.1 or direct>12 or future_cover.is_empty:return None
    core=future_cover.buffer(-margin)
    parts=list(core.geoms) if hasattr(core,'geoms') else [core]
    budget=min(20,max(3,direct*2.5))
    best=None
    allowed=unary_union([future_cover,previous_shape]).buffer(.02).union(a.buffer(.18)).union(b.buffer(.18))
    for region in parts:
        if region.geom_type!='Polygon' or region.distance(a)>.45 or region.distance(b)>.45:continue
        first=nearest_points(a,region)[1];last=nearest_points(b,region)[1]
        body=_inside_route(region,list(first.coords[0]),list(last.coords[0]),budget)
        if body is None:continue
        coords=[list(a.coords[0]),*body,list(b.coords[0])]
        unique=[coords[0]]
        for q in coords[1:]:
            if math.dist(q,unique[-1])>.005:unique.append(q)
        if len(unique)<2:continue
        line=LineString(unique)
        if line.length>budget or not allowed.buffer(1e-7).covers(line):continue
        if best is None or line.length<LineString(best).length:best=unique
    return best


def same_color_short_route(start,end,surface):
    """A very short join may sink into existing same-color stitching.

    Unlike hidden_route this is not claimed to be covered by later stitches.
    Never connect through exposed fabric, a different color, or a real hole.
    """
    a,b=Point(start),Point(end)
    if not .015<=a.distance(b)<=1.5:return None
    direct=LineString([start,end])
    # 0.02 mm bridges polygon/rail sampling seams, below the 0.1 mm machine
    # coordinate grid. It must not bridge an actually separated feature.
    if surface.buffer(.02).covers(direct):return [list(start),list(end)]
    core=surface.buffer(-.02)
    parts=list(core.geoms) if hasattr(core,'geoms') else [core]
    for region in parts:
        if region.geom_type!='Polygon' or max(region.distance(a),region.distance(b))>.08:continue
        first=nearest_points(a,region)[1];last=nearest_points(b,region)[1]
        body=_inside_route(region,list(first.coords[0]),list(last.coords[0]),1.6)
        if body is None:continue
        coords=[list(start),*body,list(end)];line=LineString(coords)
        if line.length<=1.6 and surface.buffer(.02).covers(line):return coords
    return None


def connection_candidate(root,shapes,engine,folder,update=None,blocks_link=None):
    """Ask the installed stitch engine for actual object endpoints, then route.

    The engine extension outputs SVG coordinates before machine-file centering.
    We only copy its endpoint proposals; artwork objects stay unchanged.
    Nothing is connected across color changes or exposed gaps.
    """
    folder.mkdir(parents=True,exist_ok=True)
    source=folder/'source.svg';ET.ElementTree(root).write(str(source),encoding='utf-8',xml_declaration=True)
    cmd=[str(engine),'--extension=jump_to_stroke','--minimum-jump-length=0',
         '--maximum-jump-length=12','--exclude-trim=false','--stitch-length=1.5',
         '--tolerance=0.03',str(source)]
    with (folder/'endpoints.svg').open('wb') as output,(folder/'endpoints.log').open('w') as errors:
        result=run_engine(cmd,stdout=output,stderr=errors,folder=folder,timeout=180,update=update)
    if result.returncode:raise ValueError('连接针路候选未生成，保留原方案。')
    generated=ET.parse(str(folder/'endpoints.svg')).getroot()
    nodes=[n for n in root if n.get('data-thread-color') is not None]
    positions={n.get('id'):i for i,n in enumerate(nodes)}
    surfaces={color:unary_union([shapes[n.get('id')] for n in nodes
              if n.get('data-thread-color')==color and n.get('id') in shapes])
              for color in {n.get('data-thread-color') for n in nodes}}
    candidate=copy.deepcopy(root);by_id={n.get('id'):n for n in candidate}
    planned=[];previous=None
    children=list(generated)
    for index,node in enumerate(children):
        if node.get('id') in positions:previous=node;continue
        if previous is None or node.tag!=f'{{{SVG}}}path' or node.get('transform'):continue
        following=next((n for n in children[index+1:] if n.get('id') in positions),None)
        if following is None:continue
        old_id,next_id=previous.get('id'),following.get('id')
        old=by_id[old_id];next_node=by_id[next_id]
        color=old.get('data-thread-color')
        if old.get(f'{{{STITCH}}}trim_after')!='true' or color!=next_node.get('data-thread-color'):continue
        if old_id not in shapes:continue
        numbers=re.findall(r'[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?',node.get('d',''))
        if len(numbers)!=4:continue
        x,y,X,Y=map(float,numbers)
        later=[shapes[n.get('id')] for n in nodes[positions[next_id]:]
               if n.get('data-thread-color')==color and n.get('id') in shapes]
        if not later:continue
        cover=unary_union(later)
        coincident=math.hypot(X-x,Y-y)<.015 and cover.distance(Point(x,y))<=.18
        coords=[[x,y],[X,Y]] if coincident else hidden_route([x,y],[X,Y],cover,shapes[old_id])
        kind='同色藏线'
        if coords is None:
            coords=same_color_short_route([x,y],[X,Y],surfaces[color]);kind='同色短接'
        if coords is None:continue
        if blocks_link is not None and blocks_link(int(color), LineString(coords)):continue
        pid=f'connect-{old_id}-to-{next_id}'
        if coincident:
            old.set(f'{{{STITCH}}}trim_after','false')
            planned.append({'id':pid,'color':int(color),'stitch_type':'共点连续','role':'共点连续',
                            'from':old_id,'to':next_id,'length_mm':0.,'path_mm':coords,
                            'emits_path':False,'coverage':'后续同色对象共点衔接'})
            continue
        path='M'+' L'.join(f'{a:.5f},{b:.5f}' for a,b in coords)
        el=ET.Element(f'{{{SVG}}}path',id=pid,d=path,fill='none',
                      stroke=old.get('stroke') or old.get('fill'),
                      **{'stroke-width':'.15','stroke-dasharray':'1 1',
                         'data-thread-color':color,'data-role':kind,
                         'data-stage':next_node.get('data-stage','body')})
        el.set(f'{{{INK}}}label','同色连接 · '+('后续针迹覆盖' if kind=='同色藏线' else '同色针面内短接'))
        for key,value in {'stroke_method':'running_stitch','running_stitch_length_mm':'1.5',
                          'running_stitch_tolerance_mm':'0.03','trim_after':'false'}.items():
            el.set(f'{{{STITCH}}}{key}',value)
        old.set(f'{{{STITCH}}}trim_after','false')
        candidate.insert(candidate.index(next_node),el)
        planned.append({'id':pid,'color':int(color),'stitch_type':'连接走针','role':kind,'emits_path':True,
                        'from':old_id,'to':next_id,'length_mm':round(LineString(coords).length,3),
                        'path_mm':coords,'coverage':('后续同色面针覆盖，起点短引线位于已绣同色区域' if kind=='同色藏线' else '同色针面内短距离连接，需实绣检查接头')})
    return candidate,planned
