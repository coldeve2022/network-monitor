"""ICMP echo traceroute：逐跳发现到目标的路径（PingPlotter 核心能力）。

与 Windows tracert 同机制：发送 ICMP echo 包，TTL 从 1 递增；
TTL 耗尽时中间路由器回 ICMP Time Exceeded（type 11），到达目标时回
 Echo Reply（type 0）。校验载荷中的 identifier 过滤无关包。
 注意：部分 Windows 环境创建 raw ICMP socket 需要管理员权限；失败时
 TraceManager 会捕获异常并把该目标按空路径处理。
"""
import os
import socket
import struct
import time

from ping3 import checksum


def _to_ip(host):
    if _is_ipv4(host):
        return host
    return socket.gethostbyname(host)


def _is_ipv4(s):
    try:
        socket.inet_aton(s)
        return True
    except OSError:
        return False


def _ip_header_length(data, start=0):
    if len(data) <= start or data[start] >> 4 != 4:
        return None
    length = (data[start] & 0x0f) * 4
    if length < 20 or len(data) < start + length:
        return None
    return length


def _reply_matches(data, ident, sequence):
    """Return ICMP type only for the current echo, respecting both IPv4 IHLs."""
    offset = _ip_header_length(data)
    if offset is None or len(data) < offset + 8:
        return None
    kind = data[offset]
    if kind == 0:
        echo = offset
    elif kind in (11, 3):
        inner = offset + 8
        inner_length = _ip_header_length(data, inner)
        if inner_length is None:
            return None
        echo = inner + inner_length
        if len(data) < echo + 8 or data[echo] != 8:
            return None
    else:
        return None
    received_id, received_seq = struct.unpack("!HH", data[echo + 4:echo + 8])
    return kind if (received_id, received_seq) == (ident, sequence) else None


def traceroute(dest, max_hops=24, timeout=1.0, max_idle=3, cancelled=None):
    """Discover one row per TTL, ignoring unrelated packets within its deadline."""
    cancelled = cancelled or (lambda: False)
    if cancelled():
        return []
    try:
        dest_ip = _to_ip(dest)
    except OSError:
        return []
    if cancelled():
        return []
    icmp = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    hops = []
    idle = 0
    try:
        icmp.bind(("0.0.0.0", 0))
        ident = os.getpid() & 0xFFFF
        for ttl in range(1, max_hops + 1):
            if cancelled():
                break
            payload = struct.pack("!d", time.time())
            header = struct.pack("!BBHHH", 8, 0, 0, ident, ttl)
            header = struct.pack("!BBHHH", 8, 0, socket.htons(checksum(header + payload)), ident, ttl)
            icmp.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, ttl)
            try:
                icmp.sendto(header + payload, (dest_ip, 0))
            except OSError:
                break
            deadline = time.monotonic() + timeout
            hop = None
            kind = None
            while not cancelled():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                icmp.settimeout(min(remaining, 0.2))
                try:
                    data, addr = icmp.recvfrom(512)
                except socket.timeout:
                    continue
                except OSError:
                    return hops
                matched = _reply_matches(data, ident, ttl)
                if matched is None or (matched == 0 and addr[0] != dest_ip):
                    continue
                hop, kind = addr[0], matched
                break
            if cancelled():
                break
            hops.append(hop)
            idle = idle + 1 if hop is None else 0
            if kind in (0, 3) or idle >= max_idle:
                break
    finally:
        icmp.close()
    return hops
