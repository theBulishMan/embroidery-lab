"""Package the fixed offline benchmark evidence; no provider, network or project writes."""
import csv, hashlib, json, sys
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from PIL import Image, ImageChops, ImageDraw, ImageFont
ROOT=Path(__file__).resolve().parents[2]
q,out=map(Path,sys.argv[1:3]);out.mkdir(parents=True,exist_ok=True)
old,mid,end=(q/f for f in ('base','after','final'))
labels=dict(rabbit='兔子选定稿',bow='蝴蝶结猫选定稿',seal='海豹选定稿',yellow='黄色小绣章',bee20='蜜蜂缩小·粗线',bee20fine='蜜蜂缩小·细线',bee80='蜜蜂放大',calico='三花猫圆章',sail='帆船',pin='地图标记',medal='星形徽章',stems='枝叶',camera='相机',flower='花朵',micro='十五道细纹',bolt='闪电',mushroom='蘑菇',fish='鱼尾',notes='音符',umbrella='雨伞',tulip='郁金香',letter='镂空字母',mountain='雪山',rocket='火箭',turtle='乌龟',crescent='月牙',gear='齿轮',fox='狐狸',key='钥匙',rabbitfirst='兔子首次修复稿',bowfirst='蝴蝶结猫首次生成稿',beefirst='蜜蜂首次修复稿',sealfirst='海豹首次修复稿',acorn='橡果',magnifier='放大镜',ladybug='瓢虫',shoe='系带鞋',teapot='茶壶',harmonica='口琴')
read=lambda p: json.loads(p.read_text())
rows={k:read(end/k/'result.json') for k in labels}; a={k:read(old/k/'result.json') for k in labels if (old/k/'result.json').is_file()}
status={'trial':'可试绣','adjust':'需调整','blocked':'暂不宜试绣','preflight':'排针前拦截'}
new=[k for k,d in rows.items() if d['source']=='new_png'];arch=[k for k in rows if k not in new];blind=list(labels)[-6:]
score=lambda keys:sum(rows[k]['status']=='trial' for k in keys)
rat=lambda keys:f'{score(keys)}/{len(keys)}（{100*score(keys)/len(keys):.1f}%）'
fmt=lambda x: '' if x is None else f'{x:.3f}'
with (out/'逐项结果.csv').open('w',encoding='utf-8-sig',newline='') as f:
 w=csv.writer(f);w.writerow(['样例','标识','输入','最后独立盲测','宽 mm','线材','旧版结论','当次结论','当次试绣检查分（百分制）','模拟漏底 %','缺色或盖色 %','针数','两格式落针一致','具体原因','输入 SHA-256'])
 for k,d in rows.items():w.writerow([labels[k],k,'从 PNG 自动分色' if k in new else '从已有平面稿开始',k in blind,d['width'],d['thread'],status[a[k]['status']] if k in a else '',status[d['status']],d.get('score'),fmt(d.get('bare')),fmt(d.get('color')),d.get('stitches'),'是' if d.get('files') else '无文件','；'.join(d['reasons']),d['source_sha']])
# Include the exact starting artwork for archived fixtures without including any account metadata.
source_images={}; wanted={rows[k]['source_sha'] for k in arch}
for src in (ROOT/'studio-data/projects').glob('*/v-*/classes.npy'):
 flat=src.with_name('flat.png')
 if flat.is_file():
  digest=hashlib.sha256(src.read_bytes()).hexdigest()
  if digest in wanted:source_images[digest]=flat
assert not wanted.difference(source_images), 'Missing original flat artwork for archived fixture'
with ZipFile(out/'检查依据.zip','w',compression=ZIP_DEFLATED) as z:
 for k in rows:
  if k in arch:z.write(source_images[rows[k]['source_sha']],k+'/source-flat.png')
  for f in ('input.png','result.json','fit/fit-report.json','fit/flat.png','plan/validation.json','plan/quality-review.json','plan/threads.csv'):
   p=end/k/f
   if p.is_file(): z.write(p,k+'/'+f)
  for base,tag in [(old,'原版'),(mid,'中间版')]:
   p=base/k/'result.json'
   if p.is_file(): z.write(p,tag+'/'+k+'/result.json')
 for f in ('service-smoke/service-intake.json','tests-v25.log','tests-node-v25.log'):
  if (q/f).is_file():z.write(q/f,'工程检查/'+Path(f).name)
