// Kept identical in embroidery-lab and atelier-flow. Geometry is normalized
// against the image; selection handles are UI only, never part of AI markup.
export const MIN_RADIUS = .015;
export const MAX_RADIUS = .5;
const clamp = (n, low, high) => Math.max(low, Math.min(high, n));
const corners = [[-1, -1], [1, -1], [1, 1], [-1, 1]];

// Put the number outside the edit region, away from its resize handles.
export function markBadge(mark, width, height) {
  const radius = 9, gap = 5, r = mark.radius * Math.min(width, height);
  const x = mark.x * width, y = mark.y * height;
  const top = y - r - radius - gap, bottom = y + r + radius + gap;
  return {x: clamp(x, radius + 2, width - radius - 2),
    y: top >= radius + 2 ? top : bottom <= height - radius - 2 ? bottom : clamp(y - r + radius + gap, radius + 2, height - radius - 2), radius};
}

export function fitMark(mark, width, height) {
  const radius = clamp(mark.radius, MIN_RADIUS, MAX_RADIUS);
  const rx = radius * Math.min(width, height) / width;
  const ry = radius * Math.min(width, height) / height;
  return {...mark, radius, x: clamp(mark.x, rx, 1 - rx), y: clamp(mark.y, ry, 1 - ry)};
}

export function moveMark(mark, dx, dy, width, height) {
  return fitMark({...mark, x: mark.x + dx / width, y: mark.y + dy / height}, width, height);
}

export function resizeMark(mark, corner, dx, dy, width, height) {
  const [sx, sy] = corners[corner], scale = Math.min(width, height);
  const fitted = fitMark(mark, width, height), radius = fitted.radius * scale;
  // Hold the opposite corner in place and project the drag onto a square.
  const ax = fitted.x * width - sx * radius, ay = fitted.y * height - sy * radius;
  const limit = Math.min(sx > 0 ? width - ax : ax, sy > 0 ? height - ay : ay, scale);
  const side = clamp(2 * radius + (sx * dx + sy * dy) / 2, 2 * MIN_RADIUS * scale, limit);
  return {...mark, x: (ax + sx * side / 2) / width, y: (ay + sy * side / 2) / height, radius: side / (2 * scale)};
}

