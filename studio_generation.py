"""Images API transport and reference contract, matching atelier-flow's workflow.

Images are passed as original files; annotations are a separate transparent
markup layer, never a replacement for the reference. Credentials stay local.
"""
from pathlib import Path
import base64, io, ipaddress, math, mimetypes, re, socket, time
from urllib.parse import urlsplit
import httpx
from PIL import Image, ImageDraw

MODELS = ['gpt-image-2.6-flare', 'gpt-image-2.6-sunburst', 'gpt-image-2.5-flare', 'gpt-image-2.5-sunburst']
SIZES = {
    '1K': dict(zip(['1:1','4:3','3:4','3:2','2:3','16:9','9:16'], ['1024x1024','1152x864','864x1152','1248x832','832x1248','1280x720','720x1280'])),
    '2K': dict(zip(['1:1','4:3','3:4','3:2','2:3','16:9','9:16'], ['2048x2048','2048x1536','1536x2048','2400x1600','1600x2400','2560x1440','1440x2560'])),
    '4K': dict(zip(['1:1','4:3','3:4','3:2','2:3','16:9','9:16'], ['2880x2880','3264x2448','2448x3264','3504x2336','2336x3504','3840x2160','2160x3840'])),
}
LABELS = ['图一','图二','图三','图四','图五','图六','图七','图八']
MAX_IMAGE = 30*1024*1024

def image_options(value=None):
    value = {} if value is None else value
    if not isinstance(value,dict): raise ValueError('生图设置无效。')
    options = {k:value.get(k,d) for k,d in [('model',MODELS[0]),('ratio','1:1'),('resolution','1K'),('quality','medium'),('complexity','medium')]}
    if any(not isinstance(v,str) for v in options.values()):raise ValueError('生图设置无效。')
    if options['model'] not in MODELS: raise ValueError('请选择支持的生图模型。')
    if options['resolution'] not in SIZES or options['ratio'] not in SIZES[options['resolution']]: raise ValueError('图片比例或清晰度无效。')
    if options['quality'] not in ['low','medium','high','auto'] or options['complexity'] not in ['low','medium','high']: raise ValueError('画质或复杂度无效。')
    return options

def checked_marks(value):
    if value is None:return []
    if not isinstance(value,list) or len(value)>8:raise ValueError('最多标注8处修改区域。')
    result=[]
    for mark in value:
        if not isinstance(mark,dict):raise ValueError('局部标注无效。')
        try:x,y,r=[float(mark[k]) for k in ['x','y','radius']]
        except (ValueError,TypeError,KeyError):raise ValueError('局部标注无效。')
        if not all(math.isfinite(v) for v in [x,y,r]) or not 0<=x<=1 or not 0<=y<=1 or not .015<=r<=.5:raise ValueError('局部标注超出图片范围。')
        text=mark.get('text','')
        if not isinstance(text,str) or not text.strip() or len(text)>1000:raise ValueError('请填写每一处圈选的修改要求（1000字以内）。')
        result.append({'x':round(x,5),'y':round(y,5),'radius':round(r,5),'text':text.strip()})
    return result

def reference_prompt(prompt,count,marks):
    # Slot references follow the visible ordering, as in 绘流. Missing slots
    # must fail before submission instead of silently targeting another image.
    for token in re.findall(r'@?图([一二三四五六七八]|[1-8])(?![一二三四五六七八九十百千万0-9])',prompt):
        index='一二三四五六七八'.find(token) if not token.isdigit() else int(token)-1
        if index>=count:raise ValueError(f'{LABELS[index]}还没有添加，请补齐参考图或删除引用。')
    prefix='参考图片依次对应：'+'；'.join(f'{LABELS[i]}=第{i+1}张图片' for i in range(count))+'。\n' if count else ''
    if count:prefix+='图一是本次编辑的主体母稿；其余图片仅按用户指定用途参考，不擅自替换图一的主体。\n'
    if marks:
        if not count:raise ValueError('请先选择要圈选修改的图片。')
        prefix+='只修改图一中透明标注图层圈定的区域。圈线、编号仅用于定位，不要画进结果。保留其余区域的构图、颜色、位置与细节。\n'
        prefix+='\n'.join(f'标记{i+1}（横向{m["x"]:.1%}，纵向{m["y"]:.1%}）：{m["text"]}' for i,m in enumerate(marks))+'\n'
    return prefix+prompt

def markup_bytes(source,marks):
    with Image.open(source) as im:size=im.size
    layer=Image.new('RGBA',size);d=ImageDraw.Draw(layer);scale=min(size)
    for i,m in enumerate(marks):
        x,y,r=m['x']*size[0],m['y']*size[1],m['radius']*scale
        d.ellipse((x-r,y-r,x+r,y+r),outline='#8c62e8',width=max(3,round(scale*.004)))
        bx,by=x-r*.72,y-r*.72;br=scale*.025
        d.ellipse((bx-br,by-br,bx+br,by+br),fill='#7954d6')
        # DejaVu/OS fonts are not required; the numeric label has a scalable
        # Pillow default on current Pillow, with a fallback for older versions.
        from PIL import ImageFont
        try:font=ImageFont.load_default(size=max(12,round(scale*.028)))
        except TypeError:font=ImageFont.load_default()
        d.text((bx,by),str(i+1),font=font,fill='white',anchor='mm')
    b=io.BytesIO();layer.save(b,'PNG');return b.getvalue()

