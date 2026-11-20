"""Needle clearance at intended gaps in the size-fitted colour separation."""
import cv2
import numpy as np
from shapely.geometry import LineString, box
from shapely.ops import unary_union
from shapely.strtree import STRtree


def gap_centres(ink, negative, pixel, thread):
    """Find opposite banks of a gap without marking an ordinary exposed edge."""
    ink = ink.astype(np.uint8)
    brush = np.ones((3, 3), np.uint8)
    distance, owners = cv2.distanceTransformWithLabels(1 - ink, cv2.DIST_L2, 5,
                                                       labelType=cv2.DIST_LABEL_CCOMP)
    owners = owners.astype(np.float32)
    near = (distance > 0) & (distance * pixel < 1.35 * thread + .5 * pixel)
    seam = (cv2.dilate(owners, brush) != cv2.erode(owners, brush)) & near & negative
    # Two long parallel strokes may join elsewhere in the same letter. Scan
    # for short gaps with ink at both ends, then require a sustained channel.
    # Opposed diagonals cover channels which are oblique to the raster axes.
    reach = min(48, int(np.ceil(2.5 * thread / pixel)))
    interior = np.zeros(ink.shape, np.uint8)
    for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
        step = np.hypot(dx, dy)
        length = int(reach / step)
        if length < 2:
            continue
        kernel = np.zeros((2 * length + 1, 2 * length + 1), np.uint8)
        cv2.line(kernel, (length, length), (length + dx * length, length + dy * length), 1)
        opposite = cv2.flip(kernel, -1)
        forward = cv2.dilate(ink, kernel, borderType=cv2.BORDER_CONSTANT, borderValue=0)
        backward = cv2.dilate(ink, opposite, borderType=cv2.BORDER_CONSTANT, borderValue=0)
        # A centre pixel is a local distance maximum across the channel.
        h, w = distance.shape
        if h < 3 or w < 3:
            continue
        mid = distance[1:-1, 1:-1]
        a = distance[1+dy:h-1+dy, 1+dx:w-1+dx]
        b = distance[1-dy:h-1-dy, 1-dx:w-1-dx]
        ridge = (mid >= a) & (mid >= b) & ((mid > a) | (mid > b))
        valid = negative & near & (forward != 0) & (backward != 0)
        interior[1:-1, 1:-1] |= ridge & valid[1:-1, 1:-1]
    count, labels, stats, _ = cv2.connectedComponentsWithStats(interior)
    if count > 1:
        span = np.max(stats[:, 2:4], axis=1) * pixel
        keep = span >= max(2 * thread, 4 * pixel)
        keep[0] = False
        seam |= keep[labels]
    return seam


def protected_zones(seam, pixel, margin):
    """Union exact pixel runs per island, preserving holes in closed seams."""
    count, labels, stats, _ = cv2.connectedComponentsWithStats(seam.astype(np.uint8))
    zones = []
    for label in range(1, count):
        x, y, w, h = stats[label, :4]
        rectangles = []
        for iy, row in enumerate(labels[y:y+h, x:x+w] == label, y):
            transitions = np.flatnonzero(np.diff(np.r_[False, row, False]))
            for a, b in transitions.reshape(-1, 2):
                rectangles.append(box(5 + (x+a-.5)*pixel, 5 + (iy-.5)*pixel,
                                      5 + (x+b-.5)*pixel, 5 + (iy+.5)*pixel))
        shape = unary_union(rectangles).simplify(pixel * .3, preserve_topology=True)
        zones.append(shape.buffer(margin, quad_segs=4))
    return zones


class StrokeGaps:
    def __init__(self, classes, colors, pixel, thread):
        self.by_ink = {}
        self.thread = thread
        cloth = classes == 255
        whites = [i for i, c in enumerate(colors)
                  if min(bytes.fromhex(c['hex'].lstrip('#'))) >= 237]
        blank = cloth | np.isin(classes, whites) if whites else cloth
        if not blank.any():
            return
        for ci in range(len(colors)):
            ink = classes == ci
            if not ink.any():
                continue
            negative = blank & ~ink
            if not negative.any():
                continue
            seam = gap_centres(ink, negative, pixel, thread)
            if seam.any():
                # Allow for the physical radius and rounding each needle to
                # the 0.1 mm machine grid. This protects only the gap's core.
                zones = protected_zones(seam, pixel, .6 * thread + .071)
                self.by_ink[ci] = STRtree(zones)

    def local_zones(self, ci, shape, extra=0):
        tree = self.by_ink.get(ci)
        if tree is None:
            return []
        return tree.geometries.take(tree.query(shape, predicate='dwithin', distance=extra))

    def clip(self, shape, ci, extra=0):
        zones = self.local_zones(ci, shape, extra)
        if not len(zones):
            return shape
        keepout = unary_union(zones)
        if extra:
            keepout = keepout.buffer(extra, quad_segs=4)
        def intact(part):
            return (part.geom_type == 'Polygon' and not part.is_empty and part.is_valid
                    and len(part.interiors) == len(shape.interiors) and part.area >= .80 * shape.area)
        clipped = shape.difference(keepout)
        if not intact(clipped):
            # One fragile intersection should not preclude protection at the
            # other ends of the same letter.
            clipped = shape
            for zone in zones:
                for margin in (extra, extra - .20*self.thread, extra - .40*self.thread):
                    cut = clipped.difference(zone.buffer(margin, quad_segs=4) if margin else zone)
                    if intact(cut):
                        clipped = cut
                        break
        return clipped if clipped.area < shape.area - 1e-7 else shape

    def clip_rails(self, aa, bb, ci, extra):
        edges = np.array([aa, bb], dtype=float)
        lo, hi = edges.min(axis=(0, 1)), edges.max(axis=(0, 1))
        zones = self.local_zones(ci, box(*lo, *hi), extra)
        if not len(zones):
            return None
        guard = unary_union(zones).buffer(extra, quad_segs=4)
        changed = False
        for idx, (a, b) in enumerate(zip(*edges)):
            cross = LineString([a, b])
            if cross.length < .06 or not guard.intersects(cross):
                continue
            safe = cross.difference(guard)
            for line in getattr(safe, 'geoms', [safe]):
                if line.geom_type == 'LineString' and line.length >= .65 * cross.length:
                    edges[:, idx] = [line.coords[0], line.coords[-1]]
                    changed = True
                    break
        return edges if changed else None

    def blocks_link(self, color, path):
        return bool(len(self.local_zones(color, path)))
