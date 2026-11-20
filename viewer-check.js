
  (() => {
    const root = document.getElementById('rabbit-stitch-test');
    const data = JSON.parse(document.getElementById('rabbit-test-data').textContent);
    const find = s => root.querySelector(s);
    const canvas = find('[data-canvas]'), ctx = canvas.getContext('2d');
    const photo = find('[data-original]'), range = find('[data-progress]'), play = find('[data-play]');
    const commands = data.commands, colors = data.meta.colors;
    let mode = 'simulation', index = commands.length, playing = false, frame = 0, previousTime = 0, debt = 0;
    let layer = 'all', showJumps = false;
    canvas.width = 840; canvas.height = Math.round(840 * data.height / data.width);
    const scale = canvas.width / data.width;
    photo.src = data.originalDataUri;
    range.max = commands.length; range.value = index;
    find('[data-summary]').textContent = `4 色 · ${data.meta.separated_objects.length} 色块 · ${data.stats.stitch_count.toLocaleString()} 针`;
    const status = text => { if (find('[data-status]').textContent !== text) find('[data-status]').textContent = text; };
    const shade = (rgb, mix) => rgb.map(v => Math.round(mix < 0 ? v * (1 + mix) : v + (255 - v) * mix));
    const css = rgb => `rgb(${rgb.join(',')})`;
    const bg = () => {
      // The fixed colors here represent the physical fabric and thread sample.
      ctx.fillStyle = '#f1ede4'; ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.lineWidth = .65; ctx.strokeStyle = '#e7e3da'; ctx.beginPath();
      for (let x = 0; x < canvas.width; x += 5) { ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height); }
      for (let y = 0; y < canvas.height; y += 5) { ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); }
      ctx.stroke();
    };
    const segment = (a, b, color, width, offset = 0) => {
      ctx.strokeStyle = color; ctx.lineWidth = width * scale; ctx.lineCap = 'round';
      ctx.beginPath(); ctx.moveTo((a[0] + offset) * scale, (a[1] + offset) * scale);
      ctx.lineTo((b[0] + offset) * scale, (b[1] + offset) * scale); ctx.stroke();
    };
    function paint() {
      if (!playing) play.textContent = index >= commands.length ? '从头播放' : '播放';
      photo.hidden = mode !== 'original'; canvas.hidden = mode === 'original';
      find('[data-layer-controls]').hidden = mode !== 'layers';
      find('[data-play-controls]').hidden = mode !== 'simulation';
      if (mode === 'original') { status('原始照片，用于核对造型与表情'); return; }
      bg();
      if (mode === 'layers') {
        ctx.save(); ctx.scale(scale, scale);
        data.meta.separated_objects.forEach(o => {
          const c = colors[o.color];
          ctx.globalAlpha = layer === 'all' || Number(layer) === o.color ? 1 : .06;
          ctx.fillStyle = c.hex; ctx.fill(new Path2D(o.path), 'evenodd');
        });
        ctx.restore();
        status(layer === 'all' ? `${data.meta.separated_objects.length} 个独立色块，合用 4 种线色` : `${colors[Number(layer)].name}：${data.meta.separated_objects.filter(o => o.color === Number(layer)).length} 个色块`);
        return;
      }
      let last = null, needle = null, currentColor = 0;
      const until = mode === 'result' ? commands.length : index;
      for (let i = 0; i < until; i++) {
        const p = commands[i];
        if (p[2] === 0 && last) {
          const col = colors[p[3]].thread.rgb;
          if (mode === 'result') segment(last, p, css(shade(col, -.26)), .42, .045);
          segment(last, p, css(col), mode === 'result' ? .34 : .28);
          if (mode === 'result') segment(last, p, css(shade(col, .22)), .075, -.035);
          needle = p; currentColor = p[3];
        } else if (p[2] === 1 && last && showJumps && mode === 'simulation') {
          ctx.setLineDash([3, 3]); segment(last, p, '#d35b34', .12); ctx.setLineDash([]);
        }
        last = p;
      }
      if (mode === 'simulation' && needle && until < commands.length) {
        ctx.beginPath(); ctx.arc(needle[0] * scale, needle[1] * scale, 5, 0, Math.PI * 2);
        ctx.fillStyle = '#d65424'; ctx.fill(); ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 1.5; ctx.stroke();
      }
      find('[data-progress-label]').textContent = `${index ? data.prefix[index - 1].toLocaleString() : 0} / ${data.stats.stitch_count.toLocaleString()} 针`;
      range.value = index;
      status(mode === 'result' ? '效果由机器文件的落针坐标绘制，实物仍需试绣' : `${colors[currentColor].name} · 示例线号 ${colors[currentColor].thread.code} · ${index >= commands.length ? '已播放完成' : '可逐针检查'}`);
    }
    function save() {
      window.openai?.setWidgetState?.({modelContent:{sample:'rabbit',mode,stitch:index ? data.prefix[index - 1] : 0},privateContent:{mode,index,layer}})?.catch?.(() => {});
    }
    function stop() {
      playing = false; cancelAnimationFrame(frame); play.textContent = index >= commands.length ? '从头播放' : '播放';
    }
    function changeMode(value, persist = true) {
      stop(); mode = value;
      root.querySelectorAll('[data-mode]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.mode === mode)));
      paint(); if (persist) save();
    }
    const tick = time => {
      if (!playing) return;
      if (previousTime) debt += (time - previousTime) / 1000 * Number(find('[data-speed]').value);
      previousTime = time;
      const amount = Math.floor(debt);
      if (amount) { index = Math.min(commands.length, index + amount); debt -= amount; paint(); }
      if (index >= commands.length) { stop(); save(); return; }
      frame = requestAnimationFrame(tick);
    };
    root.querySelectorAll('[data-mode]').forEach(b => b.addEventListener('click', () => changeMode(b.dataset.mode)));
    play.addEventListener('click', () => {
      if (playing) { stop(); save(); return; }
      if (index >= commands.length) index = 0;
      playing = true; previousTime = 0; debt = 0; play.textContent = '暂停'; frame = requestAnimationFrame(tick);
    });
    range.addEventListener('input', () => { stop(); index = Number(range.value); paint(); });
    range.addEventListener('change', save);
    find('[data-step]').addEventListener('click', () => {
      stop(); if (index >= commands.length) index = 0;
      do { index++; } while (index < commands.length && commands[index - 1][2] !== 0);
      paint(); save();
    });
    find('[data-layer-select]').addEventListener('change', e => { layer = e.target.value; paint(); save(); });
    find('[data-jumps]').addEventListener('change', e => { showJumps = e.target.checked; paint(); });
    colors.forEach((color, i) => {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'btn';
      const swatch = document.createElement('span'); swatch.className = 'rabbit-swatch'; swatch.style.backgroundColor = color.thread.hex; swatch.setAttribute('aria-hidden', 'true');
      button.append(swatch, document.createTextNode(`${i + 1}. ${color.name} ${color.thread.code}`));
      button.addEventListener('click', () => {
        if (mode === 'layers') { layer = String(i); find('[data-layer-select]').value = layer; paint(); save(); }
        else { changeMode('simulation', false); index = commands.findIndex(p => p[3] > i); if (index < 0) index = commands.length; paint(); save(); }
      });
      find('[data-thread-buttons]').appendChild(button);
    });
    function restore(state) {
      const stored = state?.privateContent;
      if (!stored) return;
      if (Number.isFinite(stored.index)) index = Math.max(0, Math.min(commands.length, stored.index));
      if (['all','0','1','2','3'].includes(stored.layer)) { layer = stored.layer; find('[data-layer-select]').value = layer; }
      if (['original','layers','simulation','result'].includes(stored.mode)) changeMode(stored.mode, false);
    }
    restore(window.openai?.widgetState);
    window.addEventListener('openai:set_globals', event => restore(event.detail?.globals?.widgetState));
    paint();
  })();
  