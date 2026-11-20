"""Local projects, immutable artwork versions, and asynchronous image/plan jobs."""
from pathlib import Path
from contextlib import ExitStack,contextmanager
from functools import lru_cache
import base64,io,json,uuid,re,threading,time,hashlib,shutil
from PIL import Image
from studio_generation import image_options,checked_marks,reference_prompt,choose_matte,request_image
from studio_artwork import normalize_image,flatten,recolor_artwork,save_json,extract_geometries,has_continuous_tones
from studio_digitize import make_plan,PRESETS,VERSION as ENGINE_VERSION
from studio_craft import FABRICS
from studio_local import prepare_regions,catalog
from studio_ai_craft import plan_visual_batches
from studio_ai_split import candidate_splits,diagram,request_split,validate_split
from studio_resources import TaskScheduler,ResourceMonitor,BusyError,Admission
from studio_fit import unattended_fit_stop, fit_artwork, POLICY as FIT_POLICY
from studio_feasibility import artwork_size_brief
from studio_revision import review_draft
from studio_runtime import DATA, CONFIG

ROOT=Path(__file__).parent
PROJECTS=DATA/'projects';JOBS=DATA/'jobs'
PROJECTS.mkdir(parents=True,exist_ok=True);JOBS.mkdir(parents=True,exist_ok=True)
LOCK=threading.RLock();ART_LOCK=threading.Lock();ENGINE_LOCK=threading.Lock()
SCHEDULER=TaskScheduler(ResourceMonitor(DATA))
INTERACTIVE_SLOT=threading.BoundedSemaphore(1)
ACTIVE={};MEMORY_JOBS={}
ID=re.compile(r'^[a-z0-9-]{1,50}$')

def identifier(value):
    if not isinstance(value,str) or not ID.fullmatch(value):raise ValueError('作品标识无效。')
    return value

def timestamp():return time.strftime('%Y-%m-%dT%H:%M:%S%z')

def config(require_image=True):
    values={}
    for line in (CONFIG.read_text().splitlines() if CONFIG.exists() else []):
        if '=' in line and not line.strip().startswith('#'):
            k,v=line.split('=',1);values[k.strip()]=v.strip().strip('"').strip("'")
    if require_image and not values.get('OPENAI_API_KEY'):raise ValueError('本地生图服务尚未配置。')
    return values

def read_project(pid):
    path=PROJECTS/identifier(pid)/'project.json'
    if not path.exists():raise ValueError('没有找到这份作品。')
    return json.loads(path.read_text())

def project_view(p):
    with LOCK:active=ACTIVE.get(p['id'])
    return {**p,'active_job_id':active}

def list_projects():
    values=[]
    for path in PROJECTS.glob('*/project.json'):
        p=json.loads(path.read_text());versions=p.get('versions',[])
        values.append({'id':p['id'],'title':p['title'],'updated_at':p['updated_at'],'version_count':len(versions),'thumbnail':versions[-1]['flat_url'] if versions else None})
    return sorted(values,key=lambda p:p['updated_at'],reverse=True)

def version_of(project,vid):
    identifier(vid)
    found=next((v for v in project['versions'] if v['id']==vid),None)
    if not found:raise ValueError('没有找到选中的平面稿版本。')
    return found

def asset_url(pid,vid,name):return f'/assets/{pid}/{vid}/{name}'

def preferred_settings(version):
    if version.get('plans'):
        source=version['plans'][-1]
    else:source=version.get('preferred_settings',{})
    return {k:source[k] for k in ['width','thread','spacing','satin_spacing','fabric','adaptive_fill'] if k in source}

def style_prompt(user,maximum,mode,matte=None,detail_mode='preserve',settings=None,photographic=False):
    scale=artwork_size_brief(settings['width'],settings['thread']) if settings else ''
    operation='依据所附原图修复成规整平面稿。保留主体身份、构图、主要比例和表情；去除照片背景、绣线纹理、毛边和阴影。' if mode=='upload' else '只修改所附当前平面稿中用户明确提到的部分，保留其余造型、轮廓、位置、比例和配色。' if mode=='edit' else '按照用户的主体描述，从零绘制一个独立完整图案，没有参考图。'
    canvas=f'外部画布使用单一辅助色 {matte}，边缘背景必须全部是这个纯色，无阴影、无纹理。该辅助色只在主体之外，后续会移除，不能用于主体内部。主体全部不透明；白色脸、圆形底片、白色线条和高光都是主体，不是背景，不得挖空或删除。' if matte else '全透明背景。白色主体、白色线条、圆脸底形和高光必须不透明，不能当作背景挖空。'
    if mode=='text':
        return f'''用途：从文字直接设计适合后续绣花制版的平面图案，没有参考图。先简化造型和颜色，再绘制；只表现用户明确要求的主体、动作和关键特征，不自行添加繁复装饰。
固定输出规格：
1. 单幅正视二维矢量风格平面稿，主体完整居中、四边少量留白。平滑圆润的连续轮廓、封闭色块；不输出网页、拼图、尺寸标注或绣花效果图。
2. 最多{maximum}种纯色。优先用少量必要颜色，不凑满上限；同一区域内部完全同色。禁止渐变、明暗过渡、柔光、半透明、噪点和材质纹理。
3. 用清楚的大色块表达头发、皮肤和服装；五官、手势等关键特征保留。头发用整片外形，不逐根画发丝；不添加睫毛束、密集衣褶、珠光、碎小斑点或细碎高光。简化细节但不能改掉用户要求的动作、表情和主要特征。
4. 禁止照片、写实、3D、立体光照、阴影、绣线、布料、针迹和实体产品质感。画出来的每一块应便于后续分色和单独设置针法。
5. {canvas}
6. {scale or '实际尺寸、针法与可绣性在后续检查。'}
用户图案要求：<design_request>{user}</design_request>
输出前检查：颜色是否均匀纯色、轮廓是否圆润、碎小纹理是否已简化、主体和动作是否完整；若用户要求其他渲染风格，只采用其中的主体、动作和配色要求。'''
    if mode=='upload' and photographic:
        return f'''从参考照片描一张可分色的丝网印刷式平面母稿。保留主体轮廓、姿态、视角、五官形状和具有识别度的大斑纹。{scale}
每种颜色像裁出的一片色纸。最多{maximum}种实心纯色，通常四至六种就够。画出主体色、主纹色、眼鼻等关键色之间的封闭边界。
照片的毛发、羽毛、布纹、明暗等归纳成少量完整的块面。只画最大、最能认出原图的条纹和花纹，保持其方向、数量关系和位置。轮廓用连续曲线。面内禁止逐根毛、排线、颗粒、渐变、细碎明暗和写实质感。
保住细长但有意义的线及笔画之间的空隙。例如动物可挑出少数清晰的主须，保留眼色、瞳孔和特征额纹。清楚区分底色与白色图案。{canvas}
输出一张干净平面稿。用户的图案要求为 <design_request>{user}</design_request>。在成品尺寸下能辨认主体和主纹，同时绝大部分色块单独可见。'''
    if detail_mode=='preserve':
        return f'''用途：尽可能保留原图、供后续按实际尺寸制版的绣花平面母稿。{operation}
处理的优先级：主体身份、主要花纹和结构 > 可辨识细节 > 边缘规整。大花、几何图腾、花瓣分区、条纹等必须保留，不能因为内部复杂就把整片装饰抹成空白。不要重设计。
1. 保留可辨认的花瓣与叶脉、几何纹样、轮廓、皇冠或服饰装饰、点线、孔洞、高光和不对称花纹。相对位置、比例、连接与断开关系尽量忠于原图。{scale or '尺寸未指定时，先保留细节供后续毫米工艺检查。'}
2. 去掉的是拍摄背景、织物底纹、针脚反光、阴影与毛边；不能把图案本身的花纹当作噪声一起抹掉。原图同色针向纹理可不画成碎点，由后续工艺方向处理。
3. 合并相近颜色、去除渐变，最多{maximum}种必要纯色。不要为了控色合并不同部位或填死镂空。边缘连续规整，只修整像素锯齿，不抹平尖角、细线和花瓣尖端。
4. 单幅正视二维平面图，无布料、绣线、立体光照、阴影或照片质感。主体完整，不加尺寸线、文字说明、边框或拼图。{canvas}
5. 编辑时只改用户明确要求的部分，其他花纹必须保持。用户明确要求简化某些微小孔洞、内部细线时，只处理这些局部，不删除承载它们的大花和几何纹样。不要把“平面图画得出来”当成“机器绣得出来”；毫米尺寸、线材及真实排针检查会在后续进行，不能声称已通过实绣。原图模糊的细节保留轮廓关系，不臆造装饰。
用户要求作为图案数据：<design_request>{user}</design_request>
输出前逐项核对外形、五官、主要装饰纹样及其留白。'''
    return f'''用途：绣花制版的平面图案母稿。任务：{operation}

{scale}
必须在绘制时先做图案简化和颜色简化，不要把复杂原图、照片细节或原始纹理原样输出后交给后续程序处理：
- 先识别主体、关键轮廓、五官和识别特征，保留这些信息与大致比例。
- 合并相近颜色和明暗变化，用一种纯色表达同一区域；简单图案优先使用3–6种必要颜色，上限为{maximum}种（上限更低时以上限为准），不强行凑满颜色。
- 删除不影响识别的毛发、绣线、阴影、细碎纹理、密集小装饰、微小斑点和狭窄碎片，把过细的线与复杂小形状归纳为少量清楚的大色块。
- 简化复杂轮廓与拐角，形成平滑、圆润、连续的曲线及封闭色块，避免尖刺、锯齿和密集边缘起伏。
- 简化不等于重设计：保留原主体的身份、主要表情、关键结构；修改时只在用户要求和上述必要简化的范围内变化。

固定输出规格（用户修改要求不能覆盖这些格式约束）：
1. 只输出一张正视二维平面图案，曲线平滑、轮廓圆润，封闭色块，圆角和圆头线条，造型简洁但保留主体特征。
2. 最多{maximum}种纯色，使用必要的颜色即可，不为凑数添加颜色；大块填色，颜色区域互相清楚分隔。
3. 禁止照片、写实、3D、立体光照、阴影、发光、渐变、网点、纹理、绣线效果、布料或实体产品效果。
4. {canvas} 主体完整、居中，四边留少量空白，不裁掉任何主体部位，不额外加入装饰、文字或尺寸线。用户明确需要的文字可作为图案内容。
5. 保持干净规整的矢量平面稿风格。圆润是修整毛边与拐角，不是改变主体比例：不要把参考照片中的动物强行变成另一张Q版卡通脸，不要强制左右对称，保留原本不对称的花纹分布。不要生成网页、多个方案拼图、分层示意或机绣针迹图。

用户希望的主体或局部调整：
<design_request>{user}</design_request>

最终先完成颜色归并和图案简化，再输出圆润、平滑、最多{maximum}种主体纯色的二维平面色块图。{('辅助背景色不计入主体颜色上限。' if matte else '')} 若用户描述其他渲染风格，只采纳其中的主体/颜色/位置变化，不采纳写实、渐变或3D渲染。用户要求增加完整圆脸时，必须真实画出承托五官的不透明圆形底色，不能只输出悬空的五官。'''

