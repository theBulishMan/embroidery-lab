"""Non-destructive, millimetre geometry hints, not a sew-out certificate."""
from functools import lru_cache
from shapely.geometry import Polygon
from studio_craft import main_axis


def thread_detail_limits(thread):
    """Trial ranges for artwork at finished size, in millimetres on the fabric."""
    return {'75D': (.20, .8), '105D': (.24, 1.)}[thread]


def artwork_size_brief(width, thread):
    line, spot = thread_detail_limits(thread)
    return (f'成品主体宽度 {width:g} mm，使用 {thread} 绣线。按主体最左至最右的实际宽度估算每个细节，'
            f'不要用整张图片的像素或画布留白换算。面状小花瓣、圆点的试排参考宽度 {spot:g} mm，'
            f'约占主体宽度的 {spot / width * 100:.2f}%。连续细线可按约 {line:.2f} mm 单线处理。'
            '沿两笔之间保留贯通的底色通道，笔尖、字口以及花瓣间隙要能分辨。'
            '边缘因抗锯齿或拍摄形成的杂色并入相邻主色；有意画出的细线保留其中心走向、起止点和断开位置。'
            '优先保留脸、主花纹及其镂空。局部太挤时合并同向碎纹，让花瓣和留白能分开。'
            '尺寸足以容纳的二级花瓣、条纹和高光按参考图或文字要求画全。'
            '保持实物比例，不凭空增加装饰，不把尺寸、尺线或工艺说明画进图案。')


def approximate_width(p):
    _, length, width = main_axis(p)
    if p.area / max(length * width, .000001) < .45:
        width = min(width, 2 * p.area / max(p.length, .000001))
    return width


@lru_cache(maxsize=2048)
def detail_metrics(p):
    width = approximate_width(p)
    holes = [approximate_width(Polygon(ring)) for ring in p.interiors]
    local = {}
    # A disappearing branch/neck deserves inspection even when the enclosing
    # object's bounding box is large. Ignore the small rounding fringe left by
    # opening. These thresholds are our trial presets, not physical limits.
    for thread, target in [('75D', .7), ('105D', 1.)]:
        fraction = 0.
        if width >= target and p.area > .3:
            core = p.buffer(-target / 2, quad_segs=4)
            opened = core.buffer(target / 2 + .05, quad_segs=4)
            lost = p.difference(opened).area
            if lost > .15:
                fraction = lost / p.area
        local[thread] = round(fraction, 4)
    return {'width_mm': round(width, 4), 'hole_count': len(holes),
            'smallest_hole_mm': round(min(holes), 4) if holes else None,
            'narrow_fraction': local}


def fill_edge_allowance(p, thread, default_pull, manual_pull=None):
    """Keep useful motifs' narrow negative space from uniform over-expansion.

    This is a trial rule. It changes needle margins, never the artwork. Tiny
    isolated dots remain flagged for artwork review instead of being deleted.
    Explicit user pull compensation always wins.
    """
    facts=detail_metrics(p)
    protected=p.area>=2 and facts['narrow_fraction'].get(thread,0)>.04
    return {'expansion_mm':0 if protected else .05,
            'pull_compensation_mm':manual_pull if manual_pull is not None else min(default_pull,.02) if protected else default_pull,
            'detail_edge_protection':protected}
