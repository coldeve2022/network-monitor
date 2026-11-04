"""Bounded, cancellable publication around otherwise uncancellable OS calls."""
import concurrent.futures
import threading
import time


class BoundedJobs:
    def __init__(self, capacity=16):
        self.capacity=capacity
        self.jobs={}
        self.cursor=0

    def collect(self, targets, key, worker, timeout, stop):
        self.jobs={k:v for k,v in self.jobs.items() if not v.done()}
        ordered=list(targets)
        if ordered:
            offset=self.cursor%len(ordered);ordered=ordered[offset:]+ordered[:offset]
            self.cursor+=1
        futures={}
        for target in ordered:
            identity=key(target)
            if identity in self.jobs or len(self.jobs)>=self.capacity:continue
            future=concurrent.futures.Future()
            def work(t=target,f=future):
                try:f.set_result(worker(t))
                except BaseException as exc:f.set_exception(exc)
            self.jobs[identity]=futures[identity]=future
            threading.Thread(target=work,daemon=True,name='bounded-probe-worker').start()
        deadline=time.monotonic()+timeout
        while futures and not stop.is_set():
            ready=[k for k,v in futures.items() if v.done()]
            for identity in ready:
                future=futures.pop(identity)
                try:yield identity,future.result()
                except Exception:yield identity,None
            if not futures or time.monotonic()>=deadline:break
            stop.wait(min(.02,max(0,deadline-time.monotonic())))


def wait_until_interval(stop, interval, started):
    while not stop.is_set():
        remaining=interval()-(time.monotonic()-started)
        if remaining<=0:return False
        stop.wait(min(.2,remaining))
    return True
