import unittest
from core.diagnostics import diagnose
from core.evaluator import Evaluator

class QualityTests(unittest.TestCase):
    def test_severe_loss_and_tcp_failure_cannot_be_healthy(self):
        snapshot={'targets':[{'name':'a','host':'a','kind':'base'}], 'latest_by_target':{
            'ping':{'a':{'ts':1000,'avg':20,'loss':95}},'tcp':{'service':{'ts':1000,'ms':None}}}}
        result=diagnose(snapshot,1000)
        self.assertEqual(result['level'],'warning')
        self.assertIn('TCP',' '.join(result['findings']))
        self.assertIn('95',' '.join(result['findings']))
    def test_cooldown_does_not_discard_incident_facts(self):
        events=[];e=Evaluator(events.append)
        e._fire('latency_spike','first')
        e._fire('latency_spike','second')
        self.assertEqual(len(events),2)
        self.assertTrue(events[0]['notify']);self.assertFalse(events[1]['notify'])
