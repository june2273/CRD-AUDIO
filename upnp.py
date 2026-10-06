"""UPnP IGD 포트 매핑 (실험: TURN 사용량 줄이기).

공유기에 "공인IP:포트 → 맥:포트" UDP 매핑을 요청하고, 그 공인 주소를 ICE 후보로 answer에 넣는다.
통신사 NAT 뒤의 폰도 맥에 직접 닿을 수 있어 TURN 중계 없이 P2P가 될 가능성이 커진다.
공유기 UPnP가 꺼져 있거나 이중 NAT면 효과 없음 — 그때는 TURN 폴백.
의존성 없이 SSDP 검색 + SOAP 요청만 구현.
"""
import asyncio
import logging
import re
import socket
from urllib.parse import urljoin, urlparse

import aiohttp

log = logging.getLogger("sidecar.upnp")
SERVICES = ("urn:schemas-upnp-org:service:WANIPConnection:1", "urn:schemas-upnp-org:service:WANIPConnection:2")
MSEARCH = (b'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\nMX: 2\r\n'
           b'ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n')
LEASE = 3600  # 초. 연결 종료 시 삭제. 영구 매핑만 받는 공유기(예: NETGEAR R6350)는 시작 시 cleanup()으로 정리


def _ssdp(timeout):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(MSEARCH, ("239.255.255.250", 1900))
        while True:
            m = re.search(rb"(?im)^location:\s*(\S+)", s.recvfrom(4096)[0])
            if m:
                return m.group(1).decode()
    except socket.timeout:
        return None
    finally:
        s.close()


def _local_ip_towards(host):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 1900))
        return s.getsockname()[0]
    finally:
        s.close()


class Gateway:
    def __init__(self, control_url, service):
        self.control_url = control_url
        self.service = service
        self.local_ip = _local_ip_towards(urlparse(control_url).hostname)
        self.external_ip = None

    @classmethod
    async def find(cls, timeout=3):
        location = await asyncio.get_running_loop().run_in_executor(None, _ssdp, timeout)
        if not location:
            return None
        async with aiohttp.ClientSession() as s:
            xml = await (await s.get(location, timeout=aiohttp.ClientTimeout(total=5))).text()
        for service in SERVICES:
            m = re.search(rf"<serviceType>{re.escape(service)}</serviceType>.*?<controlURL>([^<]+)</controlURL>", xml, re.S)
            if m:
                gw = cls(urljoin(location, m.group(1)), service)
                gw.external_ip = (await gw._soap("GetExternalIPAddress", {})).get("NewExternalIPAddress")
                return gw
        return None

    async def _soap(self, action, args):
        body = "".join(f"<{k}>{v}</{k}>" for k, v in args.items())
        envelope = (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                    f's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                    f'<u:{action} xmlns:u="{self.service}">{body}</u:{action}></s:Body></s:Envelope>')
        headers = {"Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{self.service}#{action}"'}
        async with aiohttp.ClientSession() as s:
            async with s.post(self.control_url, data=envelope, headers=headers,
                              timeout=aiohttp.ClientTimeout(total=5)) as r:
                text = await r.text()
        if r.status != 200:
            code = re.search(r"<errorCode>(\d+)</errorCode>", text)
            raise RuntimeError(f"{action} 실패 (UPnP 오류 {code.group(1) if code else r.status})")
        return dict(re.findall(r"<(New\w+)>([^<]*)</New\w+>", text))

    async def add_udp(self, port, desc="crd-audio"):
        """외부 포트 = 내부 포트로 매핑. 성공하면 외부 포트, 실패하면 None."""
        args = {"NewRemoteHost": "", "NewExternalPort": port, "NewProtocol": "UDP", "NewInternalPort": port,
                "NewInternalClient": self.local_ip, "NewEnabled": 1, "NewPortMappingDescription": desc,
                "NewLeaseDuration": LEASE}
        try:
            await self._soap("AddPortMapping", args)
        except RuntimeError as e:
            if "725" not in str(e):  # 725: 영구 매핑만 지원하는 공유기 → 기간 0으로 재시도
                log.warning("%s (포트 %d)", e, port)
                return None
            await self._soap("AddPortMapping", {**args, "NewLeaseDuration": 0})
        return port

    async def cleanup(self, desc="crd-audio"):
        """이전 실행이 비정상 종료돼 남은 이 맥의 매핑 삭제 (영구 매핑만 받는 공유기 대비)."""
        stale, i = [], 0
        while i < 128:
            try:
                e = await self._soap("GetGenericPortMappingEntry", {"NewPortMappingIndex": i})
            except RuntimeError:
                break  # 713: 목록 끝
            if e.get("NewPortMappingDescription") == desc and e.get("NewInternalClient") == self.local_ip:
                stale.append(int(e["NewExternalPort"]))
            i += 1
        for port in stale:
            await self.delete_udp(port)
        return stale

    async def delete_udp(self, port):
        try:
            await self._soap("DeletePortMapping", {"NewRemoteHost": "", "NewExternalPort": port, "NewProtocol": "UDP"})
        except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("매핑 삭제 실패 (포트 %d): %s", port, e)


def srflx_candidate(ip, port, rel_ip, rel_port):
    # 우선순위 계산은 RFC 8445 (srflx type preference 100, component 1)
    priority = (100 << 24) + (65535 << 8) + 255
    return f"a=candidate:upnp1 1 udp {priority} {ip} {port} typ srflx raddr {rel_ip} rport {rel_port}"
