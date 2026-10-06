"""Phase 1 / 2단계: BlackHole 2ch 입력을 WAV로 녹음해 소리가 실제로 들어오는지 검증.

사용법:
  python capture_test.py               # 5초 녹음 (시스템 출력이 BlackHole이어야 함)
  python capture_test.py --self-test   # BlackHole 출력에 440Hz 톤을 직접 재생하며 녹음
"""
import argparse
import wave

import numpy as np
import sounddevice as sd

DEVICE = "BlackHole 2ch"
RATE = 48000
CHANNELS = 2


def main():
    p = argparse.ArgumentParser()
    p.add_argument("-s", "--seconds", type=float, default=5.0)
    p.add_argument("-o", "--out", default="capture_test.wav")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()

    info = sd.query_devices(DEVICE, "input")
    print(f"device: {info['name']}  default_samplerate={info['default_samplerate']:.0f}")
    if int(info["default_samplerate"]) != RATE:
        print(f"!! BlackHole 레이트가 {RATE}가 아님 — 오디오 MIDI 설정에서 48kHz로 맞출 것")

    frames = int(args.seconds * RATE)
    if args.self_test:
        t = np.arange(frames) / RATE
        tone = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        data = sd.playrec(np.column_stack([tone, tone]), samplerate=RATE,
                          channels=CHANNELS, dtype="int16", device=(DEVICE, DEVICE))
    else:
        print(f"{args.seconds:.0f}초 녹음 중...")
        data = sd.rec(frames, samplerate=RATE, channels=CHANNELS, dtype="int16", device=DEVICE)
    sd.wait()

    with wave.open(args.out, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data.tobytes())

    f = data.astype(np.float32) / 32768
    peak = np.abs(f).max()
    rms = np.sqrt((f ** 2).mean())
    print(f"saved {args.out}: peak={peak:.3f} rms={rms:.4f} "
          f"({20 * np.log10(max(rms, 1e-9)):.1f} dBFS)")
    print("OK: 신호 들어옴" if peak > 0.001 else "FAIL: 무음 — 시스템 출력이 BlackHole인지 확인")


if __name__ == "__main__":
    main()
