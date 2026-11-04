import tempfile,time,unittest
from pathlib import Path
from core.collector import Collector
from tests.qa_paths import isolated_paths
from web.server import _collect_analyze_summary

class AnalysisTests(unittest.TestCase):
    def test_existing_history_does_not_hide_current_failure(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp,isolated_paths(Path(tmp)):
            c=Collector()
            try:
                target=c.ping_targets()[0];now=time.time();minute=int(now//60)*60
                c.db.flush_minute(minute-60,{'ping':{target['host']:{'avg':10,'min':10,'max':10,'loss':0,'jitter':0,'std':0}}})
                c.on_result(dict(ts=now,type='ping',avg=None,loss=100,jitter=0,std=0,**target))
                result=next(x for x in _collect_analyze_summary(c,1)['targets'] if x['host']==target['host'])
                self.assertGreater(result['loss_pct'],0)
                self.assertEqual(result.get('current',{}).get('loss_avg'),100)
            finally:c.close()
    def test_nullable_legacy_fields_do_not_crash_summary(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp,isolated_paths(Path(tmp)):
            c=Collector()
            try:
                target=c.ping_targets()[0];minute=int(time.time()//60)*60-60
                c.db.flush_minute(minute,{'ping':{target['host']:{'avg':10,'min':10,'max':10,'loss':None,'jitter':None,'std':None}}})
                result=_collect_analyze_summary(c,1)
                self.assertTrue(result['targets'])
            finally:c.close()
