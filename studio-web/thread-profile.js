// Working coverage widths, not a conversion from denier to measured diameter.
export const THREAD_PROFILES={
  '75D':{width:.20,label:'75D · 较细',detail:'参考线宽 0.20 mm'},
  '105D':{width:.24,label:'105D · 较粗',detail:'参考线宽 0.24 mm'},
};
export const referenceWidth=thread=>THREAD_PROFILES[thread]?.width??.24;
export function validThreadWidth(value){return Number.isFinite(value)&&value>=.10&&value<=.45}