with ZipFile(out/'兔子与月牙-试绣文件.zip','w',compression=ZIP_DEFLATED) as z:
 for k in ('rabbit','crescent'):
  assert rows[k]['status']=='trial' and rows[k]['files']
  for file in ('design.dst','design.pes','threads.csv','stitch-preview.png','validation.json'):
   z.write(end/k/'plan'/file,f"{labels[k]}_{rows[k]['width']}mm_{rows[k]['thread']}/{file}")
 z.writestr('使用说明.txt','两件样例均以绣章布／斜纹布试排，仅完成数字检查。按标注成品宽度和线材先试绣；对照各自的 threads.csv 核对换色顺序。DST 不保存实际绣线颜色。未经过实机试绣。')
fontpath=Path('/System/Library/Fonts/Supplemental/Arial Unicode.ttf');font=lambda s: ImageFont.truetype(str(fontpath),s)
W,H=1160,1036;sheet=Image.new('RGB',(W,H),'#f5f5ee');dr=ImageDraw.Draw(sheet)
dr.text((43,23),'同一张图，改了排针后有什么区别',font=font(34),fill='#17382f');dr.text((45,79),'按导出的落针绘制  ·  斜纹布 / 绣章布预设  ·  屏幕几何检查，未实机试绣',font=font(19),fill='#486055')
cols=[44,419,794];[dr.text((x,132),l,font=font(23),fill='#172e29') for x,l in zip(cols,['目标平面稿','调整前','调整后'])]
def panel(path):
 im=Image.open(path).convert('RGBA'); bg=Image.new('RGB',im.size,'#fffdf7');bg.paste(im,(0,0),im.getchannel('A'));sample=bg.getpixel((0,0));dist=ImageChops.difference(bg,Image.new('RGB',bg.size,sample)).convert('L').point(lambda x:255 if x>22 else 0);bb=dist.getbbox();bg=bg.crop(bb) if bb else bg;bg.thumbnail((296,193),Image.Resampling.LANCZOS);return bg
