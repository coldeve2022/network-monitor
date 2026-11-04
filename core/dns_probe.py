"""Public DNS attempts, errors and timing are retained separately."""
import threading
import time
import dns.resolver
from core.probe_health import guarded_run
import config
from core.ping_probe import wait_interval
from core.scheduler import BoundedJobs

class DnsProbe(threading.Thread):
    def __init__(self,on_result):
        super().__init__(daemon=True,name='dns-probe')
        self.on_result=on_result; self._stop_event=threading.Event(); self._jobs=BoundedJobs()
    def stop(self):self._stop_event.set()
    def _one(self,target):
        server,query,revision=target
        ts=time.time();start=time.perf_counter();ms=None;state='ok';error=None
        resolver=dns.resolver.Resolver(configure=False);resolver.nameservers=[server]
        resolver.timeout=resolver.lifetime=config.DNS_TIMEOUT
        try:
            resolver.resolve(query,'A');ms=round((time.perf_counter()-start)*1000,1)
        except dns.resolver.NXDOMAIN:state='resolution_error';error='NXDOMAIN'
        except dns.exception.Timeout:state='sent_timeout';error='timeout'
        except Exception as exc:state='resolution_error';error=type(exc).__name__
        return dict(ts=ts,started_at=ts,finished_at=time.time(),type='dns',server=server,query=query,probe_revision=revision,ms=ms,state=state,error_code=error)
    @guarded_run('dns')
    def run(self):
        while not self._stop_event.is_set():
            started=time.monotonic();snapshot=config.protocol_snapshot();revision=snapshot['revision'];settings=snapshot['settings'];targets=[(server,settings['dns_query_host'],revision) for server in settings['dns_servers']];seen=set()
            for key,row in self._jobs.collect(targets,lambda t:t,self._one,config.DNS_TIMEOUT+.5,self._stop_event):
                if self._stop_event.is_set():return
                if row is not None:self.on_result(row);seen.add(row['server'])
            if self._stop_event.is_set():return
            for server in settings['dns_servers']:
                if server not in seen:self.on_result(dict(ts=time.time(),type='dns',server=server,query=settings['dns_query_host'],probe_revision=revision,ms=None,state='skipped',error_code='round_budget'))
            wait_interval(self._stop_event,'dns_interval',started)