export class AnnotationCanvas {
  constructor(canvas, options) {
    this.canvas = canvas;
    this.options = options;
    this.drag = null;
    this.handlers = {
      pointerdown: e => this.down(e), pointermove: e => this.move(e),
      pointerup: e => this.up(e), pointercancel: () => this.cancel(),
      lostpointercapture: () => this.cancel(), keydown: e => this.key(e),
      contextmenu: e => e.preventDefault(),
    };
    Object.entries(this.handlers).forEach(([name, handler]) => canvas.addEventListener(name, handler));
    this.observer = new ResizeObserver(() => this.draw());
    this.observer.observe(canvas);
    this.draw();
  }
  state() { return this.options.getState(); }
  rect() { return this.canvas.getBoundingClientRect(); }
  hit(x, y, rect) {
    const {marks, selectedId} = this.state(), scale = Math.min(rect.width, rect.height);
    const selected = marks.find(m => m.id === selectedId);
    for (const mark of [...marks].reverse()) {
      const badge = markBadge(mark, rect.width, rect.height);
      if (Math.hypot(x - badge.x, y - badge.y) <= badge.radius + 2) return {mark, kind: 'move'};
    }
    if (selected) {
      const r = selected.radius * scale;
      // Tiny regions still need a movable center even when handle hit areas overlap.
      if (Math.hypot(x - selected.x * rect.width, y - selected.y * rect.height) < Math.min(8, r * .5))
        return {mark: selected, kind: 'move'};
      for (let corner = 0; corner < 4; corner++) {
        const [sx, sy] = corners[corner];
        if (Math.hypot(x - (selected.x * rect.width + sx * r), y - (selected.y * rect.height + sy * r)) <= 13)
          return {mark: selected, kind: 'resize', corner};
      }
    }
    for (const mark of [...marks].reverse()) {
      const dx = x - mark.x * rect.width, dy = y - mark.y * rect.height, r = mark.radius * scale;
      if (Math.hypot(dx, dy) <= r + 5 || (mark === selected && Math.abs(dx) <= r && Math.abs(dy) <= r))
        return {mark, kind: 'move'};
    }
    return null;
  }
  down(e) {
    e.stopPropagation();
    if (e.button !== 0 || this.state().disabled || this.drag) return;
    e.preventDefault();
    const rect = this.rect(), hit = this.hit(e.clientX - rect.left, e.clientY - rect.top, rect);
    this.canvas.focus({preventScroll: true});
    if (hit) {
      this.options.onSelect(hit.mark.id);
      this.options.onTool('select');
      this.drag = {...hit, mark: {...hit.mark}, pointer: e.pointerId, x: e.clientX, y: e.clientY, rect};
    } else if (this.state().tool === 'add') {
      if (this.state().marks.length >= (this.options.maxMarks ?? Infinity)) { this.options.onLimit?.(); return; }
      this.drag = {kind: 'add', pointer: e.pointerId, x: e.clientX, y: e.clientY, rect};
    } else this.options.onSelect('');
    if (this.drag) this.canvas.setPointerCapture(e.pointerId);
    this.draw();
  }
  move(e) {
    const drag = this.drag;
    if (!drag) {
      const rect = this.rect(), hit = this.hit(e.clientX - rect.left, e.clientY - rect.top, rect);
      this.canvas.style.cursor = this.state().disabled ? 'default' : hit?.kind === 'resize' ? (hit.corner % 2 ? 'nesw-resize' : 'nwse-resize') : hit ? 'move' : this.state().tool === 'add' ? 'crosshair' : 'default';
      return;
    }
    if (e.pointerId !== drag.pointer) return;
    e.preventDefault(); e.stopPropagation();
    if (this.state().disabled) { this.cancel(); return; }
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (Math.hypot(dx, dy) < 3 && !drag.moved) return;
    drag.moved = true;
    if (drag.kind !== 'add') {
      drag.preview = drag.kind === 'resize'
        ? resizeMark(drag.mark, drag.corner, dx, dy, drag.rect.width, drag.rect.height)
        : moveMark(drag.mark, dx, dy, drag.rect.width, drag.rect.height);
      this.draw();
    }
  }
  up(e) {
    const drag = this.drag;
    if (!drag || e.pointerId !== drag.pointer) return;
    this.move(e);
    this.drag = null;
    if (this.canvas.hasPointerCapture(e.pointerId)) this.canvas.releasePointerCapture(e.pointerId);
    if (this.state().disabled) { this.draw(); return; }
    if (drag.kind === 'add' && !drag.moved) {
      const mark = fitMark({id: crypto.randomUUID(), x: (e.clientX - drag.rect.left) / drag.rect.width, y: (e.clientY - drag.rect.top) / drag.rect.height, radius: this.state().radius ?? .08, text: ''}, drag.rect.width, drag.rect.height);
      this.options.onCommit([...this.state().marks, mark]);
      this.options.onSelect(mark.id); this.options.onTool('select');
    } else if (drag.preview) {
      this.options.onCommit(this.state().marks.map(m => m.id === drag.mark.id ? {...m, x: drag.preview.x, y: drag.preview.y, radius: drag.preview.radius} : m));
    }
    this.draw();
  }
  cancel() {
    const drag = this.drag; this.drag = null;
    if (drag && this.canvas.hasPointerCapture(drag.pointer)) this.canvas.releasePointerCapture(drag.pointer);
    this.draw();
  }
  key(e) {
    const state = this.state();
    if (state.disabled) return;
    const mark = state.marks.find(m => m.id === state.selectedId);
    if (e.key === 'Escape') {
      e.preventDefault(); e.stopPropagation();
      if (this.drag) this.cancel(); else { this.options.onSelect(''); this.options.onTool('select'); this.draw(); }
      return;
    }
    if (['Delete', 'Backspace'].includes(e.key)) {
      e.preventDefault(); e.stopPropagation();
      if (mark) { this.options.onCommit(state.marks.filter(m => m.id !== mark.id)); this.options.onSelect(''); this.draw(); }
      return;
    }
    if (mark && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) {
      e.preventDefault(); e.stopPropagation();
      const rect = this.rect(), step = e.shiftKey ? 10 : 1;
      const next = moveMark(mark, e.key === 'ArrowLeft' ? -step : e.key === 'ArrowRight' ? step : 0, e.key === 'ArrowUp' ? -step : e.key === 'ArrowDown' ? step : 0, rect.width, rect.height);
      this.options.onCommit(state.marks.map(m => m.id === mark.id ? next : m)); this.draw();
    }
  }
  draw() {
    const rect = this.rect(); if (!rect.width || !rect.height) return;
    const {canvas} = this, dpr = window.devicePixelRatio || 1;
    const width = Math.round(rect.width * dpr), height = Math.round(rect.height * dpr);
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
    const ctx = canvas.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, rect.width, rect.height);
    const state = this.state(), scale = Math.min(rect.width, rect.height);
    const marks = state.marks.map(m => this.drag?.preview && m.id === this.drag.mark.id ? this.drag.preview : m);
    const selectedId = this.drag?.mark?.id || state.selectedId;
    marks.forEach((m, i) => {
      const x = m.x * rect.width, y = m.y * rect.height, r = m.radius * scale;
      ctx.strokeStyle = '#8053d5'; ctx.lineWidth = 1.5;
      ctx.fillStyle = m.id === selectedId ? '#8053d508' : '#8053d502';
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    });
    const selected = marks.find(m => m.id === selectedId);
    if (selected && !state.disabled) {
      const x = selected.x * rect.width, y = selected.y * rect.height, r = selected.radius * scale;
      ctx.strokeStyle = '#8053d5'; ctx.lineWidth = 1; ctx.setLineDash([4, 3]); ctx.strokeRect(x - r, y - r, r * 2, r * 2); ctx.setLineDash([]);
      corners.forEach(([sx, sy]) => { const hx = clamp(x + sx * r, 4, rect.width - 4), hy = clamp(y + sy * r, 4, rect.height - 4); ctx.fillStyle = 'white'; ctx.fillRect(hx - 3, hy - 3, 6, 6); ctx.lineWidth = 1.5; ctx.strokeRect(hx - 3, hy - 3, 6, 6); });
    }
    // Paint labels last as well: handles must never erase part of a number.
    marks.forEach((mark, i) => {
      const badge = markBadge(mark, rect.width, rect.height);
      ctx.fillStyle = '#8053d5'; ctx.beginPath(); ctx.arc(badge.x, badge.y, badge.radius, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = 'white'; ctx.font = '600 12px system-ui, sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(String(i + 1), badge.x, badge.y);
    });
  }
  destroy() { this.cancel(); this.observer.disconnect(); Object.entries(this.handlers).forEach(([name, handler]) => this.canvas.removeEventListener(name, handler)); }
}
