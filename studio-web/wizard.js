const $ = selector => document.querySelector(selector);

const steps = {
  1: ['准备图案', '选择图片或输入描述，再按成品尺寸简化。', '图案来源'],
  2: ['确认平面稿', '核对轮廓和主花纹，选定要绣的版本。', '修改图案'],
  3: ['设置绣制工艺', '核对成品尺寸，选择绣线、密度和底料。', '绣制参数'],
  4: ['分区与针法', '在图上点选色块，可以拆分、合并或单独改针法。', '分区与针法'],
  5: ['模拟与导出', '对照平面稿与实际针迹，定位需要调整的部位。', '针版检查']
};

const panels = [
  [2, ['#detail-review', '#edit-section', '.version-section', '#palette-editor']],
  [3, ['#material-settings', '#material-tuning', '#material-advice']],
  [4, ['#craft-help', '#detail-fit-panel', '#local-editor', '.make-section']],
  [5, ['#needle-review', '#craft-section', '#thread-list-details', '#thread-colors-section', '#download-section', '#history-controls']],
];

const views = {
  artwork: [2, 3, 4, 5], compare: [2, 3], original: [2], layers: [4], overlay: [4],
  local: [4], simulation: [5], result: [5], coverage: [5]
};

function canViewDuringPlan(c, step) {
  return c.planning && !c.dirty && !c.pending && step >= 2 && step <= 4;
}

export class Wizard {
  constructor({context, mode, newDesign, make, message, preview}) {
    Object.assign(this, {context, mode, newDesign, make, message, preview});
    this.step = 2;
    $('#inspector-mode-toggle').onclick = () => {
      const basic = document.body.dataset.inspectorTool === 'basic';
      const initialTool = this.step === 3 ? 'tuning' : this.context().dirty ? 'palette' : 'edit';
      this.showInspectorTool(basic ? initialTool : 'basic');
    };
    for (const button of document.querySelectorAll('[data-inspector-tab]')) {
      button.onclick = () => this.showInspectorTool(button.dataset.inspectorTab);
    }
    $('#open-draft-review').onclick = () => {
      this.showInspectorTool('review');
      $('#draft-tools [data-inspector-tab="review"]').focus();
    };
    $('#material-size-hint').onclick = () => {
      this.showInspectorTool('size');
      $('#material-tools [data-inspector-tab="size"]').focus();
    };
    $('#art-draft-summary').onclick = () => {
      this.showInspectorTool('edit');
      $('#edit-prompt').focus();
    };
    for (const button of document.querySelectorAll('[data-step-target]')) {
      button.onclick = () => this.go(Number(button.dataset.stepTarget));
    }
    for (const button of document.querySelectorAll('#mobile-panes [data-pane]')) {
      button.onclick = () => this.showPane(button.dataset.pane, true);
    }
    $('#return-to-canvas').onclick = () => this.showPane('canvas', true);
    $('#wizard-back').onclick = () => this.go(this.step - 1);
    $('#wizard-next').onclick = () => this.next();
  }

  showInspectorTool(tool) {
    const expanded = tool !== 'basic';
    document.body.dataset.inspectorTool = tool;
    $('#inspector-mode-toggle').setAttribute('aria-controls', this.step === 3 ? 'material-tools' : 'draft-tools');
    $('#inspector-mode-toggle').setAttribute('aria-expanded', String(expanded));
    $('#inspector-mode-toggle').textContent = expanded ? '收起高级模式' : '高级模式';
    $('#draft-tools').hidden = !expanded || this.step !== 2;
    $('#material-tools').hidden = !expanded || this.step !== 3;
    for (const button of document.querySelectorAll('[data-inspector-tab]')) {
      button.setAttribute('aria-pressed', String(button.dataset.inspectorTab === tool));
    }
    $('.inspector').scrollTop = 0;
  }

  showPane(pane, scroll = false) {
    document.body.dataset.pane = pane;
    for (const button of document.querySelectorAll('#mobile-panes [data-pane]')) {
      button.setAttribute('aria-pressed', String(button.dataset.pane === pane));
    }
    if (scroll && window.matchMedia('(max-width: 800px)').matches) {
      window.scrollTo({top: 0, behavior: 'instant'});
    }
    if (pane === 'canvas') this.preview.render();
  }

  next() {
    const c = this.context();
    if (c.locked && canViewDuringPlan(c, this.step + 1)) { this.go(this.step + 1); return; }
    if (c.locked || c.dirty || c.pending) return;
    if (this.step === 4 && c.valid && c.craftAction) { c.craftAction.run(); return; }
    if (this.step === 4 && !c.plan) {
      if (c.valid) this.make();
      return;
    }
    this.go(this.step + 1);
  }