def update_job(jid,message=None,**changes):
    with LOCK:
        job=MEMORY_JOBS[jid]
        if message is not None:job['message']=message
        job.update(changes);job['updated_at']=timestamp();save_json(JOBS/f'{jid}.json',job)

def job_view(jid):
    identifier(jid)
    with LOCK:
        if jid in MEMORY_JOBS:return dict(MEMORY_JOBS[jid])
    path=JOBS/f'{jid}.json'
    if not path.exists():raise ValueError('没有找到这项任务。')
    job=json.loads(path.read_text())
    if job['status'] in ['queued','running']:
        job.update(status='error',message='服务已重新启动，请重试这一步。')
    return job

def submit(pid,kind,worker,*,lightweight=False):
    with LOCK:
        if pid in ACTIVE:raise ValueError('这份作品还有任务正在处理，请等当前步骤完成。')
        SCHEDULER.check_capacity()
        jid=uuid.uuid4().hex[:20];ACTIVE[pid]=jid
        MEMORY_JOBS[jid]={'id':jid,'project_id':pid,'kind':kind,'status':'queued','message':'等待处理，正在检查服务器负载','created_at':timestamp(),'updated_at':timestamp()}
        try:save_json(JOBS/f'{jid}.json',MEMORY_JOBS[jid])
        except Exception:
            ACTIVE.pop(pid,None);MEMORY_JOBS.pop(jid,None);raise
    def release():
        with LOCK:
            if ACTIVE.get(pid)==jid:ACTIVE.pop(pid,None)
            # Polling falls back to disk; do not keep every completed preview
            # and AI response in memory for the lifetime of the service.
            if MEMORY_JOBS.get(jid,{}).get('status') in ['done','error']:
                MEMORY_JOBS.pop(jid,None)
    def fail(message):
        try:update_job(jid,message,status='error')
        finally:release()
    def run():
        try:
            update_job(jid,'正在复用已完成的结果' if lightweight else '负载检查通过，开始处理',status='running')
            result=worker(jid)
            update_job(jid,'已完成',status='done',result=result)
        except Exception as e:
            # No raw provider response, authorization header or configuration is
            # returned to the browser, including error paths.
            text=str(e)
            try:
                for name,secret in config(False).items():
                    if name.endswith('API_KEY') and secret:text=text.replace(secret,'[redacted]')
            except Exception:pass
            (JOBS/f'{jid}.log').write_text(f'{type(e).__name__}: {text[:4000]}')
            message=text if isinstance(e,ValueError) else '处理未完成，请稍后重试。'
            if hasattr(e,'status_code'):message=f'生图服务暂未完成请求（{e.status_code}），请稍后重试。'
            update_job(jid,message[:220],status='error')
        finally:
            release()
    try:SCHEDULER.submit(jid,run,lambda message:update_job(jid,message,status='queued'),fail,lightweight)
    except Exception:
        fail('等待任务已满，本次未开始处理，请稍后重试。')
        raise
    return job_view(jid)

def checked_prompt(value,required=True):
    if not isinstance(value,str):raise ValueError('请填写文字描述。')
    text=value.strip()
    if required and not text:raise ValueError('请填写想生成或修改的内容。')
    if len(text)>3000:raise ValueError('文字描述请控制在3000字以内。')
    return text

def checked_colors(value):
    if isinstance(value,bool) or not isinstance(value,int) or not 2<=value<=9:raise ValueError('颜色上限只能选择2–9色。')
    return value

