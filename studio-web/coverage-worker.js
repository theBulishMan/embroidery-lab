import {analyzeCoverage} from './coverage.js';
import {renderThreadSurface} from './preview.js';
self.onmessage=async({data:{id,kind,view,lineWidth}})=>{
  try{
    if(kind==='thread-render'){
      const result=await renderThreadSurface(view,(w,h)=>new OffscreenCanvas(w,h),{progress:value=>self.postMessage({id,progress:value})});
      const bitmap=result.canvas.transferToImageBitmap();
      self.postMessage({id,bitmap,elapsedMs:result.elapsedMs},[bitmap]);return;
    }
    const result=analyzeCoverage(view,lineWidth,(w,h)=>new OffscreenCanvas(w,h));
    self.postMessage({id,result},[result.overlay.buffer]);
  }catch(error){self.postMessage({id,error:error.message})}
};
