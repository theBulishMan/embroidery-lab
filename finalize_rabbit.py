from pathlib import Path
import json, math, collections, io, base64, csv, argparse
import numpy as np
from PIL import Image, ImageDraw
import pyembroidery as pe

BASE=Path(__file__).parent/'rabbit-test-20261114'
parser=argparse.ArgumentParser()
parser.add_argument('--out',type=Path,default=BASE)
args=parser.parse_args();B=args.out.resolve()
meta=json.loads((B/'design.json').read_text())
dst=pe.read(str(B/'machine/rabbit.dst'))
pes=pe.read(str(B/'machine/rabbit.pes'))
def sewn(p):return np.array([[s[0],s[1]] for s in p.stitches if s[2]&255==pe.STITCH],dtype=float)
a,b=sewn(dst),sewn(pes)
assert a.shape==b.shape and np.allclose(a-a.min(0),b-b.min(0),atol=.001),'Machine stitch geometry mismatch'
assert len(meta['colors'])<=9
counts=collections.Counter(s[2]&255 for s in dst.stitches)
bounds=dst.bounds();commands=[];ci=0;prefix=[];n=0;lengths=[];previous=None;by_color=[0]*4
for x,y,raw in dst.stitches:
    cmd=raw&255
    if cmd==pe.COLOR_CHANGE:ci+=1
    if cmd==pe.STITCH:
        n+=1;by_color[ci]+=1
        if previous is not None:lengths.append(math.hypot(x-previous[0],y-previous[1])/10)
    commands.append([round((x-bounds[0])/10+5,3),round((y-bounds[1])/10+5,3),cmd,min(ci,3)])
    prefix.append(n);previous=(x,y)
assert ci==3 and n>1000
assert max(lengths)<6.1
stats={'stitch_count':n,'stitches_by_color':by_color,'unique_thread_colors':4,'color_changes':counts[pe.COLOR_CHANGE],
       'trims_dst':counts[pe.TRIM],'jump_commands_dst':counts[pe.JUMP],
       'dimensions_mm':[round((bounds[2]-bounds[0])/10,2),round((bounds[3]-bounds[1])/10,2)],
       'max_stitch_mm':round(max(lengths),3),'sew_geometry_dst_pes_equal':True,'machine_tested':False,'physically_sewn':False}
