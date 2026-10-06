"""Phase 1: BlackHole 2ch → Opus → WebRTC(aiortc) 오디오 사이드카 서버.

사용법:
  python server.py [--port 8080] [--device "BlackHole 2ch"]
  브라우저에서 http://<맥미니IP>:8080 열고 '연결' 클릭.

구조: 캡처(sounddevice 콜백 스레드) → Broadcaster(구독자별 asyncio 큐) → CaptureTrack.recv()
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


async def on_shutdown(app):
    app["stream"].stop()
    app["stream"].close()
    await asyncio.gather(*(pc.close() for pc in app["pcs"]))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--device", default="BlackHole 2ch")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    app = web.Application()
    app["device"] = args.device
    app["pcs"] = set()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
