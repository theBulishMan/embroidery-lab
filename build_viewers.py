from pathlib import Path
import json,re
from PIL import Image

R=Path(__file__).parent;B=R/'rabbit-test-20261114'
data=json.loads((B/'viewer-data.json').read_text())
web=(R/'web-viewer.html').read_text().replace('__DEFAULT_DATA__',json.dumps(data,ensure_ascii=False,separators=(',',':')))
(B/'index.html').write_text(web)
Path('/tmp/rabbit-web-check.js').write_text('\n'.join(re.findall(r'<script>([\s\S]*?)</script>',web)))
a=data;b=json.loads((B/'plans/w60-75d-s350/viewer-data.json').read_text())
photo=a['originalDataUri'];profiles={}
for key,item in [('105D',a),('75D',b)]:
    item=json.loads(json.dumps(item));item.pop('originalDataUri',None);item.pop('repairedDataUri',None);item['meta'].pop('planned_objects',None);profiles[key]=item
bundle={'originalDataUri':photo,'profiles':profiles}
fragment=(R/'rabbit-viewer.fragment.html').read_text().replace('__RABBIT_DATA__',json.dumps(bundle,ensure_ascii=False,separators=(',',':')))
assert len(fragment.encode())<1000000
viz=Path('/Users/chenwenjie/.codex/visualizations/2026/11/14/01a1d8bb-1b31-7d10-b65a-ed5807ceefa8/rabbit-stitch-test.html');viz.write_text(fragment)
shell=(R/'viewer-shell.html').read_text().replace('从照片到真实针迹','圆润平面稿分层与模拟').replace('machine/rabbit-60mm.','machine/rabbit.')
shell=shell.replace('DST 试验文件','105D DST 试验文件').replace('PES 试验文件','105D PES 试验文件').replace('配线单</a>','105D 配线单</a>')
shell=shell.replace('</footer>','<a href="plans/w60-75d-s350/machine/rabbit.dst" download>75D DST 试验文件</a><a href="plans/w60-75d-s350/thread-order.csv" download>75D 配线单</a></footer>')
(B/'offline-preview.html').write_text(shell.replace('__FRAGMENT__',fragment))
Path('/tmp/rabbit-inline-check.js').write_text('\n'.join(re.findall(r'<script>([\s\S]*?)</script>',fragment)))

# Export the requested four-color separation as a viewable bitmap. No generative
# changes are performed here: the geometry is the approved AI repair output.
active=json.loads((B/'active-master.json').read_text());x0,y0,x1,y1=active['bounds_px']
flat=Image.open(B/'repaired-separated.png').crop((x0,y0,x1+1,y1+1))
preview=Image.new('RGB',(flat.width+64,flat.height+64),'#fafaf6');preview.paste(flat,(32,32),flat.getchannel('A'));preview.save(B/'repaired-flat-preview.png')
seg=json.loads((B/'segmentation.json').read_text());crop=seg['crop_px'];bounds=seg['bounds_px'];original=Image.open(B/'original.jpg').crop(crop)
display_width=bounds[2]-bounds[0]+1
flat=flat.resize((display_width,round(flat.height*display_width/flat.width)),Image.Resampling.LANCZOS)
display_height=max(original.height,flat.height+40)
right=Image.new('RGB',(original.width,display_height),'#fafaf6')
right.paste(flat,(bounds[0]-crop[0],bounds[1]-crop[1]),flat.getchannel('A'))
comparison=Image.new('RGB',(original.width*2+24,display_height),'white');comparison.paste(original,(0,0));comparison.paste(right,(original.width+24,0));comparison.save(B/'original-vs-repaired.png')
print({'web_bytes':len(web.encode()),'inline_bytes':len(fragment.encode()),'stitches':{k:v['stats']['stitch_count'] for k,v in profiles.items()}})
