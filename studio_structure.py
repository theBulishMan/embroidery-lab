"""Direction evidence from the visible outline, in finished millimetres."""
import math
from functools import lru_cache

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import split


@lru_cache(maxsize=1024)
def converging_surface(shape):
    """Two long sides leading to a common root and a curved, spreading edge.

    A triangle, an ellipse or a uniformly wide band alone supplies no such
    evidence. Test the entire outline against the proposed structure.
    """
    if shape.interiors or shape.area < 10 or shape.area < .94 * shape.convex_hull.area:
        return None
    diameter = math.hypot(shape.bounds[2]-shape.bounds[0], shape.bounds[3]-shape.bounds[1])
    outline = np.asarray(shape.simplify(max(.10, diameter*.009)).exterior.coords)
    edges = np.diff(outline, axis=0)
    lengths = np.linalg.norm(edges, axis=1)
    long = np.flatnonzero(lengths > diameter*.30)
    if not 2 <= len(long) <= 5:
        return None
    for i, first in enumerate(long):
        a, u = outline[first], edges[first]
        for second in long[i+1:]:
            b, v = outline[second], edges[second]
            system = np.column_stack((u, -v))
            if abs(np.linalg.det(system)) < .40 * lengths[first] * lengths[second]:
                continue
            root = a + u*np.linalg.solve(system, b-a)[0]
            if shape.exterior.distance(Point(root)) > diameter*.05:
                continue
            tips = [max(outline[j:j+2], key=lambda pt: np.linalg.norm(pt-root)) - root
                    for j in (first, second)]
            radii = np.linalg.norm(tips, axis=1)
            radius = float(radii.mean())
            if radius < 3 or min(radii) < .86*max(radii):
                continue
            angles = np.arctan2(np.array(tips)[:,1], np.array(tips)[:,0])
            turn = (angles[1]-angles[0]+math.pi) % (2*math.pi)-math.pi
            sweep = abs(float(turn))
            if not math.radians(38) <= sweep <= math.radians(151):
                continue
            start = float(angles[0] if turn > 0 else angles[1])
            stations = np.linspace(start, start+sweep, 49)
            arc = root + radius*np.column_stack((np.cos(stations), np.sin(stations)))
            sector = Polygon([root, *arc])
            if shape.symmetric_difference(sector).area <= .065*shape.area:
                return (tuple(root.tolist()), start, sweep, radius)
    return None


def spreading_sections(shape, field, pitch):
    """Partition the true shape without duplicating a hub or introducing colour passes."""
    root, start, sweep, radius = field
    count = max(3, min(9, math.ceil(math.degrees(sweep)/18)))
    # Near convergence multiple wedges would send lock stitches to a single
    # point. Fill this shared tip once, then work across the outer sections.
    hub_disk = Point(root).buffer(max(3*pitch, min(1.5, .14*radius)))
    hub = shape.intersection(hub_disk)
    rest = shape.difference(hub_disk)
    if hub.geom_type != 'Polygon' or hub.is_empty or rest.geom_type != 'Polygon':
        return []
    parts = [rest]
    for theta in np.linspace(start, start+sweep, count+1)[1:-1]:
        reach = radius*3
        ray = LineString([root, (root[0]+reach*math.cos(theta), root[1]+reach*math.sin(theta))])
        intersected = [piece for piece in parts if ray.crosses(piece)]
        if len(intersected) != 1:
            return []
        piece = intersected[0]
        children = list(split(piece, ray).geoms)
        if len(children) != 2 or min(p.area for p in children) < .30:
            return []
        parts.remove(piece)
        parts.extend(children)
    def offset(part):
        p = part.representative_point()
        return (math.atan2(p.y-root[1], p.x-root[0])-start) % (2*math.pi)
    parts.sort(key=offset)
    midpoints = start + sweep*(np.arange(count)+.5)/count
    angle = lambda a: round((-math.degrees(a)) % 180, 2)
    return [(hub, angle(start+sweep/2))] + [(p, angle(a)) for p, a in zip(parts, midpoints)]