def choose_matte(paths):
    """Choose an auxiliary canvas color far from the supplied artwork colors."""
    import numpy as np
    samples=[]
    for path in paths:
        with Image.open(path) as im:
            im=im.convert('RGBA');im.thumbnail((100,100));a=np.array(im)
        samples.extend(a[a[:,:,3]>200,:3][::5].tolist())
    candidates=[(255,0,255),(0,255,0),(0,255,255),(255,0,0),(0,0,255)]
    if not samples:return '#00FF00'
    a=np.array(samples,dtype=float)
    chosen=max(candidates,key=lambda c:float(np.quantile(np.linalg.norm(a-np.array(c),axis=1),.01)))
    return '#'+''.join(f'{v:02X}' for v in chosen)

def _checked_remote(url):
    p=urlsplit(url)
    if p.scheme!='https' or not p.hostname or p.username or p.password:raise ValueError('生图服务返回的图片地址无效。')
    try:addresses=socket.getaddrinfo(p.hostname,p.port or 443,type=socket.SOCK_STREAM)
    except OSError:raise ValueError('生成图片地址暂时无法解析，请稍后重试。')
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):raise ValueError('生图服务返回的图片地址不可用。')

def request_image(config,prompt,options,references,marks=None):
    options=image_options(options);marks=checked_marks(marks)
    base=config['OPENAI_BASE_URL'].rstrip('/');base=base if base.endswith('/v1') else base+'/v1'
    payload={'model':options['model'],'prompt':prompt,'size':SIZES[options['resolution']][options['ratio']],'quality':options['quality'],'n':1}
    if re.search(r'gpt-image-2\.[56]',options['model']):payload['complexity']=options['complexity']
    files=[]
    if references:
        name='image' if len(references)==1 else 'image[]'
        for i,path in enumerate(references):
            path=Path(path);files.append((name,(f'image-{i+1}{path.suffix}',path.read_bytes(),mimetypes.guess_type(path.name)[0] or 'image/png')))
        if marks:files.append(('markup',('markup.png',markup_bytes(references[0],marks),'image/png')))
    began=time.perf_counter()
    # No background/output_format extension: use the same supported fields and
    # original-file multipart convention as atelier-flow. Never auto-retry a
    # paid generation when its completion state is unknown.
    try:
        with httpx.Client(timeout=httpx.Timeout(240,connect=20),follow_redirects=False) as client:
            response=client.post(base+('/images/edits' if references else '/images/generations'),headers={'Authorization':'Bearer '+config['OPENAI_API_KEY']},**({'data':payload,'files':files} if files else {'json':payload}))
            if not response.is_success:
                raise ValueError({401:'生图服务密钥无效，请检查本地配置。',403:'生图服务拒绝了当前模型。',413:'参考图片超出服务接收上限，请减少图片。',429:'生图服务繁忙或额度不足，请稍后重试。'}.get(response.status_code,f'生图服务未完成请求（{response.status_code}），请稍后重试。'))
            try:data=response.json();item=data['data'][0]
            except (ValueError,KeyError,TypeError,IndexError):raise ValueError('生图服务没有返回可用图片。')
            encoded=item.get('b64_json')
            if encoded:
                if not isinstance(encoded,str) or len(encoded)>MAX_IMAGE*4/3+4:raise ValueError('生成图片过大。')
                try:raw=base64.b64decode(encoded,validate=True)
                except ValueError:raise ValueError('生成图片数据不完整。')
            else:
                url=item.get('url','');_checked_remote(url)
                with client.stream('GET',url,timeout=30) as download:
                    if not download.is_success:raise ValueError('生成图片下载失败，请稍后重试。')
                    chunks=[];length=0
                    for chunk in download.iter_bytes():
                        length+=len(chunk)
                        if length>MAX_IMAGE:raise ValueError('生成图片过大。')
                        chunks.append(chunk)
                    raw=b''.join(chunks)
    except httpx.TimeoutException:raise ValueError('生图等待超时，未自动重复提交。请稍后再试。')
    except httpx.HTTPError:raise ValueError('生图服务连接中断，未自动重复提交。请稍后再试。')
    if len(raw)>MAX_IMAGE:raise ValueError('生成图片过大。')
    usage=data.get('usage')
    return raw,{'provider':'atelier-flow-images','model':options['model'],'size':payload['size'],'quality':options['quality'],'complexity':options['complexity'],'reference_count':len(references),'has_markup':bool(marks),'generation_seconds':round(time.perf_counter()-began,2),'usage':usage if isinstance(usage,dict) else None}
