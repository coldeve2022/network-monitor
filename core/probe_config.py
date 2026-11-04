"""Validated protocol-specific configuration, kept separate from UI preferences."""
import ipaddress
from core.targets import valid_host

def validate(values, previous):
    keys={'tcp_targets','dns_servers','dns_query_host','trace_targets','traffic_interface'}
    if not isinstance(values,dict) or set(values)-keys:raise ValueError('未知专项检测设置')
    result={**previous,**values}
    tcp=result['tcp_targets']
    if not isinstance(tcp,list) or len(tcp)>100:raise ValueError('TCP目标最多100个')
    targets=[];names=set()
    for row in tcp:
        if not isinstance(row,dict) or set(row)-{'name','host','port'}:raise ValueError('TCP目标需要名称、主机和端口')
        name=row.get('name','');port=row.get('port')
        if isinstance(name,str):name=name.strip()
        if not isinstance(name,str) or not name.strip() or len(name)>100 or name in names:raise ValueError('TCP目标名称不能为空或重复')
        if isinstance(port,bool) or not isinstance(port,int) or not 1<=port<=65535:raise ValueError('端口范围1—65535')
        names.add(name);targets.append({'name':name.strip(),'host':valid_host(row.get('host')),'port':port})
    result['tcp_targets']=targets
    if not isinstance(result['dns_servers'],list) or len(result['dns_servers'])>16:raise ValueError('DNS服务器最多16个')
    try:result['dns_servers']=list(dict.fromkeys(str(ipaddress.IPv4Address(s)) for s in result['dns_servers']))
    except (ValueError,TypeError):raise ValueError('DNS服务器必须为IPv4地址') from None
    result['dns_query_host']=valid_host(result['dns_query_host'])
    if not isinstance(result['trace_targets'],list) or len(result['trace_targets'])>8:raise ValueError('路径目标最多8个')
    result['trace_targets']=list(dict.fromkeys(valid_host(s) for s in result['trace_targets']))
    if not isinstance(result['traffic_interface'],str) or len(result['traffic_interface'])>256:raise ValueError('网卡名称无效')
    return result
