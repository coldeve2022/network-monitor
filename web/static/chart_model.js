(function(root){'use strict';
 function resolveViewWindow(state,latestMs,nowMs){const end=Number.isFinite(latestMs)?latestMs:nowMs;return state.mode==='follow'?{xStart:end-state.span_ms,xEnd:end}:{xStart:state.start_ms,xEnd:state.end_ms};}
 function mergeSeries(history,raw,rawStart,threshold,historicalThreshold=()=>90000){
  const merged=new Map();for(const point of history)if(point[0]<rawStart)merged.set(point[0],point);for(const point of raw)merged.set(point[0],point);
  const result=[];for(const point of [...merged.values()].sort((a,b)=>a[0]-b[0])){const previous=result.at(-1);const maxGap=previous&&previous[0]>=rawStart&&point[0]>=rawStart?threshold:previous?historicalThreshold(previous,point):90000;if(previous&&point[0]-previous[0]>maxGap)result.push([previous[0]+1,null]);result.push(point);}return result;
 }
 const api={resolveViewWindow,mergeSeries};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.NMCharts=api;
})(typeof window!=='undefined'?window:globalThis);
