import assert from 'node:assert/strict';
import test from 'node:test';
import {Wizard} from './studio-web/wizard.js';
import {LocalEditor} from './studio-web/local-editor.js';

test('switching modes protects manual edits and invalidates parked AI choices at a different size',()=>{
  const context={width:20,thread:'75D',fabric:'twill',project_id:'p',version_id:'v'};
  const editor=Object.assign(Object.create(LocalEditor.prototype),{context:()=>context,cuts:[],regions:[{id:'petal'},{id:'edge'}],
    overrides:{petal:{method:'satin',source:'ai'},edge:{method:'run'}},form(){},onChange(){}});
  editor.switchPlanMode(false);assert.deepEqual(editor.overrides,{edge:{method:'run'}});
  editor.switchPlanMode(true);assert.equal(editor.overrides.petal.method,'satin');
  editor.switchPlanMode(false);editor.overrides.petal={method:'fill'};
  editor.switchPlanMode(true);assert.deepEqual(editor.overrides.petal,{method:'fill'});
  editor.overrides.petal={method:'satin',source:'ai'};editor.switchPlanMode(false);context.width=80;
  editor.switchPlanMode(true);assert.equal(editor.overrides.petal,undefined);
  const manualHistory=[{overrides:{edge:{method:'run'}}}];
  editor.history=manualHistory;
  editor.switchPlanMode(false);editor.switchPlanMode(true);
  assert.equal(editor.history,manualHistory);
});

function setup(){
  const elements=new Map();
  const el=selector=>{if(!elements.has(selector))elements.set(selector,{dataset:{},setAttribute(k,v){this[k]=v}});return elements.get(selector)};
  const nav=Array.from({length:5},(_,i)=>({...el('step'+i),dataset:{stepTarget:String(i+1)}}));
  const panes=['canvas','inspector'].map(pane=>Object.assign(el(`#mobile-panes [data-pane="${pane}"]`),{dataset:{pane}}));
  const body={dataset:{}};
  globalThis.document={body,querySelector:s=>s===`[data-pane="${body.dataset.pane}"]`?body:el(s),
    querySelectorAll:s=>s==='[data-step-target]'?nav:s==='#mobile-panes [data-pane]'?panes:[]};
  globalThis.window={scrollTo(){},matchMedia:()=>({matches:true})};
  const c={version:{id:'v1',color_count:4},plan:null,valid:true,locked:false,dirty:false,pending:false};
  let plans=0;const notices=[];
  const wizard=new Wizard({context:()=>c,mode(){},newDesign(){},make(){plans++},message:t=>notices.push(t),preview:{render(){}}});
  wizard.go(2,true);
  return {wizard,c,el,nav,notices,get plans(){return plans}};
}

test('confirmation and sizing lead to a separate craft step without generating stitches',()=>{
  const s=setup();s.wizard.next();assert.equal(s.wizard.step,3);assert.equal(s.plans,0);
  assert.match(s.el('#wizard-next').textContent,/分区与针法/);
  s.wizard.next();assert.equal(s.wizard.step,4);assert.equal(s.plans,0);
  assert.equal(s.el('#craft-help').dataset.wizardHidden,'false');
  assert.equal(s.el('#material-settings').dataset.wizardHidden,'true');
  s.wizard.next();assert.equal(s.plans,1);assert.equal(s.wizard.step,4);
});

test('draft and material settings start compact and use distinct advanced tools',()=>{
  const s=setup(), toggle=s.el('#inspector-mode-toggle');
  toggle.onclick();
  assert.equal(document.body.dataset.inspectorTool,'edit');
  assert.equal(s.el('#draft-tools').hidden,false);
  s.wizard.refresh();
  assert.equal(toggle['aria-expanded'],'true');
  s.wizard.next();
  assert.equal(document.body.dataset.inspectorTool,'basic');
  assert.equal(s.el('#draft-tools').hidden,true);
  toggle.onclick();
  assert.equal(document.body.dataset.inspectorTool,'tuning');
  assert.equal(s.el('#material-tools').hidden,false);
  s.wizard.go(2);
  assert.equal(s.el('#material-tools').hidden,true);
  assert.equal(toggle['aria-expanded'],'false');
});
test('simulation needs a completed plan and returns to craft for adjustments',()=>{
  const s=setup();s.wizard.go(4);s.wizard.go(5);assert.equal(s.wizard.step,4);
  assert.equal(s.nav[4].disabled,true);
  s.c.plan={stitch_count:1234,downloads:{'工程包':'/test.zip'}};s.wizard.go(5,true);
  assert.equal(s.wizard.step,5);assert.equal(s.el('#wizard-download').hidden,false);
  assert.equal(s.el('#craft-help').dataset.wizardHidden,'true');
  s.el('#wizard-back').onclick();assert.equal(s.wizard.step,4);
  s.wizard.next();assert.equal(s.wizard.step,5);assert.equal(s.plans,0);
});
test('invalid dimensions or unapplied edits cannot start a stitch job',()=>{
  const s=setup();s.wizard.go(3);s.c.valid=false;s.wizard.next();assert.equal(s.wizard.step,3);assert.equal(s.plans,0);
  s.c.valid=true;s.wizard.go(4);s.c.pending=true;s.wizard.next();assert.equal(s.plans,0);
  s.c.pending=false;s.c.locked=true;s.wizard.next();assert.equal(s.plans,0);
});
test('changed settings require regeneration before simulation',()=>{
  const s=setup();s.c.plan={stitch_count:1234,downloads:{'工程包':'/test.zip'}};s.wizard.go(5);
  s.c.plan=null;s.wizard.go(3);s.wizard.next();assert.equal(s.wizard.step,4);
  assert.equal(s.nav[4].disabled,true);s.wizard.next();assert.equal(s.plans,1);
});
test('an active stitch job lets the user view its artwork and settings without starting another job',()=>{
  const s=setup();
  s.c.locked=true;s.c.planning=true;s.wizard.go(4,true);
  assert.deepEqual(s.nav.map(button=>button.disabled),[true,false,false,false,true]);
  s.nav[1].onclick();assert.equal(s.wizard.step,2);
  assert.equal(s.el('#wizard-back').disabled,true);
  assert.equal(s.el('#wizard-next').textContent,'查看本版工艺 →');
  s.wizard.next();assert.equal(s.wizard.step,3);
  s.wizard.next();assert.equal(s.wizard.step,4);
  assert.equal(s.el('#wizard-next').disabled,true);
  s.wizard.next();assert.equal(s.plans,0);
});
test('switching the phone inspector keeps the page intact and returns to the drawing',()=>{
  const s=setup();s.nav[2].onclick();
  assert.equal(s.wizard.step,3);
  assert.equal(document.body.dataset.pane,'inspector');
  assert.equal(document.body.textContent,undefined);
  assert.equal(s.el('#mobile-panes [data-pane="inspector"]').textContent,'绣制参数');
  s.el('#return-to-canvas').onclick();
  assert.equal(document.body.dataset.pane,'canvas');
  assert.equal(s.el('#mobile-panes [data-pane="canvas"]')['aria-pressed'],'true');
});