for i,(k,prev,title,note) in enumerate([('crescent',mid,'月牙 · 26 mm · 75D','改铺针方向，预计漏底由 2.85% 降至 0.08%；少用了 138 针。'),('seal',old,'海豹 · 60 mm · 75D','预计漏底由 2.51% 降至 1.76%；低于本次 2% 的试绣检查线。'),('micro',old,'十五道细纹 · 20 mm · 105D','旧版删去了整排花纹却给了通过；新版保留 15 道，但细缝仍窄，提示调整。')]):
 y=175+i*278;source=(end/k/'input.png') if k in new else source_images[rows[k]['source_sha']];
 for x,p in zip(cols,(source,prev/k/'plan/stitch-preview.png',end/k/'plan/stitch-preview.png')):
  dr.rounded_rectangle((x,y,x+324,y+200),radius=12,fill='#fffdf7',outline='#d5dcd3',width=1);im=panel(p);sheet.paste(im,(x+(324-im.width)//2,y+(200-im.height)//2))
 dr.text((46,y+211),title,font=font(19),fill='#17382f');dr.text((46,y+240),note,font=font(17),fill='#465a50')
sheet.save(out/'针迹对照.png')
core=list(a); prior=sum(d['status']=='trial' for d in a.values()); passed=score(list(rows));before_false=sum(d['status']=='trial' and rows[k]['status']!='trial' for k,d in a.items()); ready=[r for r in rows.values() if r.get('files')]
report=f'''# 无人制版验收 · 2026-11-18

**本机构建**：`generic-flat-v25-concave-rows`，尺寸适配 `fit-v6-source-motifs`。以「选图、给实际宽度和线材之后不再动手，能否拿到可试绣文件」评分。固定使用绣章布／斜纹布、常规密度、最多九色；以下不代表实际订单占比，也不是成品合格概率。试绣条件不等于量产结论。

| 输入 | 无人通过／全部 | 这组结果能说明什么 |
|---|---:|---|
| 全部测试配置 | {rat(list(rows))} | 包括不同版本、尺寸、线材，拦截计失败。 |
| 新的平面 PNG | {rat(new)} | 从 PNG 分色、尺寸适配、排针、导出检查，全程无人工修图。 |
| 项目已有真实图案平面稿 | {rat(arch)} | 含蜜蜂、猫、兔子、海豹等来源；同一蜜蜂有多个尺寸，并非不同订单。部分历史平面稿本身经过人工选稿，不算本次从原始照片自动修复的验证。 |
| 最后留出的 6 张（包含在 PNG 组） | {rat(blind)} | 图、尺寸、线材预先固定，未用其结果调整本构建。 |

十级评分约定：对这组样本，过八成不改稿、无局部硬性缺陷、两种机器格式一致，才认为达到 **8 分可试绣**；要判断 **9 分可生产**，还需新订单和同尺寸、同线材、同底布的实绣对照。一个设计取得软件评分 94/100，不能推出全系统 9.4/10。对所有输入不挑图，工程成熟度估计 **7/10**。干净平面 PNG 这组达到 **8/10** 的文件级目标。复杂照片从首次 AI 出图到实绣的成功率尚无独立测量。

## 同口径前后变化

同一批 {len(core)} 个输入配置，无需调针进入试绣的数量：旧版显示 **{prior}/{len(core)}**，其中 {before_false} 份（十五道细纹）虽被放行，但实际省略整组可见花纹；新版 **{score(core)}/{len(core)}**，细纹样本已纠正。额外样本不纳入这次旧版对比。

- 月牙的实际文件在圆弧附近留出几排细缝。调整自动针向后，模拟未覆盖从 {read(mid/'crescent/result.json')['bare']:.2f}% 降至 {rows['crescent']['bare']:.2f}%，针数从 {read(mid/'crescent/result.json')['stitches']:,} 降至 {rows['crescent']['stitches']:,}。前者为本轮中间构建。
- 海豹从 {a['seal']['bare']:.2f}% 降至 {rows['seal']['bare']:.2f}%；兔子选定稿从需调整变为可试绣（{rows['rabbit']['stitches']:,} 针），初次修复稿在相同宽度仍有局部细节风险。
- 鱼尾与另一色相邻的一小段凹口恢复。十五道细纹在 20 mm / 105D 尺寸下已完整保留进适配稿；局部仍不能证明能分开，系统没有放行。
- 蜜蜂首次修复稿在 58 mm / 75D 下，尺寸适配将舍弃 {rows['beefirst']['fit_stop']['groups']} 组成组花纹（{rows['beefirst']['fit_stop']['parts']} 处）；新版约 {rows['beefirst']['seconds']:.0f} 秒就标记问题并保留原稿。早先尝试完整排针触及 300 秒预算。预检没有导出文件，不计作成功。
- 最后盲测最明显的问题：24 mm / 105D 口琴，缺失本色或被后绣颜色遮住的面积约 {rows['harmonica']['color']:.2f}%。单看露底不能发现这个问题。

## 检查口径

对完成排针的 {len(ready)} 份设计，读取 DST 与 PES 对比移动和落针，再在浏览器按实际顺序和假设铺线宽度核对几何覆盖。75D 用 0.20 mm、105D 用 0.24 mm 作为试排参考，不是线筒保证直径。要求综合检查分至少 80/100、预计未覆盖 < 2%、缺色或盖色 < 5%，且无已识别的形状、挤缝、外沿或细节风险。所有数字都是数字检查；不能推算断线率、实际布面漏底率或量产良率。绣序也会影响顶层颜色，因此两种覆盖异常分别计算，不用加密或渲染遮盖。

每个测试配置都保留了输入图、参数和输入指纹，见 [逐项结果.csv](逐项结果.csv) 与 [检查依据.zip](检查依据.zip)。一个真实 PNG 还通过实际项目建单入口自动跑完，从上传到输出一个任务；付费 AI 入口仅用替代响应测试请求和出错保护，未单独评估模型画质。本机无新实绣记录，本轮没有更新线上服务。

[查看针迹对照](针迹对照.png) · [下载兔子、月牙两份试绣样件](兔子与月牙-试绣文件.zip)
'''
(out/'验收报告.md').write_text(report);(out/'workdir.txt').unlink(missing_ok=True)
for f in out.glob('*.zip'):
 with ZipFile(f) as z:assert z.testzip() is None
with (out/'逐项结果.csv').open(encoding='utf-8-sig') as f:assert len(list(csv.reader(f)))==1+len(rows)
print(f'{passed}/{len(rows)} total, {rat(new)} clean, {rat(arch)} archived, {rat(blind)} blind, {len(ready)} readbacks')
