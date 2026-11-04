"""Local/AI consumers share exact, non-overlapping range summaries."""
import time
from core.statistics import merge_stats,quality_summary,summarize

def build_analysis_summary(collector,hours):
    ov=collector.overview();now=time.time()
    with collector._lock:
        current_key=collector._minute_key
        current={k:dict(v) for k,v in collector._minutes.get(current_key,{}).items()}
    summary={'hours':hours,'as_of':now,'status':ov['status'],'targets':[],
        'traces':ov['traces'],'probe_health':ov.get('probe_health',{}),'storage':ov.get('storage',{}),'uptime':ov.get('uptime'),'protocols':{},
        'events':[{'ts':ts,'type':kind,'message':message} for ts,kind,message in collector.db.recent_events(100)
                  if now-hours*3600<=ts<=now][-15:]}
    cached={}
    for target in ov['targets']:
        host=target['host']
        if host not in cached:
            live=current.get(('ping',host))
            history=collector.db.query_stats('ping',hours,host,end_before=current_key if live else None)
            combined=merge_stats([history,live or {}]);q=quality_summary(combined);raw=summarize(combined)
            cached[host]={
                'avg_ms':q['latency_avg'],'max_ms':raw['max'],'loss_pct':q['loss_avg'],
                'jitter_ms':q['jitter_avg'],'std_ms':q['std_avg'],'samples':q['sample_count'],
                'sample_count':q['sample_count'],'approximate':q['approximate'],
                'current':quality_summary(live) if live else None,
                'scope':'已完成历史与当前分钟，无重叠合并；旧桶缺计数时为近似',
                'as_of':now,'requested_start':now-hours*3600}
        if cached[host]['samples']:
            summary['targets'].append({'name':target['name'],'host':host,'kind':target['kind'],**cached[host]})
    for protocol in ('tcp','dns'):
        summary['protocols'][protocol]=[
            {**sample,'fresh':-5<=now-sample['ts']<=180} for sample in ov.get('latest_by_target',{}).get(protocol,{}).values()]
    return summary
