"""Every client owns a bounded send queue; slow clients cannot block others."""
import asyncio

class BroadcastHub:
    def __init__(self,capacity=256):
        self.capacity=capacity;self.queues={};self.tasks={};self.closing=set();self.dropped=0
    def subscribe(self,client):
        self.unsubscribe(client)
        queue=asyncio.Queue(maxsize=self.capacity);self.queues[client]=queue
        self.tasks[client]=asyncio.create_task(self._send(client,queue))
    async def _send(self,client,queue):
        try:
            while True:
                data=await queue.get()
                await asyncio.wait_for(client.send_json(data),timeout=2)
        except asyncio.CancelledError:raise
        except Exception:self.unsubscribe(client,close=True)
    async def _close_client(self,client):
        try:await asyncio.wait_for(client.close(code=1013),timeout=1)
        except Exception:pass
    def unsubscribe(self,client,close=False):
        self.queues.pop(client,None)
        task=self.tasks.pop(client,None)
        if task and task is not asyncio.current_task():task.cancel()
        if close:
            task=asyncio.create_task(self._close_client(client));self.closing.add(task);task.add_done_callback(self.closing.discard)
    def publish(self,data):
        for client,queue in list(self.queues.items()):
            try:queue.put_nowait(data)
            except asyncio.QueueFull:
                self.dropped+=1;self.unsubscribe(client,close=True)
    async def close(self):
        tasks=list(self.tasks.values())+list(self.closing)
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        self.tasks.clear();self.queues.clear();self.closing.clear()
