"""Probe process health is independent from target reachability."""
import functools
import logging
import threading
import time
import config


class ProbeHealth:
    def __init__(self):self._rows={};self._lock=threading.Lock()
    def record_sample(self,protocol,ts,state='ok'):
        with self._lock:
            row=self._rows.setdefault(protocol,{'error_count':0})
            row.update(last_sample=ts,last_received_mono=time.monotonic(),status='degraded' if state in ('skipped','local_error','cancelled') else 'running')
    def record_round(self,protocol,samples):
        if any(s.get('state') in ('skipped','local_error','resolution_error','cancelled') for s in samples):
            with self._lock:self._rows.setdefault(protocol,{})['status']='degraded'

    def record_error(self,protocol,code,ts):
        with self._lock:
            row=self._rows.setdefault(protocol,{'error_count':0})
            row.update(last_error=code,last_error_at=ts,status='degraded',error_count=row['error_count']+1)
    def snapshot(self,now):
        with self._lock:rows={k:dict(v) for k,v in self._rows.items()}
        for protocol in ('ping','tcp','dns','traffic'):
            row=rows.setdefault(protocol,{'error_count':0,'status':'waiting'})
            fresh=max(15,config.get(protocol+'_interval',1)*3+config.get('ping_timeout',2))
            row['fresh_for']=fresh
            if row.get('last_sample') is not None:
                if row['last_sample']>now+5:row['status']='clock_skew'
                elif now-row['last_sample']>fresh or time.monotonic()-row.get('last_received_mono',time.monotonic())>fresh:row['status']='stale'
        return rows


def report_error(callback,protocol,exc):
    owner=getattr(callback,'__self__',None)
    if owner is not None and hasattr(owner,'report_probe_error'):
        owner.report_probe_error(protocol,type(exc).__name__)
    logging.getLogger(__name__).error('%s probe failed: %s',protocol,type(exc).__name__)


def guarded_run(protocol):
    def decorate(run):
        @functools.wraps(run)
        def guarded(self):
            while not self._stop_event.is_set():
                try:
                    run(self)
                    return
                except Exception as exc:
                    report_error(self.on_result,protocol,exc)
                    self._stop_event.wait(.2)
        return guarded
    return decorate