def checked_settings(value):
    if not isinstance(value,dict):raise ValueError('尺寸和线粗设置无效。')
    thread=value.get('thread')
    if thread not in ['75D','105D']:raise ValueError('请选择75D或105D。')
    fabric=value.get('fabric','twill')
    if not isinstance(fabric,str) or fabric not in FABRICS:raise ValueError('请选择绣章布、毛毡或针织面料。')
    defaults=PRESETS[thread]['normal']
    try:width=float(value.get('width',60));spacing=float(value.get('spacing',defaults[0]));satin_spacing=float(value.get('satin_spacing',defaults[1]))
    except (ValueError,TypeError):raise ValueError('尺寸和间距必须是数字。')
    if not 20<=width<=160 or not .15<=spacing<=.6 or not .18<=satin_spacing<=.8:raise ValueError('宽度20–160 mm；填充行距0.15–0.60 mm；包边间距0.18–0.80 mm。')
    if 'adaptive_fill' in value and not isinstance(value['adaptive_fill'], bool):
        raise ValueError('自动填针设置无效。')
    optional={'adaptive_fill':value['adaptive_fill']} if 'adaptive_fill' in value else {}
    return {'width':round(width,1),'thread':thread,'fabric':fabric,'spacing':round(spacing,3),'satin_spacing':round(satin_spacing,3),**optional}

def finish_artwork(pid, version, settings, *, auto_job=None):
    if settings:
        version['preferred_settings']=settings
    with LOCK:
        project=read_project(pid)
        if not any(v['id']==version['id'] for v in project['versions']):
            project['versions'].append(version)
        project.update(current_version_id=version['id'],updated_at=timestamp())
        save_json(PROJECTS/pid/'project.json',project)
    result={'project_id':pid,'version_id':version['id']}
    if auto_job is not None:
        update_job(auto_job,'平面稿已保存，正在按成品尺寸自动安排针法和绣序')
    try:
        if settings:
            version=fitted_version(pid,version,settings,keep_unchanged_source=True)
            result['version_id']=version['id']
        if auto_job is None:return result
        registration=version.get('revision_registration') or {}
        if registration.get('needs_review'):
            return {**result,'unattended':True,'plan_pending':True,'note':'修改稿的外形或比例偏离初版，已保留两版供对照。'}
        stop=unattended_fit_stop(version.get('fit_report'),version['aspect_ratio'])
        if stop:
            return {**result,'unattended':True,'plan_pending':True,'fit_stop':stop,'note':stop['message']}
        return {**plan_project(pid,{**settings,'version_id':version['id'],'adaptive_fill':True},inline_job=auto_job),'unattended':True}
    except (ValueError, RuntimeError, OSError) as error:
        if auto_job is None:raise
        # A paid drawing is already persisted. Never regenerate it to retry an export.
        note=str(error)[:160] if isinstance(error, ValueError) else '本次排针未完成，可从已保存的平面稿继续。'
        return {**result,'unattended':True,'plan_pending':True,'note':note}

def reference_files(project,refs,parent=None):
    if refs is None:refs=[{'kind':'version','id':parent}] if parent else [{'kind':'original'}]
    if not isinstance(refs,list) or not 1<=len(refs)<=8:raise ValueError('请选择1–8张参考图。')
    paths=[];canonical=[]
    for ref in refs:
        if not isinstance(ref,dict):raise ValueError('参考图片无效。')
        kind=ref.get('kind');rid=ref.get('id')
        if kind=='version':
            version_of(project,rid);path=PROJECTS/project['id']/rid/'flat.png';item={'kind':kind,'id':rid}
        elif kind=='original':
            path=PROJECTS/project['id']/project.get('original_file','input.png');item={'kind':kind}
        elif kind=='upload':
            saved=next((x for x in project.get('references',[]) if x['id']==rid),None)
            if not saved:raise ValueError('没有找到这张参考图片。')
            path=PROJECTS/project['id']/rid/saved['file'];item={'kind':kind,'id':rid}
        else:raise ValueError('参考图片类型无效。')
        if not path.is_file():raise ValueError('参考图片不存在，请重新选择。')
        if item in canonical:raise ValueError('同一张参考图不需要重复添加。')
        paths.append(path);canonical.append(item)
    if sum(p.stat().st_size for p in paths)>30*1024*1024:raise ValueError('参考图总大小请控制在30 MB内。')
    return paths,canonical

def generate_version(pid,mode,prompt,maximum,parent=None,settings=None,image_settings=None,references=None,marks=None,detail_mode='preserve',auto_plan=False,*,inline_job=None,revision_key=None):
    detail_mode=checked_detail_mode(detail_mode)
    # No source exists in text mode. Avoid the photo-detail tracing path even
    # when an older client sends its default preserve setting.
    if mode=='text':detail_mode='simplify'
    options=image_options(image_settings);marks=checked_marks(marks)
    project=read_project(pid)
    if parent and not settings:
        settings=preferred_settings(version_of(project,parent)) or None
    if settings:
        settings={'width':60,'thread':'105D',**settings}
    width,thread=(settings['width'],settings['thread']) if settings else (60,'105D')
    paths,refs=reference_files(project,references,parent) if mode!='text' else ([],[])
    combined=reference_prompt(prompt,len(paths),marks)
    def worker(jid):
        project=read_project(pid);vid='v-'+(revision_key or uuid.uuid4().hex)[:12];folder=PROJECTS/pid/vid
        folder.mkdir(exist_ok=bool(revision_key))
        if mode=='upload' and not (PROJECTS/pid/'input.png').exists():
            normalize_image(paths[0],PROJECTS/pid/'input.png')
        matte=choose_matte(paths)
        photographic=mode=='upload' and bool(paths) and has_continuous_tones(paths[0])
        full_prompt=style_prompt(combined,maximum,mode,matte,detail_mode,settings,photographic)
        reusable=None
        if revision_key:
            for saved in [folder,*sorted((PROJECTS/pid).glob('v-*'))]:
                brief=saved/'generation-prompt.txt';metadata=saved/'generation.json'
                if brief.is_file() and metadata.is_file() and (saved/'generated.png').is_file() and brief.read_text()==full_prompt:
                    prior=json.loads(metadata.read_text())
                    if prior.get('references')==refs and prior.get('image_settings')==options:
                        reusable=saved,prior
                        break
        (folder/'generation-prompt.txt').write_text(full_prompt)
        update_job(jid,'AI正在修复圆润平面稿' if mode=='upload' else 'AI正在按文字修改平面稿' if mode=='edit' else 'AI正在绘制圆润平面稿')
        if reusable:
            saved,run=reusable
            if saved!=folder:shutil.copyfile(saved/'generated.png',folder/'generated.png')
        else:
            raw,run=request_image(config(),full_prompt,options,paths,marks)
            normalize_image(io.BytesIO(raw),folder/'generated.tmp',max_side=None)
            (folder/'generated.tmp').replace(folder/'generated.png')
        save_json(folder/'generation.json',{**run,'image_settings':options,'references':refs,'marks':marks,'matte':matte,
                                          'photo_simplification':photographic})
        update_job(jid,'正在分色，保留白色主体与线条',generated_url=asset_url(pid,vid,'generated.png'))
        register_to=PROJECTS/pid/parent if inline_job is not None and mode=='edit' and parent else None
        with ART_LOCK:meta,view=flatten(folder/'generated.png',folder,maximum,background_color=matte,detail_mode=detail_mode,width=width,thread=thread,register_to=register_to)
        version={'id':vid,'parent_id':parent,'kind':mode,'prompt':prompt,'created_at':timestamp(),'max_colors':maximum,'color_count':len(meta['colors']),'object_count':len(view['meta']['separated_objects']),'aspect_ratio':(meta['height_px']-1)/max(1,meta['width_px']-1),'flat_url':asset_url(pid,vid,'flat.png'),'preview_url':asset_url(pid,vid,'preview.json'),'layers_url':asset_url(pid,vid,'layers.svg'),'plans':[]}
        version.update(generated_url=asset_url(pid,vid,'generated.png'),image_settings=options,references=refs,marks=marks,generation_seconds=run['generation_seconds'],background_handling=meta['background_handling'],detail_mode=detail_mode)
        if 'revision_registration' in meta:version['revision_registration']=meta['revision_registration']
        return finish_artwork(pid,version,settings,auto_job=jid if auto_plan else None)
    return worker(inline_job) if inline_job is not None else submit(pid,'artwork',worker)

