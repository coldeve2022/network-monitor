"""One threshold policy for measurement facts, diagnostics and display."""
import config
from core.models import loss_eligible

def thresholds():
    return dict(latency_warn=config.EVAL_LATENCY_MED,latency_bad=config.EVAL_LATENCY_BAD,
                loss_warn=config.EVAL_LOSS_MED,loss_bad=config.EVAL_LOSS_BAD)

def ping_quality(sample):
    if not loss_eligible(sample):return 'unknown'
    avg=sample.get('avg');loss=sample.get('loss') or 0
    if avg is None:return 'offline'
    if avg>config.EVAL_LATENCY_BAD or loss>config.EVAL_LOSS_BAD:return 'bad'
    if avg>config.EVAL_LATENCY_MED or loss>config.EVAL_LOSS_MED:return 'fair'
    return 'good'

def group_quality(samples):
    incomplete=any(not loss_eligible(s) for s in samples)
    samples=[s for s in samples if loss_eligible(s)]
    if not samples:return 'unknown'
    states=[ping_quality(s) for s in samples]
    if all(s=='offline' for s in states):return 'offline'
    if any(s in ('offline','bad') for s in states):return 'bad'
    return 'fair' if 'fair' in states else 'unknown' if incomplete else 'good'
