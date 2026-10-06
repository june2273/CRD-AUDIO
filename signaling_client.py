"""클라우드 시그널링 접속 (스파이크 A: 외부망 연결 검증용).

호스트는 시그널링 Worker(signaling/)의 자기 방에 WebSocket 하나로 접속해 있다가, 클라이언트가 보낸
암호화된 offer를 받아 answer를 돌려준다. 열린 포트 없이 외부망에서도 WebRTC를 협상할 수 있다.

보안: 페어링 키(pairKey)는 이 맥과 QR을 스캔한 기기만 안다. SDP는 AES-GCM(AAD=hostId)으로
암호화되어 서버는 내용을 읽거나 바꿀 수 없다. 서버는 호스트 인증값의 해시만 기억한다.
"""
import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
from pathlib import Path

import aiohttp
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger("sidecar.signal")
PAIRING_FILE = Path.home() / ".config/crd-audio/pairing.json"


def b64e(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def load_pairing(reset=False):
    """hostId(128bit)와 pairKey(256bit)를 처음 한 번 만들어 저장. reset이면 새로 발급(기존 기기 무효)."""
    if PAIRING_FILE.exists() and not reset:
        return json.loads(PAIRING_FILE.read_text())
    PAIRING_FILE.parent.mkdir(parents=True, exist_ok=True)
    p = {"h": secrets.token_hex(16), "k": b64e(secrets.token_bytes(32))}
    PAIRING_FILE.write_text(json.dumps(p))
    os.chmod(PAIRING_FILE, 0o600)
    return p


def pairing_link(signal_url, p):
    base = signal_url.replace("wss://", "https://", 1).replace("ws://", "http://", 1).rstrip("/")
    return f"{base}/#h={p['h']}&k={p['k']}"


def print_qr(link):
    import qrcode
    qr = qrcode.QRCode(border=1)
    qr.add_data(link)
    qr.print_ascii(invert=True)


class SignalingClient:
    def __init__(self, signal_url, pairing, answer_fn):
        self.url = signal_url.rstrip("/")
        self.hostid = pairing["h"]
        self.aes = AESGCM(b64d(pairing["k"]))
        self.aad = self.hostid.encode()
        self.auth = hashlib.sha256(b64d(pairing["k"]) + b"host-auth").hexdigest()
        self.answer_fn = answer_fn  # async (sdp, label) -> answer sdp

    def seal(self, obj):
        iv = secrets.token_bytes(12)
        return {"iv": b64e(iv), "ct": b64e(self.aes.encrypt(iv, json.dumps(obj).encode(), self.aad))}

    def unseal(self, m):
        return json.loads(self.aes.decrypt(b64d(m["iv"]), b64d(m["ct"]), self.aad))

    async def run(self):
        url = f"{self.url}/ws/{self.hostid}?role=host&auth={self.auth}"
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    async with session.ws_connect(url, heartbeat=30) as ws:
                        log.info("signaling connected (%s)", self.url)
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                asyncio.create_task(self._handle(ws, json.loads(msg.data)))
                    log.warning("signaling closed (%s) — 3초 후 재접속", ws.close_code)
                except aiohttp.WSServerHandshakeError as e:
                    if e.status == 403:
                        log.error("시그널링 서버가 호스트 인증을 거부 — 이 hostId를 다른 맥이 쓰는 중? --reset-pairing")
                        return
                    log.warning("signaling handshake failed (%s) — 3초 후 재시도", e.status)
                except aiohttp.ClientError as e:
                    log.warning("signaling error: %s — 3초 후 재시도", e)
                await asyncio.sleep(3)

    async def _handle(self, ws, m):
        if "error" in m:
            return
        peer = m.get("from")
        try:
            offer = self.unseal(m)
        except Exception:
            log.warning("복호화 실패 (peer %s) — 페어링 키가 다른 기기", peer)
            return
        if offer.get("type") != "offer" or offer.get("peer") != peer:
            return
        sdp = await self.answer_fn(offer["sdp"], f"signal:{peer}")
        await ws.send_str(json.dumps({"to": peer, **self.seal({"type": "answer", "sdp": sdp, "peer": peer})}))