  go(step, force = false) {
    if (!steps[step]) return;
    const c = this.context();
    if (!force && (c.locked || c.dirty || c.pending) && !canViewDuringPlan(c, step)) {
      this.message(c.dirty ? '请先保存或还原配色。' : '请先应用或放弃当前修改。');
      return;
    }
    if (step === 1 && !force) { this.newDesign(); return; }
    if (step >= 2 && !c.version) return;
    if (step === 4 && !c.valid) { this.message('请先填写有效尺寸、线材和密度。'); return; }
    if (step === 5 && !c.plan) { this.message('先在“分区与针法”生成针迹，再查看模拟。'); return; }
    const entering = this.step !== step;
    if (entering) $('.inspector').scrollTop = 0;
    this.step = step;
    if ((entering || force) && (step === 2 || step === 3)) this.showInspectorTool('basic');
    document.body.dataset.step = String(step);
    this.preview.showDimensions = step >= 2;
    this.showPane(step === 3 ? 'inspector' : 'canvas');
    this.mode(step === 5 ? 'result' : step === 4 ? (c.plateMode === 'ai' ? 'local' : 'layers') : 'artwork');
    this.refresh();
    if (!c.locked) this.message(steps[step][1]);
    if (!force) window.scrollTo({top: 0, behavior: 'instant'});
  }

  refresh() {
    const c = this.context(), s = this.step, stop = c.locked || c.dirty || c.pending;
    const [name, description, properties] = steps[s];
    $('#step-kicker').textContent = `第 ${s} 步 / 5`;
    $('#step-title').textContent = name;
    $('#step-description').textContent = c.planning ? '本版正在排针，可以查看图稿、工艺和下一轮制版方式。' : description;
    $('#inspector-title').textContent = properties;
    const paneButton = $('#mobile-panes [data-pane="inspector"]');
    paneButton.textContent = properties;
    for (const b of document.querySelectorAll('[data-step-target]')) {
      const n = Number(b.dataset.stepTarget);
      b.setAttribute('aria-current', n === s ? 'step' : 'false');
      b.disabled = (stop && !canViewDuringPlan(c, n)) || (n >= 2 && !c.version) || (n === 4 && !c.valid) || (n === 5 && !c.plan);
    }
    for (const [step, selectors] of panels) {
      for (const selector of selectors) $(selector).dataset.wizardHidden = String(s !== step);
    }
    $('#advanced-compare').dataset.wizardHidden = String(s !== 4);
    $('#preview-stats').dataset.wizardHidden = String(s < 2);
    for (const [view, activeSteps] of Object.entries(views)) {
      $(`[data-view="${view}"]`).dataset.wizardHidden = String(!activeSteps.includes(s));
    }
    $('#wizard-footer').hidden = s === 1;
    $('#wizard-back').textContent = s === 2 ? '重新选图' : s === 5 ? '← 返回分区与针法' : '← 上一步';
    $('#wizard-back').disabled = stop && !canViewDuringPlan(c, s - 1);
    $('#wizard-download').hidden = s !== 5 || !c.plan || c.dirty || c.pending;
    if (c.plan) {
      $('#wizard-download').href = c.plan.downloads['工程包'];
      $('#wizard-download').textContent = c.assessment?.canTrial ? '下载试绣工程 ↓' :
        ['adjust', 'blocked'].includes(c.assessment?.status) ? '下载试排稿（需调整）' : '下载试排稿（待评估）';
    }
    $('#wizard-next').hidden = s === 5;
    $('#wizard-next').disabled = (stop && !canViewDuringPlan(c, s + 1)) || !c.version || ([3, 4].includes(s) && !c.valid);
    $('#wizard-next').textContent = s === 2 ? '确认图案，设置工艺 →' : s === 3 ? '下一步：分区与针法 →' : c.plan ? '查看模拟与导出 →' : '生成针迹，查看模拟 →';
    if (s === 4 && c.craftAction) $('#wizard-next').textContent = c.craftAction.label;
    if (c.planning && s >= 2 && s <= 4) $('#wizard-next').textContent = s === 2 ? '查看本版工艺 →' : s === 3 ? '查看制版进度 →' : '正在生成本次针版…';
    $('#wizard-footer-note').textContent = s === 5 ? '工程须结合实物试绣核对。' : c.dirty ? '请先保存配色' : c.pending ? '请先应用或放弃局部修改' : '';
  }
}