def checked_detail_mode(value):
    if value not in ('preserve','simplify'):raise ValueError('请选择保留细节或简化图案。')
    return value

def new_project(body,owner_callback=None):
    SCHEDULER.check_capacity()
    settings=checked_settings(body['settings']) if 'settings' in body else None
    auto_plan=body.get('auto_plan',False)
    if not isinstance(auto_plan,bool):raise ValueError('制版方式无效。')
    if auto_plan and not settings:raise ValueError('自动制版需要成品尺寸与线粗。')
    if settings and 'width' not in body['settings']:raise ValueError('请先填写成品宽度。')
    width,thread=(settings['width'],settings['thread']) if settings else (60,'105D')
    mode=body.get('mode');prompt=checked_prompt(body.get('prompt',''),required=mode=='text');maximum=checked_colors(body.get('max_colors',9))
    detail_mode=checked_detail_mode(body.get('detail_mode','preserve'))
    if mode not in ['upload','text','flat']:raise ValueError('请选择导入平面稿、上传修复或文字生成。')
    image_bytes=None
    if mode in ['upload','flat']:
        uri=body.get('image_data','')
        if not isinstance(uri,str) or not re.match(r'^data:image/(png|jpeg|webp);base64,',uri):raise ValueError('请选择PNG、JPG或WebP图片。')
        try:image_bytes=base64.b64decode(uri.split(',',1)[1],validate=True)
        except Exception:raise ValueError('图片无法读取，请重新选择。')
        if len(image_bytes)>8_000_000:raise ValueError('图片请小于8 MB。')
    options=image_options(body.get('image_settings'))
    background=body.get('background_mode','preserve')
    if background not in ['preserve','remove_white']:raise ValueError('背景选项无效。')
    pid=uuid.uuid4().hex[:16];folder=PROJECTS/pid;folder.mkdir()
    now=timestamp();project={'id':pid,'title':prompt[:24] if prompt else ('导入平面稿' if mode=='flat' else '上传图片修复'),'mode':mode,'created_at':now,'updated_at':now,'source_url':f'/assets/{pid}/input.png' if image_bytes else None,'versions':[],'current_version_id':None}
    if image_bytes is not None:
        try:
            with Image.open(io.BytesIO(image_bytes)) as im:
                if im.format not in ['JPEG','MPO','PNG','WEBP'] or im.width*im.height>25_000_000:raise ValueError('请使用2500万像素以内的 PNG、JPG 或 WebP 图片。')
                ext={'JPEG':'jpg','MPO':'jpg','PNG':'png','WEBP':'webp'}[im.format]
        except (OSError,Image.DecompressionBombError):raise ValueError('图片无法读取，请使用2500万像素以内的 PNG、JPG 或 WebP 图片。') from None
        project['original_file']='original.'+ext;(folder/project['original_file']).write_bytes(image_bytes)
    save_json(folder/'project.json',project)
    if owner_callback:owner_callback(pid)
    if mode=='flat':
        def worker(jid):
            update_job(jid,'正在直接分色与匹配绣线，不调用 AI')
            vid='v-'+uuid.uuid4().hex[:12];target=folder/vid;target.mkdir()
            normalize_image(folder/project['original_file'],folder/'input.png')
            shutil.copy2(folder/'input.png',target/'generated.png')
            try:
                with ART_LOCK:meta,view=flatten(target/'generated.png',target,maximum,preserve_white=background=='preserve',detail_mode=detail_mode,width=width,thread=thread,
                    compressed_source=ext in {'jpg','webp'})
            except ValueError as error:
                if '生成图没有清晰' in str(error):
                    raise ValueError('背景无法自动分离，请使用透明底或纯白底的平面稿。') from error
                raise
            version={'id':vid,'parent_id':None,'kind':'flat','prompt':'直接导入并分色，未使用 AI 重绘','created_at':timestamp(),
                     'max_colors':maximum,'color_count':len(meta['colors']),'object_count':len(view['meta']['separated_objects']),
                     'aspect_ratio':(meta['height_px']-1)/max(1,meta['width_px']-1),'flat_url':asset_url(pid,vid,'flat.png'),
                     'preview_url':asset_url(pid,vid,'preview.json'),'layers_url':asset_url(pid,vid,'layers.svg'),'detail_mode':detail_mode,'plans':[]}
            return finish_artwork(pid,version,settings,auto_job=jid if auto_plan else None)
        return submit(pid,'import',worker)
    fallback='修复成规整的平面图，保留原图造型和重要特征，去掉细碎装饰，简化成清楚的大色块。' if detail_mode=='simplify' else '修复成规整的平面图，保留原图造型、花纹与细节。'
    return generate_version(pid,mode,prompt or fallback,maximum,settings=settings,image_settings=options,detail_mode=detail_mode,auto_plan=auto_plan)

def edit_project(pid,body):
    project=read_project(pid);parent=version_of(project,body.get('parent_version_id'))
    prompt=checked_prompt(body.get('prompt',''));maximum=checked_colors(body.get('max_colors',parent['max_colors']))
    extra={}
    if 'settings' in body:extra['settings']=checked_settings(body['settings'])
    for k in ['image_settings','references','marks']:
        if k in body:extra[k]=body[k]
    extra['detail_mode']=checked_detail_mode(body.get('detail_mode','preserve'))
    return generate_version(pid,'edit',prompt,maximum,parent['id'],**extra)

def upload_reference(pid,body):
    read_project(pid);uri=body.get('image_data','')
    if not isinstance(uri,str) or not re.match(r'^data:image/(png|jpeg|webp);base64,',uri):raise ValueError('请选择PNG、JPG或WebP图片。')
    try:raw=base64.b64decode(uri.split(',',1)[1],validate=True)
    except ValueError:raise ValueError('图片无法读取。')
    if len(raw)>10*1024*1024:raise ValueError('单张参考图请小于10 MB。')
    # Validate the original, but keep its bytes and resolution for Images API.
    with Image.open(io.BytesIO(raw)) as im:
        if im.format not in ['PNG','JPEG','MPO','WEBP'] or im.width*im.height>25_000_000:raise ValueError('参考图格式或尺寸无效。')
        ext={'PNG':'png','JPEG':'jpg','MPO':'jpg','WEBP':'webp'}[im.format];im.verify()
    rid='r-'+uuid.uuid4().hex[:12];folder=PROJECTS/pid/rid;folder.mkdir()
    filename='reference.'+ext;(folder/filename).write_bytes(raw)
    name=body.get('name','参考图片')
    if not isinstance(name,str):name='参考图片'
    ref={'id':rid,'file':filename,'name':Path(name).name[:100],'url':asset_url(pid,rid,filename)}
    with LOCK:
        project=read_project(pid);project.setdefault('references',[]).append(ref);save_json(PROJECTS/pid/'project.json',project)
    return ref

