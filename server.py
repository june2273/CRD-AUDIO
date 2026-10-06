"""BlackHole 2ch 오디오 사이드카 서버.

사용법:
  python server.py [--port 8080] [--capture tap|blackhole]
  데스크톱: http://<맥미니IP>:8080 열고 '연결' 클릭 (WebRTC, 저지연)
  모바일:   VLC에서 http://<맥미니IP>:8080/stream.mp3 열기 (HTTP 스트림, 백그라운드 재생)

구조: 캡처 → Broadcaster(구독자별 asyncio 큐)
       ├→ CaptureTrack.recv() → aiortc Opus → WebRTC
       └→ Mp3Encoder: ffmpeg stdin → MP3 → /stream.mp3 클라이언트들
     캡처는 둘 중 하나 (Broadcaster에 20ms int16 스테레오 프레임을 넣는 것만 같으면 된다):
       - TapCapture (기본): audiotee(Core Audio 탭) stdout. 출력 장치를 바꿀 필요 없음
       - BlackHoleCapture: sounddevice로 BlackHole 2ch 입력 (시스템 출력이 BlackHole이어야 함)
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
from aiortc import (MediaStreamTrack, RTCConfiguration, RTCIceServer, RTCPeerConnection,
                    RTCSessionDescription)

import signaling_client

RATE = 48000
CHANNELS = 2
FRAME = 960  # 20ms @ 48kHz — Opus 프레임 크기와 일치
MAX_QUEUE = 5  # 구독자당 최대 100ms 버퍼. 넘치면 오래된 프레임 버림 (지연 누적 방지)
FRAME_BYTES = FRAME * CHANNELS * 2
# STUN: 외부망에서 공인 주소 후보(srflx)를 얻어 홀펀칭. aiortc는 첫 STUN 서버만 쓴다
ICE_CONFIG = RTCConfiguration(iceServers=[RTCIceServer(urls="stun:stun.cloudflare.com:3478")])
SILENCE_RESTART = 60 * RATE // FRAME  # 완전 무음 60초 → 탭 재생성 (장시간 무음 버퍼 버그·출력 장치 변경 대응)

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


class BlackHoleCapture:
    def __init__(self, broadcaster, device):
        self.broadcaster = broadcaster
        self.device = device

    async def start(self):
        def callback(indata, frames, time_info, status):
            if status:
                log.warning("capture status: %s", status)
            self.broadcaster.push_threadsafe(indata.copy())

        self.stream = sd.InputStream(device=self.device, samplerate=RATE, channels=CHANNELS,
                                     dtype="int16", blocksize=FRAME, callback=callback)
        self.stream.start()
        log.info("capturing from %s @ %d Hz", self.device, RATE)

    async def stop(self):
        self.stream.stop()
        self.stream.close()


class TapCapture:
    """audiotee(Core Audio 탭)를 하위 프로세스로 띄워 stdout PCM을 읽는다. 죽거나 무음이 길면 재시작.

    권한(TCC)은 서버를 실행한 앱(iTerm2 등)에 묶이고, 미허용이면 에러 없이 0만 들어온다.
    """

    def __init__(self, broadcaster, binary):
        self.broadcaster = broadcaster
        self.binary = binary
        self.proc = None
        self.task = None

    async def start(self):
        if not Path(self.binary).exists():
            raise SystemExit(f"audiotee 없음: {self.binary} — ./build_audiotee.sh 실행")
        self.task = asyncio.create_task(self._run())

    async def _run(self):
        silent_streak = 0  # 연속 재생성 횟수 — 로그 반복 방지용
        while True:
            self.proc = await asyncio.create_subprocess_exec(
                self.binary, "--stereo", "--sample-rate", str(RATE), "--chunk-duration", str(FRAME / RATE),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stderr_task = asyncio.create_task(self._log_stderr(self.proc.stderr))
            if not silent_streak:
                log.info("capturing system audio via audiotee (pid %d) @ %d Hz", self.proc.pid, RATE)
            zeros = 0
            try:
                while zeros < SILENCE_RESTART:
                    pcm = np.frombuffer(await self.proc.stdout.readexactly(FRAME_BYTES), dtype=np.int16)
                    self.broadcaster._push(pcm.reshape(-1, CHANNELS))
                    zeros = zeros + 1 if not pcm.any() else 0
                    if zeros == 0:
                        silent_streak = 0
                silent_streak += 1
                if silent_streak == 1:
                    log.info("60초간 완전 무음 → 탭 재생성 (무음이 계속되면 60초마다 조용히 반복)")
                await self._terminate()
            except asyncio.IncompleteReadError:
                code = await self.proc.wait()
                log.error("audiotee exited (code %s) — 1초 후 재시작", code)
                silent_streak = 0
                await asyncio.sleep(1)
            finally:
                stderr_task.cancel()

    @staticmethod
    async def _log_stderr(stream):
        # audiotee 로그는 JSON 줄. debug/info는 버리고 나머지만 남긴다
        while line := await stream.readline():
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("message_type") in ("error", "warning"):
                log.warning("audiotee: %s %s", msg.get("data", {}).get("message"), msg.get("data", {}).get("context", ""))

    async def _terminate(self):
        if self.proc.returncode is None:
            self.proc.terminate()  # SIGTERM → audiotee가 탭/aggregate 장치를 정리하고 종료
            try:
                await asyncio.wait_for(self.proc.wait(), 2)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()

    async def stop(self):
        if self.task:
            self.task.cancel()
        if self.proc:
            await self._terminate()


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


async def create_answer(app, sdp, label):
    """클라이언트 offer SDP → answer SDP. LAN(/offer)과 클라우드 시그널링이 공유한다."""
    pc = RTCPeerConnection(ICE_CONFIG)
    app["pcs"].add(pc)
    track = CaptureTrack(app["broadcaster"])
    pc.addTrack(track)

    @pc.on("connectionstatechange")
    async def on_state():
        log.info("peer %s: %s", label, pc.connectionState)
        if pc.connectionState in ("failed", "closed"):
            track.stop()
            await pc.close()
            app["pcs"].discard(pc)

    await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
    await pc.setLocalDescription(await pc.createAnswer())
    return pc.localDescription.sdp


async def offer(request):
    params = await request.json()
    sdp = await create_answer(request.app, params["sdp"], request.remote)
    return web.Response(content_type="application/json", text=json.dumps({"sdp": sdp, "type": "answer"}))


async def on_startup(app):
    app["broadcaster"] = Broadcaster(asyncio.get_running_loop())
    if app["args"].capture == "tap":
        app["capture"] = TapCapture(app["broadcaster"], app["args"].audiotee)
    else:
        app["capture"] = BlackHoleCapture(app["broadcaster"], app["args"].device)
    await app["capture"].start()
    app["mp3"] = Mp3Encoder(app["broadcaster"], app["args"].bitrate)
    await app["mp3"].start()
    if app["args"].signal:
        pairing = signaling_client.load_pairing(app["args"].reset_pairing)
        link = signaling_client.pairing_link(app["args"].signal, pairing)
        print(f"\n기기 연결: 아래 QR을 폰 카메라로 스캔하거나 링크를 여세요 (키가 들어 있으니 공유 금지)\n{link}")
        signaling_client.print_qr(link)
        client = signaling_client.SignalingClient(
            app["args"].signal, pairing, lambda sdp, label: create_answer(app, sdp, label))
        app["signal"] = asyncio.create_task(client.run())


async def on_shutdown(app):
    if "signal" in app:
        app["signal"].cancel()
    await app["mp3"].stop()
    await app["capture"].stop()
    await asyncio.gather(*(pc.close() for pc in app["pcs"]))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--capture", choices=["tap", "blackhole"], default="tap",
                   help="tap: Core Audio 탭(audiotee), blackhole: BlackHole 2ch 입력")
    p.add_argument("--audiotee", default=str(ROOT / "vendor/audiotee/.build/release/audiotee"))
    p.add_argument("--device", default="BlackHole 2ch", help="--capture blackhole일 때 입력 장치")
    p.add_argument("--bitrate", default="192k", help="MP3 스트림 비트레이트")
    p.add_argument("--signal", help="클라우드 시그널링 주소 (예: wss://crd-audio.<계정>.workers.dev) — 외부망 연결")
    p.add_argument("--reset-pairing", action="store_true", help="페어링 키 재발급 (기존 기기 연결 해제)")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    app = web.Application()
    app["args"] = args
    app["pcs"] = set()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)
    app.router.add_get("/stream.mp3", stream_mp3)
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
