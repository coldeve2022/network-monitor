"""TCP connection attempts with bounded publication and shutdown."""
import socket
import threading
import time
from core.probe_health import guarded_run
import config
from core.ping_probe import wait_interval
from core.scheduler import BoundedJobs

class TcpProbe(threading.Thread):
    def __init__(self,on_result):
        super().__init__(daemon=True,name='tcp-probe')
        self.on_result=on_result; self._stop_event=threading.Event(); self._jobs=BoundedJobs()
    def stop(self): self._stop_event.set()
    def _one(self,t):
        ts=time.time();start=time.perf_counter();state='ok';error=None;ms=None
        try:
            with socket.create_connection((t['host'],t['port']),timeout=config.TCP_TIMEOUT):
                ms=round((time.perf_counter()-start)*1000,1)
        except socket.gaierror as exc:state='resolution_error';error=str(exc.errno)
        except ConnectionRefusedError:state='connection_refused';error='connection_refused'
        except TimeoutError:state='sent_timeout';error='timeout'
        except OSError as exc:state='local_error';error=type(exc).__name__
        return dict(ts=ts,started_at=ts,finished_at=time.time(),type='tcp',state=state,error_code=error,ms=ms,**t)
    @guarded_run('tcp')
    def run(self):
        while not self._stop_event.is_set():
            started=time.monotonic();snapshot=config.protocol_snapshot();revision=snapshot['revision'];targets=[{**t,'probe_revision':revision} for t in snapshot['settings']['tcp_targets']];seen=set()
            for key,row in self._jobs.collect(targets,lambda t:(t['probe_revision'],t['name']),self._one,config.TCP_TIMEOUT+.5,self._stop_event):
                if self._stop_event.is_set():return
                if row is not None:self.on_result(row);seen.add(row['name'])
            if self._stop_event.is_set():return
            for t in targets:
                if t['name'] not in seen:self.on_result(dict(ts=time.time(),type='tcp',ms=None,state='skipped',error_code='round_budget',**t))
            wait_interval(self._stop_event,'tcp_interval',started)
