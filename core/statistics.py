"""Merge sufficient statistics instead of averaging means or deviations."""
import math
from core.models import loss_eligible


def accumulate(stats,item):
    kind=item['type'];value=item.get('avg') if kind=='ping' else item.get('ms')
    stats.setdefault('count',0);stats.setdefault('ok',0)
    if item.get('session_id') and item.get('sequence'):
        marks=stats.setdefault('watermarks',{});marks[item['session_id']]=max(marks.get(item['session_id'],0),item['sequence'])
    if kind=='ping' and not loss_eligible(item) or item.get('state') in ('skipped','cancelled','local_error'):
        stats['skipped']=stats.get('skipped',0)+1
        return
    stats['count']+=1
    if kind=='ping':
        stats['loss_sum']=stats.get('loss_sum',0)+(100 if value is None else 0)
        stats['loss_count']=stats.get('loss_count',0)+1
    if kind in ('ping','tcp','dns') and value is not None:
        stats['ok']+=1
        field='avg' if kind=='ping' else 'ms'
        stats[field]=stats.get(field,0)+value
        stats['sum_sq']=stats.get('sum_sq',0)+value*value
        stats['min']=value if stats.get('min') is None else min(stats['min'],value)
        stats['max']=value if stats.get('max') is None else max(stats['max'],value)
        jitter=item.get('jitter')
        if kind=='ping' and jitter is not None:
            stats['jitter']=stats.get('jitter',0)+jitter
            stats['jitter_count']=stats.get('jitter_count',0)+1
    if kind=='traffic':
        dt=item.get('duration',1)
        stats['duration']=stats.get('duration',0)+dt
        for side in ('rx','tx'):
            field=side+'_bps';stats[field]=stats.get(field,0)+item[field]
            stats[side+'_bytes']=stats.get(side+'_bytes',0)+item.get(side+'_bytes',item[field]*dt)
            stats[side+'_peak']=max(stats.get(side+'_peak',item[field]),item[field])


def merge_stats(parts):
    result={'count':0,'ok':0}
    peaks=('max','rx_peak','tx_peak')
    for part in parts:
        for key,value in part.items():
            if key=='watermarks':
                marks=result.setdefault(key,{})
                for session,sequence in value.items():marks[session]=max(marks.get(session,0),sequence)
            elif key=='legacy_approximate':result[key]=result.get(key,False) or value
            elif key in peaks and value is not None:result[key]=max(result.get(key,value),value)
            elif key=='min' and value is not None:result[key]=min(result.get(key,value),value)
            elif isinstance(value,(int,float)) and not isinstance(value,bool):result[key]=result.get(key,0)+value
    return result


def summarize(stats,metric='ping'):
    count=stats.get('count',0);ok=stats.get('ok',0)
    if metric=='traffic':
        duration=stats.get('duration',0)
        return {**{s+'_bps':stats.get(s+'_bytes',0)/duration if duration else stats.get(s+'_bps',0)/count if count else 0 for s in ('rx','tx')},
                'rx_peak':stats.get('rx_peak',0),'tx_peak':stats.get('tx_peak',0)}
    total=stats.get('avg' if metric=='ping' else 'ms',0)
    avg=total/ok if ok else None
    variance=max(0,stats.get('sum_sq',total*total/ok if ok else 0)/ok-avg*avg) if ok else None
    loss_count=stats.get('loss_count',count)
    loss=stats.get('loss_sum',(count-ok)*100)/loss_count if loss_count else None
    jitter_count=stats.get('jitter_count',count)
    jitter=stats.get('jitter',0)/jitter_count if jitter_count and ok else None
    std=math.sqrt(variance) if variance is not None else None
    return {'avg':avg,'min':stats.get('min'),'max':stats.get('max'),'loss':loss,
            'jitter':jitter,'std':std,'ms':avg,'sample_count':count,'success_count':ok,
            'failure_count':count-ok,'skipped_count':stats.get('skipped',0),
            'approximate':bool(stats.get('legacy_approximate',False))}


def quality_summary(stats):
    a=summarize(stats);avg=a['avg'];loss=a['loss'];jitter=a['jitter']
    def rounded(v,n=1):return round(v,n) if v is not None else None
    return {'latency_avg':rounded(avg),'loss_avg':rounded(loss,2),'jitter_avg':rounded(jitter),
            'std_avg':rounded(a['std']),'cv':rounded(a['std']/avg*100 if avg else 0 if avg==0 else None),
            'mos':rounded(max(1,min(5,4.5-.005*avg-.1*(loss or 0)-.005*(jitter or 0))) if avg is not None else None,2),
            'availability':rounded(100-loss if loss is not None else None),
            'sample_count':a['sample_count'],'success_count':a['success_count'],'approximate':a['approximate']}
