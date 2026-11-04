"""Conservative local interpretation of actual probe results; no external requests."""
import time

import config
from core.quality_policy import ping_quality


def diagnose(snapshot, now=None):
    now = time.time() if now is None else now
    latest = snapshot.get('latest_by_target', {}).get('ping', {})
    fresh_for = max(15, config.get('ping_interval') * 3 + config.get('ping_timeout'))
    bases = {t['host']: t for t in snapshot.get('targets', []) if t.get('kind') == 'base'}
    findings, suggestions = [], []
    failed, slow, missing, lossy = [], [], [], []
    for host, target in bases.items():
        sample = latest.get(host)
        if not sample or not -5 <= now - sample['ts'] <= fresh_for or ping_quality(sample)=='unknown':
            missing.append(target['name'])
        elif sample.get('avg') is None:
            failed.append(target['name'])
        elif sample['avg'] > config.EVAL_LATENCY_MED:
            slow.append(target['name'])
        if sample and (sample.get('loss') or 0)>config.EVAL_LOSS_MED:
            lossy.append(target['name']+' '+str(sample['loss'])+'%')
    if not bases or len(missing) == len(bases):
        return {'level': 'unknown', 'title': '等待新的检测数据',
                'findings': ['当前没有足够的新鲜样本，无法判断网络质量。'],
                'suggestions': ['添加实际使用的服务器或域名，并等待至少几轮采样。'], 'ts': now}
    if failed:
        findings.append('ICMP 无响应：' + '、'.join(failed) + '。目标可能拒绝 ICMP，不能仅凭此确认业务断网。')
        suggestions.append('对比网关、多个公网目标及 TCP/DNS 检测；仅单个目标异常时优先核查目标自身。')
    if slow:
        findings.append('当前延迟偏高：' + '、'.join(slow) + '。')
        suggestions.append('在异常发生时对比有线与 Wi-Fi，并保存历史记录以观察持续时间。')
    if missing:
        findings.append('数据缺失或过期：' + '、'.join(missing) + '。')
    if lossy:
        findings.append('窗口ICMP丢包偏高：'+'、'.join(lossy)+'。一次成功响应不表示整个窗口已恢复。')
    tcp=snapshot.get('latest_by_target',{}).get('tcp',{})
    tcp_fail=[name for name,s in tcp.items() if -5<=now-s['ts']<=config.get('tcp_interval')*3+config.TCP_TIMEOUT and s.get('ms') is None]
    if tcp_fail:
        findings.append('TCP连接失败或未完成：'+'、'.join(tcp_fail)+'；请按对应业务核查端口和错误原因。')
        suggestions.append('对照TCP目标的超时、连接拒绝与解析错误，不将单个服务异常推断为全网断线。')
    dns = snapshot.get('latest_by_target', {}).get('dns', {})
    recent_dns = [s for s in dns.values() if -5 <= now - s['ts'] <= config.get('dns_interval') * 3 + config.DNS_TIMEOUT]
    if recent_dns and all(s.get('ms') is None for s in recent_dns):
        findings.append('当前 DNS 探测均无响应，可能是解析链路或对公共 DNS 的访问受限。')
        suggestions.append('对照系统 DNS 与当前网络策略，确认实际应用能否正常解析域名。')
    level = 'warning' if failed or slow or missing or lossy or tcp_fail or (recent_dns and all(s.get('ms') is None for s in recent_dns)) else 'good'
    return {'level': level, 'title': '发现需要关注的信号' if level == 'warning' else '当前探测未发现明显异常',
            'findings': findings or ['当前基础目标均响应，延迟在设定阈值内；这不代表所有应用均可用。'],
            'suggestions': suggestions or ['持续监测实际使用的目标，发生卡顿时对照对应时间段的历史和事件。'], 'ts': now}
