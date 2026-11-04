"""Shared validation and identities for configured endpoints."""
import ipaddress,re,uuid

def valid_host(value):
    if not isinstance(value,str):raise ValueError('主机必须为字符串')
    host=value.strip().rstrip('.').lower()
    try:return str(ipaddress.IPv4Address(host))
    except ValueError:pass
    try:host=host.encode('idna').decode('ascii')
    except UnicodeError:raise ValueError('请输入有效IP或域名') from None
    if len(host)>253 or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',host) or re.fullmatch(r'[0-9.]+',host):
        raise ValueError('请输入有效IPv4或域名，不含协议、端口和路径')
    return host

def base_target(name,host,identity=None):
    host=valid_host(host)
    return dict(name=str(name or host)[:100],host=host,target_id=identity or uuid.uuid4().hex,
                kind='base',trace_of=None,hop=None,protected=False)
