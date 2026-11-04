"""One bounded persistence owner. Acquisition never waits for SQLite or disk."""
import concurrent.futures,queue,threading,time

class StorageWriter:
    def __init__(self,db,on_error,capacity=512,on_success=None):
        self.db=db;self.on_error=on_error;self.queue=queue.Queue(maxsize=capacity)
        self.on_success=on_success
        self.condition=threading.Condition();self.submitted=0;self.completed=0
        self.stop_requested=threading.Event();self.closed=threading.Event();self.last_error=None;self.oldest_started=None
        self.thread=threading.Thread(target=self._run,name='storage-writer',daemon=True)
        db._writer=self;self.thread.start()
    def is_owner(self):return threading.current_thread() is self.thread
    def submit(self,action,args=(),kwargs=None,wait=False):
        future=concurrent.futures.Future()
        with self.condition:
            if self.stop_requested.is_set():raise RuntimeError('storage writer is stopping')
            number=self.submitted+1
            try:self.queue.put_nowait((number,action,args,kwargs or {},future))
            except queue.Full:
                if wait:raise TimeoutError('storage queue full')
                self.last_error='queue_full';return False
            self.submitted=number
        if wait:return future.result(timeout=5)
        return True
    def _run(self):
        try:
            while not self.stop_requested.is_set() or not self.queue.empty():
                try:number,action,args,kwargs,future=self.queue.get(timeout=.05)
                except queue.Empty:continue
                self.oldest_started=time.monotonic()
                try:
                    result=getattr(self.db,action)(*args,**kwargs)
                    if self.on_success:self.on_success()
                    self.last_error=None;future.set_result(result)
                except Exception as exc:
                    self.last_error=type(exc).__name__
                    try:self.on_error(action,args,kwargs,exc)
                    except Exception:pass
                    future.set_exception(exc)
                finally:
                    self.oldest_started=None
                    with self.condition:self.completed=number;self.condition.notify_all()
                    self.queue.task_done()
        finally:
            self.db._close_actual();self.closed.set()
    def drain(self,timeout=5):
        if self.is_owner():return
        deadline=time.monotonic()+timeout
        with self.condition:
            watermark=self.submitted
            while self.completed<watermark:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise TimeoutError('storage is not keeping up')
                self.condition.wait(remaining)
    def snapshot(self):
        return {'queued':self.queue.qsize(),'error':self.last_error,'write_age':time.monotonic()-self.oldest_started if self.oldest_started else 0,'submitted':self.submitted,'completed':self.completed}
    def close(self,timeout=5):
        self.stop_requested.set()
        if not self.is_owner():self.thread.join(timeout)
        return self.closed.is_set()

class ConnectionView:
    """Legacy direct SQL inspection has an explicit read-your-writes barrier."""
    def __init__(self,db):self.db=db
    def __getattr__(self,name):
        value=getattr(self.db._conn,name)
        if not callable(value):return value
        def call(*args,**kwargs):
            self.db._drain_external()
            return value(*args,**kwargs)
        return call