(B/'validation.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2))
view={'meta':meta,'stats':stats,'commands':commands,'prefix':prefix,'width':max(meta['page_width_mm'],stats['dimensions_mm'][0]+10),
      'height':max(meta['page_height_mm'],stats['dimensions_mm'][1]+10)}
(B/'stitch-data.json').write_text(json.dumps(view,ensure_ascii=False,separators=(',',':')))

# Display stroke width is an illustration parameter, not a measured diameter.
S=15; W=round(view['width']*S);H=round(view['height']*S)
im=Image.new('RGB',(W,H),(241,237,228));dr=ImageDraw.Draw(im)
for x in range(0,W,5):dr.line((x,0,x,H),fill=(234,231,222),width=1)
for y in range(0,H,5):dr.line((0,y,W,y),fill=(247,244,237),width=1)
visual_width=meta['row_spacing_mm']*1.02
last=None
for x,y,cmd,color in commands:
    here=(x*S,y*S)
    if cmd==pe.STITCH and last is not None:
        col=meta['colors'][color]['thread']['rgb']
        dark=tuple(max(0,int(v*.73)) for v in col)
        light=tuple(min(255,int(v+(255-v)*.22)) for v in col)
        shift=(.045*S,.065*S)
        dr.line((last[0]+shift[0],last[1]+shift[1],here[0]+shift[0],here[1]+shift[1]),fill=dark,width=max(1,round(visual_width*1.08*S)))
        dr.line((last[0],last[1],here[0],here[1]),fill=tuple(col),width=max(1,round(visual_width*S)))
        dr.line((last[0]-.04*S,last[1]-.04*S,here[0]-.04*S,here[1]-.04*S),fill=light,width=max(1,round(.07*S)))
    last=here
im.save(B/'stitch-preview.png')

with (B/'thread-order.csv').open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.writer(f);w.writerow(['绣序','颜色','针数','线规格','试排间距mm','演示品牌系列','线号','线名','RGB','备注'])
    for i,c in enumerate(meta['colors']):w.writerow([i+1,c['name'],by_color[i],meta['thread_spec'],meta['row_spacing_mm'],'Madeira Rayon',c['thread']['code'],c['thread']['name'],c['thread']['hex'],'试排参数；须按实体线卡和试绣校准'])

seg=json.loads((BASE/'segmentation.json').read_text());crop=seg['crop_px']
thumb=Image.open(BASE/'original.jpg').convert('RGB').crop(crop)
thumb.thumbnail((680,760));buf=io.BytesIO();thumb.save(buf,format='JPEG',quality=85)
view['originalDataUri']='data:image/jpeg;base64,'+base64.b64encode(buf.getvalue()).decode()
view['sourceCrop']=crop
active=json.loads((BASE/'active-master.json').read_text())
r=Image.open(BASE/active['master']).convert('RGBA');box=active['bounds_px'];r=r.crop((box[0],box[1],box[2]+1,box[3]+1));r.thumbnail((560,680));rb=io.BytesIO();r.save(rb,format='PNG')
view['repairedDataUri']='data:image/png;base64,'+base64.b64encode(rb.getvalue()).decode()
(B/'viewer-data.json').write_text(json.dumps(view,ensure_ascii=False,separators=(',',':')))
report=f'''# 兔子原图分色与试排

2026-11-14。当前版本以用户最后选定并上传的 user-approved-master.png 为唯一母版，保持该稿轮廓、五官和比例，再提取4色矢量和针迹。此前调用 gpt-image-2.6-flare 的修复候选另存为 ai-repaired-v2.png，不再用于当前排针。
从 user-approved-master.png 提取黑色轮廓、浅黄底面、粉色细节和白色高光，生成 {len(meta['separated_objects'])} 个可编辑色块。
旧粗分色参考使用人工标注主体范围；新版本使用用户选定稿的自身比例，不再按原照片毛边提取，也不把选定稿拉伸到照片比例；网页叠图以选定稿为对照。
照片、旧粗分色和修复候选均保留供参考。选定稿中的渐变被归并为4种平色；输出与选定稿的形状对照，针法为本次试排设置。

## 尺寸、线规格与针数

- 设定尺寸：{meta['design_width_mm']} × {meta['design_height_mm']} mm。60 mm是初始演示宽度，实物尺寸尚未提供。
- 当前线规格选项：{meta['thread_spec']}；试排间距：{meta['row_spacing_mm']} mm。
- 75D / 105D为用户指定线粗选项。默认0.35 / 0.42 mm为本演示可调试排预设，未由用户线材厂家校准，不表示实际线径或厂家推荐。
- 针数：{n:,} 条落针指令，来自重新读取导出的DST，包含打底、主绣和补针，不计跳针、剪线和换色指令。
- 各色针数：{by_color}；合计已核对。4色，3次换色，{counts[pe.TRIM]}次剪线指令。
- 针迹范围（含补偿）：{stats['dimensions_mm'][0]} × {stats['dimensions_mm'][1]} mm；最长单针{stats['max_stitch_mm']} mm。
- DST / PES逐针坐标一致（平移归一化比较）。动画和静态效果使用同一份读回针迹。
- 调整宽度、线规格或间距会重新排针；不是对原针迹等比放大或用面积公式估针数。

## 配色与针法

黄色底面填针与打底，粉色和五官填针，黑色外圈缎面针与转角补针，白色高光最后。
针法是本次试排设置，并非对照片原版针法的恢复。预览线宽仅为屏幕表现。
Madeira Rayon是演示色库，使用CIEDE2000匹配；线粗选项不意味着已确认同品牌同规格产品。
实际装线请核对实体线卡和thread-order.csv。PES自身调色板可能改变屏幕显示色；DST不包含实际线色。

## 文件

- rabbit-layers.svg：选定平面稿分离的矢量色块。
- rabbit-embroidery.svg：带针法参数的工程。
- machine/rabbit.dst、rabbit.pes：当前参数的实验机绣文件。
- stitch-preview.png：实际针迹绘制的效果。
- validation.json：针数及机器格式读回检查。

尚未确认机型、底布、衬布、实际线材，也未试绣；针数是这套试排参数的计算结果，后续修版会变化。

线粗与密度参考：https://www.madeirausa.com/_resources/common/userfiles/file/Thread/Misc%20thread/Madeira%20USA%20Threads_Digitizing.pdf
参考流程：https://myweavo.com/create 、https://myweavo.com/help
参考演示：https://xhslink.cn/o/ADbupCmwe2t
'''
(B/'README.md').write_text(report)
print(json.dumps(stats,ensure_ascii=False))
