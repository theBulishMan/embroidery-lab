from pathlib import Path
import json, re, sys, math, itertools
import numpy as np
from lxml import etree as ET
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union
import pyembroidery as pe

sys.path.insert(0, str(Path.cwd()))
from studio_craft import rail_coverage

base = Path('studio-data/projects/25c550742a3c43bb/v-338236c5d225/plans')
result = {'sample': '20 mm yellow patch', 'physical_sewout': False, 'plans': {}}
for version, key in [('v6', 'p-5046c365ac7cd0'), ('v7', 'p-e51bf0ae4ad045')]:
    folder = base/key
    preview = json.loads((folder/'preview.json').read_text())
    root = ET.parse(str(folder/'embroidery.svg')).getroot()
    nodes = [e for e in root if e.tag.endswith('path')]
    trimmed = [e for e in nodes if e.get('{http://inkstitch.org/namespace}trim_after') == 'true']
    groups, group, lengths, previous = [], [], [], None
    for x, y, raw in pe.read(str(folder/'design.dst')).stitches:
        cmd = raw & 255
        if cmd == pe.STITCH:
            group.append([x/10, y/10])
            lengths.append(0 if previous is None else math.hypot(x-previous[0], y-previous[1])/10)
        if cmd == pe.TRIM and group:
            groups.append((group, lengths)); group, lengths = [], []
        previous = (x, y)
    if group: groups.append((group, lengths))
    assert len(groups) == len(trimmed), (len(groups), len(trimmed))
    items = []
    for node, (points, lengths) in zip(trimmed, groups):
        a = np.array(lengths)
        item = {'id': node.get('id'), 'role': node.get('data-role'),
                'needles': len(points), 'zero_length_commands': int((a < 1e-8).sum()),
                'positive_stitches_below_0_3_mm': int(((a > 1e-8) & (a < .3-1e-8)).sum()),
                'positive_stitches_below_0_5_mm': int(((a > 1e-8) & (a < .5-1e-8)).sum()),
                'length_quantiles_mm': np.round(np.quantile(a[a>1e-8], [.05,.5,.95]), 3).tolist()}
        items.append(item)
    result['plans'][version] = {'plan_id': key, 'stats': preview['stats'], 'objects': items,
                               'short_stitch_note': 'Includes underlay, edge turns and lock stitches; audit markers, not automatic defects.'}

root = ET.parse(str(base/'p-e51bf0ae4ad045'/'embroidery.svg')).getroot()
satins, segments = [], []
for node in root:
    if node.get('{http://inkstitch.org/namespace}satin_column') != 'true': continue
    parts = node.get('d').strip().split('M')[1:]
    rails = [np.array([[float(v) for v in pair.split(',')] for pair in re.findall(r'[-\d.]+,[-\d.]+', part)]) for part in parts]
    assert len(rails) == 2 and len(rails[0]) == len(rails[1])
    aa, bb = rails
    advances = [np.linalg.norm(np.diff(r, axis=0), axis=1) for r in rails]
    directions = np.unwrap(np.arctan2((bb-aa)[:,1], (bb-aa)[:,0]))
    turns = np.abs(np.diff(directions))*180/np.pi
    ratio = np.minimum(*advances) / np.maximum(np.maximum(*advances), 1e-8)
    rungs = [LineString([a,b]) for a,b in zip(aa,bb)]
    crossings = sum(rungs[i].crosses(rungs[j]) for i in range(len(rungs)) for j in range(i+1,len(rungs)))
    risk = [i for i in range(len(turns)) if ratio[i] < .2 or turns[i] > 15]
    row = {'id': node.get('id'), 'rail_nodes_per_side': len(aa),
           'rail_steps': len(aa)-1, 'collapsed_steps_under_0_01_mm': [int((x<.01).sum()) for x in advances],
           'strong_length_imbalance_steps_ratio_below_0_2': int((ratio<.2).sum()),
           'direction_turns_over_15_deg_per_step': int((turns>15).sum()),
           'max_direction_turn_deg': round(float(turns.max()),2), 'proper_rung_crossings': crossings,
           'risk_stations': [{'index': i, 'center': np.round((aa[i]+bb[i])/2,3).tolist(),
                              'turn_deg': round(float(turns[i]),2), 'advance_ratio': round(float(ratio[i]),3)} for i in risk]}
    satins.append(row)
    segments.append((node.get('id'), rail_coverage(aa,bb), aa, bb))

border = [(name, p) for name,p,_,_ in segments if name.startswith('c4-region3-')]
overlaps = [{'a':a, 'b':b, 'area_mm2':round(pa.intersection(pb).area,4)}
            for (a,pa),(b,pb) in itertools.combinations(border,2) if pa.intersection(pb).area>.005]
result['rail_audit'] = {'objects':satins, 'border_object_overlaps':overlaps,
    'method_note':'Geometry before pull compensation. The 0.2 advance ratio and 15 degree turn are diagnostic cutoffs selected for this review, not manufacturing standards. Unequal rails can be valid on curves; these stations require local spacing/shortening review.'}
out = Path('/tmp/embroidery-deep-review')
out.mkdir(parents=True, exist_ok=True)
(out/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({v:{'needles':p['stats']['stitch_count'], 'short_0_3':sum(x['positive_stitches_below_0_3_mm'] for x in p['objects']), 'short_0_5':sum(x['positive_stitches_below_0_5_mm'] for x in p['objects']), 'objects':p['objects']} for v,p in result['plans'].items()},ensure_ascii=False))
print('RAILS',json.dumps([{k:v for k,v in x.items() if k!='risk_stations'} for x in satins],ensure_ascii=False))
print('OVERLAPS',json.dumps(overlaps))

# An analytical vector diagram of actual rail control geometry, not a sew-out or a thread render.
paths = []
colors = ['#2563eb','#0f766e','#9333ea','#ca8a04','#c026d3','#b45309']
for index,(name,p,aa,bb) in enumerate(segments):
    if not name.startswith('c4-region3-'): continue
    color = colors[len(paths)%len(colors)]
    path = lambda r:'M'+' L'.join(f'{x:.4f},{y:.4f}' for x,y in r)
    shape = '<path d="'+path(p.exterior.coords)+' Z" fill="'+color+'" fill-opacity=".13"/>' if p.geom_type=='Polygon' else ''
    lines = ''.join(f'<line x1="{a[0]}" y1="{a[1]}" x2="{b[0]}" y2="{b[1]}" stroke="{color}" stroke-width=".018"/>' for a,b in zip(aa,bb))
    rails = f'<path d="{path(aa)} {path(bb)}" fill="none" stroke="{color}" stroke-width=".055"/>'
    paths.append(shape+lines+rails)
svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1000" viewBox="3 3 24 24"><rect x="3" y="3" width="24" height="24" fill="#fffdf8"/>'+''.join(paths)+'</svg>'
(out/'actual-border-rails.svg').write_text(svg)
