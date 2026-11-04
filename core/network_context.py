"""Read-only active IPv4 route and interface context."""
import ipaddress,subprocess
import psutil

def detect_gateway():
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    try:text=subprocess.check_output(['route','print','-4'],text=True,timeout=1,creationflags=flags,encoding='gbk',errors='ignore')
    except Exception:return None
    routes=[]
    for line in text.splitlines():
        parts=line.split()
        if len(parts)>=5 and parts[:2]==['0.0.0.0','0.0.0.0']:
            try:gateway=str(ipaddress.IPv4Address(parts[2]));metric=int(parts[4])
            except (ValueError,TypeError):continue
            routes.append((metric,gateway))
    return min(routes)[1] if routes else None

def snapshot():
    return {'gateway':detect_gateway(),'interfaces':[{'name':name,'isup':value.isup,'speed_mbps':value.speed} for name,value in psutil.net_if_stats().items()]}
