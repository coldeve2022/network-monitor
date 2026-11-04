(function(root){'use strict';
 function errorText(data,status){const detail=data?.error||data?.detail||('请求失败（HTTP '+status+'）');return Array.isArray(detail)?detail.map(item=>(item.loc||[]).join('.')+' '+item.msg).join('；'):String(detail);}
 function createClient({fetch,getToken}){
  async function execute(url,options={},parse=false){
   const {timeoutMs=10000,...settings}=options;const controller=new AbortController();
   const abort=()=>controller.abort();if(settings.signal){if(settings.signal.aborted)controller.abort();else settings.signal.addEventListener('abort',abort,{once:true});}
   const timer=setTimeout(abort,timeoutMs);const headers={...(settings.headers||{})};
   if(settings.method&&settings.method!=='GET')headers['X-NM-Token']=getToken();
   try{const response=await fetch(url,{...settings,headers,signal:controller.signal});if(!parse)return response;
    let data;try{data=await response.json()}catch(error){throw Error('服务响应无法读取，请确认程序仍在运行');}
    if(!response.ok||data?.error)throw Error(errorText(data,response.status));return data;
   }catch(error){if(error.name==='AbortError')throw Error('请求超时或已取消，请重试');throw error;}
   finally{clearTimeout(timer);settings.signal?.removeEventListener('abort',abort);}
  }
  return {request:(url,options)=>execute(url,options,false),json:(url,options)=>execute(url,options,true)};
 }
 const api={createClient,errorText};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.NMApi=api;
})(typeof window!=='undefined'?window:globalThis);
