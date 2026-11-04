import unittest

from core.diagnostics import diagnose


class DiagnosticTests(unittest.TestCase):
    def snapshot(self, samples, now=1000):
        return {'targets': [{'host': h, 'name': h, 'kind': 'base'} for h in samples],
                'latest_by_target': {'ping': {h: {'avg': value, 'ts': ts} for h, (value, ts) in samples.items()}}}

    def test_no_samples_cannot_claim_healthy(self):
        result = diagnose(self.snapshot({}), now=1000)
        self.assertEqual(result['level'], 'unknown')

    def test_partial_failure_is_not_healthy(self):
        result = diagnose(self.snapshot({'a': (20, 999), 'b': (None, 999)}), now=1000)
        self.assertEqual(result['level'], 'warning')
        self.assertIn('b', ' '.join(result['findings']))

    def test_all_timeout_does_not_claim_isp_outage(self):
        result = diagnose(self.snapshot({'a': (None, 999)}), now=1000)
        self.assertEqual(result['level'], 'warning')
        self.assertIn('ICMP', ' '.join(result['findings']))

    def test_old_samples_cannot_claim_healthy(self):
        result = diagnose(self.snapshot({'a': (20, 800)}), now=1000)
        self.assertEqual(result['level'], 'unknown')

    def test_healthy_recent_samples(self):
        result = diagnose(self.snapshot({'a': (20, 999)}), now=1000)
        self.assertEqual(result['level'], 'good')


if __name__ == '__main__':
    unittest.main()
