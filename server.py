"""BlackHole 2ch 오디오 사이드카 서버.

사용법:
  python server.py [--port 8080] [--device "BlackHole 2ch"]
  데스크톱: http://<맥미니IP>:8080 열고 '연결' 클릭 (WebRTC, 저지연)
  모바일:   VLC에서 http://<맥미니IP>:8080/stream.mp3 열기 (HTTP 스트림, 백그라운드 재생)

구조: 캡처(sounddevice 콜백 스레드) → Broadcaster(구독자별 asyncio 큐)
       ├→ CaptureTrack.recv() → aiortc Opus → WebRTC
       └→ Mp3Encoder: ffmpeg stdin → MP3 → /stream.mp3 클라이언트들
     Phase 4에서는 Broadcaster에 프레임을 넣는 캡처 부분만 audiotee로 교체한다.
"""
import argparse
import asyncio
import fractions
import json
import logging
from pathlib import Path

import av
import numpy as np
import sounddevice as sd
from aiohttp import web
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription

RATE = 48000
CHANNELS = 2
FRAME = 960  # 20ms @ 48kHz — Opus 프레임 크기와 일치
MAX_QUEUE = 5  # 구독자당 최대 100ms 버퍼. 넘치면 오래된 프레임 버림 (지연 누적 방지)

log = logging.getLogger("sidecar")
ROOT = Path(__file__).parent


class Broadcaster:
    """캡처 스레드에서 받은 PCM 프레임을 각 피어의 큐로 나눠준다."""

    def __init__(self, loop):
        self.loop = loop
        self.queues = set()

    def subscribe(self):
        q = asyncio.Queue(MAX_QUEUE)
        self.queues.add(q)
        return q

    def unsubscribe(self, q):
        self.queues.discard(q)

    def _push(self, pcm):
        for q in self.queues:
            if q.full():
                q.get_nowait()
            q.put_nowait(pcm)

    def push_threadsafe(self, pcm):
        self.loop.call_soon_threadsafe(self._push, pcm)


class CaptureTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self, broadcaster):
        super().__init__()
        self.broadcaster = broadcaster
        self.queue = broadcaster.subscribe()
        self.pts = 0

    async def recv(self):
        pcm = await self.queue.get()
        frame = av.AudioFrame.from_ndarray(pcm.reshape(1, -1), format="s16", layout="stereo")
        frame.sample_rate = RATE
        frame.pts = self.pts
        frame.time_base = fractions.Fraction(1, RATE)
        self.pts += FRAME
        return frame

    def stop(self):
        super().stop()
        self.broadcaster.unsubscribe(self.queue)


class Mp3Encoder:
    """Broadcaster의 PCM을 ffmpeg 1개로 MP3 인코딩해 모든 HTTP 청취자에게 나눠준다."""

    def __init__(self, broadcaster, bitrate):
        self.broadcaster = broadcaster
        self.bitrate = bitrate
        self.clients = set()
        self.tasks = []

    async def start(self):
        self.proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "s16le", "-ar", str(RATE), "-ac", str(CHANNELS), "-i", "pipe:0",
            "-c:a", "libmp3lame", "-b:a", self.bitrate, "-f", "mp3", "-flush_packets", "1", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        self.tasks = [asyncio.create_task(self._feed()), asyncio.create_task(self._fanout())]
        log.info("mp3 encoder started (%s)", self.bitrate)

    async def _feed(self):
        q = self.broadcaster.subscribe()
        try:
            while True:
                self.proc.stdin.write((await q.get()).tobytes())
                await self.proc.stdin.drain()
        finally:
            self.broadcaster.unsubscribe(q)

    async def _fanout(self):
        while chunk := await self.proc.stdout.read(4096):
            for q in self.clients:
                if q.full():
                    q.get_nowait()  # 느린 클라이언트는 오래된 청크를 버림 (MP3 디코더는 프레임 경계에서 재동기화)
                q.put_nowait(chunk)
        log.error("ffmpeg exited (code %s)", await self.proc.wait())
        self._close_clients()

    def _close_clients(self):
        for q in self.clients:
            if q.full():
                q.get_nowait()
            q.put_nowait(None)  # 청취자 핸들러 종료 신호 — 없으면 종료 시 aiohttp가 60초간 대기

    async def stop(self):
        for t in self.tasks:
            t.cancel()
        self._close_clients()
        if self.proc.returncode is None:
            self.proc.kill()
            await self.proc.wait()


def start_capture(device, broadcaster):
    def callback(indata, frames, time_info, status):
        if status:
            log.warning("capture status: %s", status)
        broadcaster.push_threadsafe(indata.copy())

    stream = sd.InputStream(device=device, samplerate=RATE, channels=CHANNELS,
                            dtype="int16", blocksize=FRAME, callback=callback)
    stream.start()
    log.info("capturing from %s @ %d Hz", device, RATE)
    return stream


async def index(request):
    return web.FileResponse(ROOT / "static" / "index.html")


async def stream_mp3(request):
    encoder = request.app["mp3"]
    q = asyncio.Queue(64)  # 4KB × 64 ≈ 192kbps에서 약 10초
    encoder.clients.add(q)
    log.info("mp3 listener %s connected (%d)", request.remote, len(encoder.clients))
    resp = web.StreamResponse(headers={"Content-Type": "audio/mpeg", "Cache-Control": "no-cache"})
    await resp.prepare(request)
    try:
        while (chunk := await q.get()) is not None:
            await resp.write(chunk)
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        encoder.clients.discard(q)
        log.info("mp3 listener %s disconnected (%d)", request.remote, len(encoder.clients))
    return resp


async def offer(request):
    params = await request.json()
    pc = RTCPeerConnection()
    request.app["pcs"].add(pc)
    track = CaptureTrack(request.app["broadcaster"])
    pc.addTrack(track)

    @pc.on("connectionstatechange")
    async def on_state():
        log.info("peer %s: %s", request.remote, pc.connectionState)
        if pc.connectionState in ("failed", "closed"):
            track.stop()
            await pc.close()
            request.app["pcs"].discard(pc)

    await pc.setRemoteDescription(RTCSessionDescription(sdp=params["sdp"], type=params["type"]))
    await pc.setLocalDescription(await pc.createAnswer())
    return web.Response(content_type="application/json", text=json.dumps(
        {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}))


async def on_startup(app):
    app["broadcaster"] = Broadcaster(asyncio.get_running_loop())
    app["stream"] = start_capture(app["device"], app["broadcaster"])
    app["mp3"] = Mp3Encoder(app["broadcaster"], app["bitrate"])
    await app["mp3"].start()


async def on_shutdown(app):
    await app["mp3"].stop()
    app["stream"].stop()
    app["stream"].close()
    await asyncio.gather(*(pc.close() for pc in app["pcs"]))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--device", default="BlackHole 2ch")
    p.add_argument("--bitrate", default="192k", help="MP3 스트림 비트레이트")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    app = web.Application()
    app["device"] = args.device
    app["bitrate"] = args.bitrate
    app["pcs"] = set()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)
    app.router.add_get("/stream.mp3", stream_mp3)
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