def recolor_project(pid,body):
    project=read_project(pid);parent=version_of(project,body.get('parent_version_id'))
    source=PROJECTS/pid/parent['id'];colors=body.get('colors')
    current=json.loads((source/'palette.json').read_text())['colors']
    if not isinstance(colors,list) or len(colors)!=len(current):
        raise ValueError('配色数量与当前平面稿不一致，请重新载入。')
    if any(not isinstance(h,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',h) for h in colors):
        raise ValueError('请选择有效颜色。')
    colors=[h.upper() for h in colors]
    if colors==[c['hex'].upper() for c in current]:raise ValueError('配色还没有改变。')
    settings=checked_settings(body['settings']) if 'settings' in body else preferred_settings(parent)
    def worker(jid):
        update_job(jid,'正在保存配色，保留原有轮廓和比例')
        vid='v-'+uuid.uuid4().hex[:12];folder=PROJECTS/pid/vid
        with ART_LOCK:meta,view=recolor_artwork(source,folder,colors,settings.get('width',60),settings.get('thread','105D'))
        changes=[f'{c["name"]}→{h}' for c,h in zip(current,colors) if c['hex'].upper()!=h]
        version={'id':vid,'parent_id':parent['id'],'kind':'palette','prompt':'直接改色：'+'、'.join(changes),'created_at':timestamp(),'max_colors':parent['max_colors'],'color_count':len(meta['colors']),'object_count':len(view['meta']['separated_objects']),'aspect_ratio':parent['aspect_ratio'],'flat_url':asset_url(pid,vid,'flat.png'),'preview_url':asset_url(pid,vid,'preview.json'),'layers_url':asset_url(pid,vid,'layers.svg'),'plans':[],'preferred_settings':settings}
        with LOCK:
            p=read_project(pid);p['versions'].append(version);p['current_version_id']=vid;p['updated_at']=timestamp();save_json(PROJECTS/pid/'project.json',p)
        return {'project_id':pid,'version_id':vid}
    return submit(pid,'palette',worker)

def fitted_version(pid, version, settings, keep_unchanged_source=False):
    """Reuse a size-specific derivative; never repeatedly erode a derivative."""
    project=read_project(pid)
    while version.get('kind')=='fit':
        version=version_of(project,version['parent_id'])
    source_id=version['id']
    signature={'policy':FIT_POLICY,'width':settings['width'],'thread':settings['thread']}
    if all(version.get('fit_report',{}).get(k)==v for k,v in signature.items()):return version
    cached=next((v for v in reversed(project['versions']) if v.get('kind')=='fit'
                 and v.get('parent_id')==source_id and all(v.get('fit_report',{}).get(k)==x for k,x in signature.items())),None)
    if cached:return cached
    vid='v-'+uuid.uuid4().hex[:12];folder=PROJECTS/pid/vid
    try:
        with ART_LOCK:meta,view,report=fit_artwork(PROJECTS/pid/source_id,folder,settings['width'],settings['thread'])
    except Exception:
        shutil.rmtree(folder,ignore_errors=True)
        raise
    if keep_unchanged_source and not report['changes']:
        shutil.rmtree(folder)
        with LOCK:
            project=read_project(pid);version=version_of(project,source_id)
            version['fit_report']=report
            save_json(PROJECTS/pid/'project.json',project)
        return version
    result={'id':vid,'parent_id':source_id,'kind':'fit','prompt':f"按 {settings['width']:g} mm / {settings['thread']} 适配花纹和间隙",'created_at':timestamp(),
            'max_colors':version['max_colors'],'color_count':len(meta['colors']),'object_count':len(view['meta']['separated_objects']),
            'aspect_ratio':version['aspect_ratio'],'flat_url':asset_url(pid,vid,'flat.png'),'preview_url':asset_url(pid,vid,'preview.json'),
            'layers_url':asset_url(pid,vid,'layers.svg'),'plans':[],'detail_mode':meta.get('detail_mode','simplify'),'fit_report':report,'preferred_settings':settings}
    if (folder/'fit-changes.png').is_file():
        result['change_map_url']=asset_url(pid,vid,'fit-changes.png')
    if 'revision_registration' in version:result['revision_registration']=version['revision_registration']
    with LOCK:
        project=read_project(pid);project['versions'].append(result);project['current_version_id']=vid;project['updated_at']=timestamp();save_json(PROJECTS/pid/'project.json',project)
    return result

def fit_project(pid,body):
    project=read_project(pid);version=version_of(project,body.get('parent_version_id'));settings=checked_settings(body)
    def worker(jid):
        update_job(jid,'正在按实际尺寸自动取舍难绣细节，原稿保留')
        fitted=fitted_version(pid,version,settings)
        return {'project_id':pid,'version_id':fitted['id']}
    return submit(pid,'fit',worker)

def machine_file_signature(folder):
    hashes = []
    for name in ('design.dst', 'design.pes'):
        with (folder/name).open('rb') as source:
            hashes.append(hashlib.file_digest(source, 'sha256').hexdigest())
    return '-'.join(hashes)


def record_sewout(pid, body):
    vid, key = identifier(body.get('version_id')), identifier(body.get('plan_id'))
    token = identifier(body.get('sample_id'))
    if not re.fullmatch(r'[a-f0-9]{32}', token) or body.get('result') not in ('pass', 'revise'):
        raise ValueError('请为本次试绣选择结果。')
    setup, note = body.get('setup'), body.get('note', '')
    if not isinstance(setup, str) or not 1 <= len(setup.strip()) <= 120:
        raise ValueError('请填写本次试绣的机台与用料，最多 120 字。')
    if not isinstance(note, str) or len(note.strip()) > 300:
        raise ValueError('试绣备注最多 300 字。')
    entry = {'id': token, 'result': body['result'], 'setup': setup.strip(), 'note': note.strip()}
    with LOCK:
        project = read_project(pid)
        version = version_of(project, vid)
        plan = next((p for p in version.get('plans', []) if p['id'] == key), None)
        if not plan:
            raise ValueError('当前图稿没有这份针版。')
        folder = PROJECTS/pid/vid/'plans'/key
        if not all((folder/name).is_file() for name in ('design.dst', 'design.pes')):
            raise ValueError('当前针版缺少机器文件，请先重新生成。')
        signature = machine_file_signature(folder)
        if body.get('file_signature') and body['file_signature'] != signature:
            raise ValueError('这份针版的机器文件已更新，请重新打开后核对。')
        entry['file_signature'] = signature
        records = plan.setdefault('sewouts', [])
        existing = next((r for r in records if r['id'] == token), None)
        if existing and any(existing[k] != v for k, v in entry.items()):
            raise ValueError('这次试绣已登记，请刷新后核对。')
        if not existing:
            records.append({**entry, 'created_at': timestamp()})
        plan['file_signature'] = signature
        save_json(PROJECTS/pid/'project.json', project)
    return {'file_signature': signature, 'sewouts': records}


def plan_project(pid,body,*,inline_job=None):
    if inline_job is None:SCHEDULER.check_capacity()
    project=read_project(pid);version=version_of(project,body.get('version_id'))
    settings=checked_settings(body);width,spacing,thread,satin_spacing=settings['width'],settings['spacing'],settings['thread'],settings['satin_spacing']
    fabric=settings['fabric']
    source=body.get('revision_from')
    before=None
    if source is not None:
        if not isinstance(source,dict) or source.get('version_id')!=version['id']:
            raise ValueError('修改前后的评估需要对应同一份图稿。')
        before=draft_review(pid,source)
        if (before['width'],before['thread'])!=(width,thread):
            raise ValueError('同尺寸复评需要沿用初版的宽度和线材。')
    folder=PROJECTS/pid/version['id']
    requested={'region_cuts':body.get('region_cuts') if body.get('region_cuts') is not None else [],
               'object_overrides':body.get('object_overrides') if body.get('object_overrides') is not None else {}}
    auto_fit=body.get('auto_simplify',False)
    if not isinstance(auto_fit,bool):raise ValueError('自动取舍设置无效。')
    # Respect existing manual work. Geometry IDs must never migrate by guess.
    auto_fit=auto_fit and not requested['region_cuts'] and not requested['object_overrides']
    if not isinstance(requested['region_cuts'],list) or len(requested['region_cuts'])>64:raise ValueError('一份方案最多分区 64 次。')
    if not isinstance(requested['object_overrides'],dict) or len(requested['object_overrides'])>600:raise ValueError('局部工艺设置无效。')
    def complete(key):
        return all((folder/'plans'/key/name).is_file() for name in ['preview.json','embroidery-project.zip','design.dst','design.pes','validation.json'])
    # Cheap exact-match cache lookup. A cache miss is geometrically validated
    # inside the admitted job, never by an unbounded HTTP request thread.
    encoded=json.dumps(requested,sort_keys=True,separators=(',',':'),allow_nan=False)
    cached=None if auto_fit else next((p for p in reversed(version.get('plans',[])) if p.get('engine_version')==ENGINE_VERSION
                 and p.get('adaptive_fill',False)==settings.get('adaptive_fill',False)
                 and all(p.get(k)==v for k,v in settings.items() if k!='adaptive_fill')
                 and json.dumps({k:p.get(k,[] if k=='region_cuts' else {}) for k in requested},sort_keys=True,separators=(',',':'))==encoded
                 and complete(p['id'])),None)
    def worker(jid):
        nonlocal version,folder,cached
        if auto_fit:
            update_job(jid,'正在评估细节并自动取舍，另存新版，保留原稿')
            version=fitted_version(pid,version,settings,keep_unchanged_source=True);folder=PROJECTS/pid/version['id']
        if cached:
            local={k:cached[k] for k in requested};key=cached['id']
        else:
            update_job(jid,'正在核对色块与局部工艺')
            *_,cuts,overrides=prepare_regions(folder,width,requested['region_cuts'],requested['object_overrides'])
            local={'region_cuts':cuts,'object_overrides':overrides}
            fill_mode='adaptive:' if settings.get('adaptive_fill') else ''
            key='p-'+hashlib.sha256((f'{ENGINE_VERSION}:{width}:{thread}:{spacing}:{satin_spacing}:{fabric}:'+fill_mode+json.dumps(local,sort_keys=True,separators=(',',':'))).encode()).hexdigest()[:14]
        out=folder/'plans'/key
        if not complete(key):
            if cached:raise ValueError('缓存文件已改变，请重新提交生成工程。')
            with ENGINE_LOCK:make_plan(folder,out,width,thread,spacing,lambda message:update_job(jid,message),satin_spacing=satin_spacing,fabric=fabric,region_cuts=cuts,object_overrides=overrides,adaptive_fill=settings.get('adaptive_fill',False))
        view=json.loads((out/'preview.json').read_text());base=asset_url(pid,version['id'],f'plans/{key}/')
        plan={'id':key,'engine_version':ENGINE_VERSION,**settings,**local,'height':view['meta']['design_height_mm'],'stitch_count':view['stats']['stitch_count'],'quality_review':view['meta'].get('quality_review'),'preview_url':base+'preview.json','downloads':{name:base+file for name,file in [('工程包','embroidery-project.zip'),('可编辑工程','embroidery.svg'),('尺寸图','dimensions.svg'),('DST','design.dst'),('PES','design.pes'),('配线单','threads.csv'),('局部工艺','region-settings.json')]}}
        plan['file_signature'] = machine_file_signature(out)
        plan['adaptive_fill'] = settings.get('adaptive_fill', False)
        with LOCK:
            p=read_project(pid);v=version_of(p,version['id'])
            existing = next((x for x in v['plans'] if x['id'] == key), None)
            if existing and existing.get('sewouts'):
                plan['sewouts'] = existing['sewouts']
            if before and not existing:
                p.setdefault('revisions',[]).append({'kind':'manual','source_version':source['version_id'],
                    'source_plan':source.get('plan_id'),'before':before,'created_at':timestamp(),
                    'result':{'project_id':pid,'version_id':version['id'],'plan_id':key}})
            v['plans']=[x for x in v['plans'] if x['id']!=key]+[plan];p['updated_at']=timestamp();save_json(PROJECTS/pid/'project.json',p)
        return {'project_id':pid,'version_id':version['id'],'plan_id':key}
    return worker(inline_job) if inline_job is not None else submit(pid,'plan',worker,lightweight=cached is not None)

def advise_project(pid,body,*,inline_job=None):
    project=read_project(pid);version=version_of(project,body.get('version_id'))
    settings=checked_settings(body);folder=PROJECTS/pid/version['id']
    instruction=checked_prompt(body.get('instruction',''),required=False)
    if len(instruction)>500:raise ValueError('工艺要求请控制在500字以内。')
    region_id=body.get('region_id')
    if region_id is not None and (not isinstance(region_id,str) or len(region_id)>180):
        raise ValueError('请重新点选要分析的色块。')
    def worker(jid):
        prepared=prepare_regions(folder,settings['width'],body.get('region_cuts'),body.get('object_overrides'))
        cuts,overrides=prepared[-2:]
        snapshot={'project_id':pid,'version_id':version['id'],**settings,'region_cuts':cuts,'object_overrides':overrides}
        conf=config(False)
        reference=PROJECTS/pid/project.get('original_file','input.png')
        if not reference.is_file():reference=folder/'generated.png'
        fingerprint=hashlib.sha256(json.dumps({'engine':ENGINE_VERSION,'craft':'visual-roles-v3','snapshot':snapshot,'instruction':instruction,'region_id':region_id,
            'vision':conf.get('ALIBABA_VISION_MODEL','qwen3-vl-plus'),'model':conf.get('ALIBABA_PLANNER_MODEL'),
            'reference':hashlib.sha256(reference.read_bytes()).hexdigest() if reference.is_file() else None,
            'image':hashlib.sha256((folder/'flat.png').read_bytes()).hexdigest()},sort_keys=True).encode()).hexdigest()[:20]
        cache=folder/f'craft-cache-{fingerprint}.json'
        if cache.exists():
            advice=json.loads(cache.read_text());advice['usage'].update(cached=True,calls=0)
            return {'project_id':pid,'version_id':version['id'],'advice':advice}
        advice=plan_visual_batches(conf,folder,reference,settings,instruction,prepared,folder/f'craft-parts-{fingerprint}',lambda stage:update_job(jid,stage),region_id)
        advice['snapshot']=snapshot
        if not advice.get('incomplete'):save_json(cache,advice)
        return {'project_id':pid,'version_id':version['id'],'advice':advice}
    return worker(inline_job) if inline_job is not None else submit(pid,'craft_advice',worker)


@lru_cache(maxsize=24)
def revision_region_ids(path, changed_ns):
    objects=json.loads(path.read_text())['meta']['separated_objects']
    return frozenset(key for obj in objects if (key:=obj.get('region_key') or obj.get('id')))


def draft_review(pid,body):
    project=read_project(pid);version=version_of(project,body.get('version_id'));plan=None
    if body.get('plan_id'):
        plan=next((p for p in version['plans'] if p['id']==body['plan_id']),None)
        if plan is None:raise ValueError('请选择当前图稿的针版，然后重新评估。')
    # The object ids come from this machine file, including any manual splits.
    # Preflight failures have no file and only artwork evidence is available.
    ids=set()
    if plan:
        path=PROJECTS/pid/version['id']/'plans'/plan['id']/'preview.json'
        if not path.is_file():raise ValueError('这份针版尚未生成。')
        craft=(plan.get('quality_review') or {}).get('stitch_suitability') or {}
        measured=craft.get('regions')
        if isinstance(measured,dict) and len(measured)==craft.get('checked_regions'):
            ids=measured.keys()
        else:
            ids=revision_region_ids(path,path.stat().st_mtime_ns)
    return review_draft(version,plan,body.get('observations') or {},ids)


def revise_draft(pid,body):
    review=draft_review(pid,body)
    if not review['route']:raise ValueError('当前评估没有可自动修改的部位。')
    project=read_project(pid);vid=body['version_id'];parent=version_of(project,vid)
    source_plan=next((p for p in parent['plans'] if p['id']==body.get('plan_id')),None)
    settings=checked_settings(source_plan or parent['preferred_settings'])
    changes=[{k:item[k] for k in ('kind','text','region')} for item in review['items']]
    signature=hashlib.sha256(json.dumps([ENGINE_VERSION,vid,body.get('plan_id'),review['route'],changes],
                                        sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20]
    def worker(jid):
        stored=read_project(pid)
        prior=next((a for a in stored.get('revisions',[]) if a.get('key')==signature),None)
        pending=prior and prior['result'].get('plan_pending') and not prior['result'].get('fit_stop')
        saved=version_of(stored,prior['result']['version_id']) if pending else None
        if prior and (not pending or (saved.get('revision_registration') or {}).get('needs_review')):
            return {**prior['result'],'revision':prior,'cached':True}
        notes=' '.join(item['text'] for item in review['items'])
        if saved:
            result=finish_artwork(pid,saved,settings,auto_job=jid)
        elif review['route']=='artwork':
            # Start with the pre-fit drawing; the failed fit already discarded
            # some of the very marks the revision is meant to preserve.
            master=parent
            while master.get('kind')=='fit':
                master=version_of(project,master['parent_id'])
            references=[{'kind':'version','id':master['id']}]
            if project.get('original_file'):references.append({'kind':'original'})
            regions=(parent.get('fit_report') or {}).get('omitted_motifs',[])
            positions=[]
            for group in regions[:6]:
                if len(group.get('bbox_mm',[]))==4:
                    x,y,X,Y=group['bbox_mm']
                    positions.append(f'横向{(x+X-10)/2/review["width"]:.0%}、纵向{(y+Y-10)/2/review["height"]:.0%}')
            instruction=(f'请按制版初评修订图一。成品 {review["width"]:g} × {review["height"]:g} mm，{review["thread"]}。'
                         '以图一为主体，保持外轮廓宽高比、姿态、关键五官及主色。仅修复评估指出的细节。'
                         +notes+' 重点位置：'+'，'.join(positions)+'。用少量闭合的大块代替织不出的纹理，保留有意义的细线和孔隙。')
            update_job(jid,'正在按初版评估修改平面稿，完成后会按原尺寸重新排针')
            result=generate_version(pid,'edit',instruction,parent['max_colors'],vid,settings=settings,
                                    references=references,detail_mode='simplify',auto_plan=True,inline_job=jid,revision_key=signature)
        else:
            overrides=dict(source_plan.get('object_overrides',{}))
            cuts=source_plan.get('region_cuts',[])
            for rid in review['targets']:
                update_job(jid,'AI 正在按评估修改目标色块的工艺')
                response=advise_project(pid,{**settings,'version_id':vid,'region_cuts':cuts,'object_overrides':overrides,
                                         'region_id':rid,'instruction':notes[:500]},inline_job=jid)
                for item in response['advice']['suggestions']:
                    if item['id']==rid and rid not in overrides:overrides[rid]=item['settings']
            if overrides==source_plan.get('object_overrides',{}):
                raise ValueError('本次没有可采用的新针法，初版已保留。')
            result=plan_project(pid,{**settings,'version_id':vid,'region_cuts':cuts,
                                      'object_overrides':overrides},inline_job=jid)
            result['unattended']=True
        record={'key':signature,'source_version':vid,'source_plan':body.get('plan_id'),
                'before':review,'result':result.copy(),'created_at':timestamp()}
        with LOCK:
            current=read_project(pid)
            current['revisions']=[r for r in current.get('revisions',[]) if r.get('key')!=signature]+[record]
            save_json(PROJECTS/pid/'project.json',current)
        return {**result,'revision':record}
    return submit(pid,'revision',worker)


def preserve_details_project(pid,body):
    project=read_project(pid);parent=version_of(project,body.get('parent_version_id'))
    settings=preferred_settings(parent)
    source=PROJECTS/pid/parent['id']
    def worker(jid):
        update_job(jid,'从本版生成原图重新提取细节，原版本保留')
        vid='v-'+uuid.uuid4().hex[:12];folder=PROJECTS/pid/vid;folder.mkdir()
        shutil.copy2(source/'generated.png',folder/'generated.png')
        palette=json.loads((source/'palette.json').read_text())
        generation=json.loads((source/'generation.json').read_text()) if (source/'generation.json').exists() else {}
        with ART_LOCK:
            meta,view=flatten(folder/'generated.png',folder,parent['max_colors'],background_color=generation.get('matte'),
                              preserve_white=palette.get('background_handling')=='preserve_white',detail_mode='preserve',
                              width=settings.get('width',60),thread=settings.get('thread','105D'))
        if generation:save_json(folder/'generation.json',generation)
        version={'id':vid,'parent_id':parent['id'],'kind':'details','prompt':'保留细节重新分色；已在生成阶段丢失的花纹无法自动恢复',
                 'created_at':timestamp(),'max_colors':parent['max_colors'],'color_count':len(meta['colors']),
                 'object_count':len(view['meta']['separated_objects']),'aspect_ratio':(meta['height_px']-1)/max(1,meta['width_px']-1),
                 'flat_url':asset_url(pid,vid,'flat.png'),'generated_url':asset_url(pid,vid,'generated.png'),
                 'preview_url':asset_url(pid,vid,'preview.json'),'layers_url':asset_url(pid,vid,'layers.svg'),
                 'plans':[],'detail_mode':'preserve','preferred_settings':preferred_settings(parent)}
        with LOCK:
            p=read_project(pid);p['versions'].append(version);p['current_version_id']=vid;p['updated_at']=timestamp();save_json(PROJECTS/pid/'project.json',p)
        return {'project_id':pid,'version_id':vid}
    return submit(pid,'details',worker)

def split_project(pid,body):
    project=read_project(pid);version=version_of(project,body.get('version_id'))
    settings=checked_settings(body);folder=PROJECTS/pid/version['id']
    instruction=checked_prompt(body.get('instruction',''),required=False)
    use_ai=body.get('use_ai',True)
    if type(use_ai) is not bool:raise ValueError('分区模式无效。')
    if len(instruction)>500:raise ValueError('分区要求请控制在500字以内。')
    def worker(jid):
        _,_,_,_,canonical_cuts,canonical_overrides=prepare_regions(folder,settings['width'],body.get('region_cuts'),body.get('object_overrides'))
        snapshot={'project_id':pid,'version_id':version['id'],**settings,'region_cuts':canonical_cuts,'object_overrides':canonical_overrides}
        model=config(False).get('ALIBABA_VISION_MODEL','qwen3-vl-plus') if use_ai else 'local'
        fingerprint=hashlib.sha256(json.dumps({'engine':'split-v4','snapshot':snapshot,'request':instruction,'model':model,'image_hash':hashlib.sha256((folder/'classes.npy').read_bytes()).hexdigest()},sort_keys=True).encode()).hexdigest()[:20]
        cache=folder/f'split-cache-{fingerprint}.json'
        if cache.exists():
            result=json.loads(cache.read_text());result['usage']['cached']=True
            return {'project_id':pid,'version_id':version['id'],'split_advice':result}
        update_job(jid,'正在找可分开的窄颈与转向，保留每种颜色的轮廓和孔洞')
        candidates,regions,cuts,overrides,height=candidate_splits(folder,settings['width'],body.get('region_cuts'),body.get('object_overrides'),settings['thread'])
        path=folder/f'split-candidates-{jid}.png';diagram(folder,candidates,path)
        reference=PROJECTS/pid/project.get('original_file','input.png')
        if not reference.exists():reference=folder/'generated.png'
        if use_ai:
            update_job(jid,'AI 正在对照原图检查花纹细节，并选择合适的分界')
            raw,usage=request_split(config(False),reference,candidates,{**settings,'height':height},instruction,path)
        else:
            seen=set();selected=[]
            for c in candidates:
                if c['region_id'] in seen:continue
                seen.add(c['region_id']);selected.append({'candidate_id':c['id'],'confidence':1,'reason':'按窄颈和形状转折找到的候选，请核对分界是否符合图案。','children':[]})
                if len(selected)>=8:break
            raw={'selected':selected,'detail_notes':['免费规则只分析几何，不能识别原图装饰是否被 AI 重绘删掉，请使用原图对照。'],'summary':'本地几何分区建议'}
            usage={'model':'local','elapsed_s':0,'input_tokens':0,'output_tokens':0}
        save_json(folder/f'split-response-{jid}.json',raw)
        update_job(jid,'正在核对分区并集、孔洞及已有手动设置')
        result=validate_split(raw,folder,settings['width'],cuts,overrides,candidates,settings['thread'])
        if use_ai:
            for split in result['accepted']:
                for child in split['children']:
                    if child in result['object_overrides']:
                        result['object_overrides'][child]['source']='ai'
        result.update(usage=usage,source='ai' if use_ai else 'local',candidate_count=len(candidates),diagram_url=asset_url(pid,version['id'],path.name),
                      snapshot={'project_id':pid,'version_id':version['id'],**settings,'region_cuts':cuts,'object_overrides':overrides})
        save_json(folder/f'split-review-{jid}.json',result)
        save_json(cache,result)
        return {'project_id':pid,'version_id':version['id'],'split_advice':result}
    return submit(pid,'split_advice',worker)


@contextmanager
def interactive_analysis():
    if not INTERACTIVE_SLOT.acquire(blocking=False):raise BusyError('上一处色块还在分析，请稍后再试。')
    try:
        resources=SCHEDULER.monitor.snapshot()
        reason=Admission(SCHEDULER.limits).reason(resources,lightweight=True)
        if reason:raise BusyError('色块分析稍后重试：'+reason+'。')
        yield
    finally:INTERACTIVE_SLOT.release()

def region_catalog(pid,body):
    project=read_project(pid);version=version_of(project,body.get('version_id'))
    thread=body.get('thread','105D')
    if thread not in ('75D','105D'):raise ValueError('请选择75D或105D。')
    try:width=float(body.get('width',60))
    except (ValueError,TypeError):raise ValueError('尺寸必须是数字。')
    if not 20<=width<=160:raise ValueError('宽度需要在20–160 mm之间。')
    folder=PROJECTS/pid/version['id'];width=round(width,1);cuts=body.get('region_cuts')
    # Versions are immutable. Include input stamps so replacements/imports also
    # invalidate the cache; different cuts and physical sizes never share it.
    stamps=[(folder/name).stat() for name in ['classes.npy','palette.json']]
    fingerprint=hashlib.sha256(json.dumps([ENGINE_VERSION,width,thread,cuts or [],
        [(s.st_mtime_ns,s.st_size) for s in stamps]],sort_keys=True).encode()).hexdigest()[:24]
    cache=folder/f'regions-cache-{fingerprint}.json'
    if cache.exists():return json.loads(cache.read_text())
    with interactive_analysis():
        result=catalog(folder,width,cuts,thread);save_json(cache,result);return result

def size_advice(pid,body):
    project=read_project(pid);version=version_of(project,body.get('version_id'))
    folder=PROJECTS/pid/version['id'];cache=folder/f'size-advice-{ENGINE_VERSION}.json'
    if cache.exists():return json.loads(cache.read_text())
    with interactive_analysis():return calculate_size_advice(folder,cache)

def calculate_size_advice(folder,cache):
    palette,groups,height=extract_geometries(folder,60)
    details=[]
    for ci,group in enumerate(groups):
        for polygon in group:
            corners=list(polygon.minimum_rotated_rectangle.exterior.coords)
            widths=[((b[0]-a[0])**2+(b[1]-a[1])**2)**.5 for a,b in zip(corners,corners[1:])]
            short=min(widths)
            # Thin/curved strips and rings: area / two-sided perimeter is a
            # width approximation. Solid islands use their oriented short side.
            if polygon.area/max(.001,polygon.minimum_rotated_rectangle.area)<.45:
                short=min(short,2*polygon.area/max(.001,polygon.length))
            if short>0:details.append({'color':palette['colors'][ci]['name'],'short_mm_at_60':round(short,4),'area_mm2_at_60':round(polygon.area,4)})
    details.sort(key=lambda x:x['short_mm_at_60'])
    profiles={}
    # Explicit trial heuristics, not a conversion from denier to diameter and
    # not a guarantee for arbitrary thread brands or actual sew-out coverage.
    for thread,threshold in [('75D',.7),('105D',1.)]:
        minimum=60*threshold/max(.001,details[0]['short_mm_at_60']) if details else 20
        width=max(20,int(__import__('math').ceil(minimum/5)*5))
        profiles[thread]={'width_mm':width,'height_mm':round(width*height/60,1),'detail_target_mm':threshold,'over_limit':width>160}
    result={'profiles':profiles,'details':details[:5],'object_count':len(details),'method':'independent_region_short_side_and_thin_strip_estimate','note':'根据独立色块的短边与窄条估算；不代表所有局部细颈或真实线材极限。需试绣校准。'}
    save_json(cache,result);return result

def seed_rabbit():
    pid='rabbit-reference';folder=PROJECTS/pid
    if (folder/'project.json').exists():return
    if not all((ROOT/'rabbit-test-20261114'/name).is_file() for name in ['original.jpg','user-approved-master.png']):return
    folder.mkdir(exist_ok=True);vid='v-selected';version_folder=folder/vid;version_folder.mkdir(exist_ok=True)
    shutil.copy2(ROOT/'rabbit-test-20261114/original.jpg',folder/'input-source.jpg')
    normalize_image(folder/'input-source.jpg',folder/'input.png')
    normalize_image(ROOT/'rabbit-test-20261114/user-approved-master.png',version_folder/'generated.png')
    meta,view=flatten(version_folder/'generated.png',version_folder,9);now=timestamp()
    version={'id':vid,'parent_id':None,'kind':'selected','prompt':'你选定的圆润平面稿','created_at':now,'max_colors':9,'color_count':len(meta['colors']),'object_count':len(view['meta']['separated_objects']),'aspect_ratio':(meta['height_px']-1)/(meta['width_px']-1),'flat_url':asset_url(pid,vid,'flat.png'),'preview_url':asset_url(pid,vid,'preview.json'),'layers_url':asset_url(pid,vid,'layers.svg'),'plans':[]}
    save_json(folder/'project.json',{'id':pid,'title':'兔子 · 已选平面稿','mode':'upload','created_at':now,'updated_at':now,'source_url':f'/assets/{pid}/input.png','current_version_id':vid,'versions':[version]})
