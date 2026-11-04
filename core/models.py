"""Versioned wire contract. Legacy metric fields remain readable during migration."""
from dataclasses import dataclass
import math

VALID_STATES = frozenset(('ok','sent_timeout','resolution_error','connection_refused','skipped','local_error','cancelled'))


@dataclass(frozen=True)
class ProbeOutcome:
    state: str
    value: float | None = None
    error_code: str | None = None
    started_at: float | None = None
    finished_at: float | None = None


def loss_eligible(item):
    return item.get('state', 'ok' if item.get('avg') is not None else 'sent_timeout') in ('ok','sent_timeout')


def normalize_sample(item):
    result=dict(item)
    protocol=result['type']
    value=result.get('avg') if protocol=='ping' else result.get('ms')
    if protocol=='traffic': value=result.get('rx_bps')
    if isinstance(value,bool) or value is not None and (not isinstance(value,(float,int)) or not math.isfinite(value)):
        value=None
        result['avg' if protocol=='ping' else 'rx_bps' if protocol=='traffic' else 'ms']=None
        result['state']='local_error'
    result.setdefault('state','ok' if value is not None else 'sent_timeout')
    if result['state'] not in VALID_STATES: result['state']='local_error'
    result.setdefault('schema_version',2)
    result.setdefault('protocol',protocol)
    identity=result.get('host') or result.get('name') or result.get('server') or 'all'
    if protocol=='tcp' and result.get('host') and result.get('port'):
        identity=f"tcp:{result.get('name')}:{result['host']}:{result['port']}"
        result['storage_key']=f"{result.get('name')} ({result['host']}:{result['port']})"
    elif protocol=='dns' and result.get('query'):
        identity=f"dns:{result['server']}:{result['query']}"
        result['storage_key']=f"{result['server']} ({result['query']})"
    result.setdefault('target_id',identity)
    result.setdefault('started_at',result['ts'])
    result.setdefault('finished_at',result['ts'])
    result.setdefault('scheduled_at',result['ts'])
    result.setdefault('config_revision',0)
    return result
