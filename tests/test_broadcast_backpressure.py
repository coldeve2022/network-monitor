import asyncio,unittest

class BackpressureTests(unittest.TestCase):
    def test_slow_client_cannot_delay_fast_client(self):
        from web.broadcast import BroadcastHub
        async def scenario():
            fast=[];gate=asyncio.Event()
            class Fast:
                async def send_json(self,data):fast.append(data['n'])
                async def close(self,**kwargs):pass
            class Slow:
                async def send_json(self,data):await gate.wait()
                async def close(self,**kwargs):pass
            hub=BroadcastHub(capacity=4)
            hub.subscribe(Slow());hub.subscribe(Fast())
            try:
                for n in range(30):
                    hub.publish({'n':n});await asyncio.sleep(.001)
                await asyncio.sleep(.02)
                self.assertIn(29,fast)
                self.assertTrue(all(q.qsize()<=4 for q in hub.queues.values()))
            finally:await hub.close()
        asyncio.run(scenario())
