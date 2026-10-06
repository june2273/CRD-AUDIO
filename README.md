# CRD Audio Sidecar

크롬 원격데스크톱(CRD)은 macOS 호스트의 소리를 전송하지 않는다.
화면은 CRD 그대로 쓰고, **소리만 별도 경로로 보내는 사이드카 서버**.

```
맥미니 시스템 오디오 → BlackHole 2ch → server.py ─┬→ WebRTC (Opus)  → 데스크톱/아이패드 브라우저  (~0.15초)
                                                 └→ HTTP MP3       → 휴대폰 VLC                (1~3초, 백그라운드 재생)
```

## 요구 사항

- macOS (Apple Silicon에서 확인), [BlackHole 2ch](https://github.com/ExistentialAudio/BlackHole), 48kHz로 설정
- Python 3.12 (3.14는 aiortc/PyAV 휠 문제로 피함)
- Homebrew `ffmpeg` (libmp3lame 포함) — HTTP 스트림용

## 설치

```bash
git clone git@github.com:june2273/CRD-AUDIO.git
cd CRD-AUDIO
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 사용법

1. **시스템 설정 > 사운드 > 출력 → BlackHole 2ch**
   맥미니에서도 함께 들으려면 오디오 MIDI 설정에서 Multi-Output Device(BlackHole + 실제 출력 장치)를 만들어 선택.
2. **캡처 확인 (처음 한 번)**
   ```bash
   .venv/bin/python capture_test.py --self-test   # 출력 설정과 무관하게 BlackHole 경로 검증
   .venv/bin/python capture_test.py -s 5          # 실제 시스템 소리 5초 녹음 → capture_test.wav
   ```
   `OK: 신호 들어옴`이 나오면 성공.
3. **서버 실행** (창을 닫으면 서버도 꺼짐)
   ```bash
   .venv/bin/python server.py            # 옵션: --port 8080 --device "BlackHole 2ch" --bitrate 192k
   ```
4. **다른 기기에서 듣기**

   | 기기 | 방법 |
   |---|---|
   | 데스크톱 / 아이패드 브라우저 | `http://<맥미니IP>:8080` → **연결** |
   | 휴대폰 | VLC for Mobile → 네트워크 스트림 → `http://<맥미니IP>:8080/stream.mp3` (설정에서 네트워크 캐싱 최소) |

   맥미니 IP 확인: `ipconfig getifaddr en0`

> ⚠️ **맥미니 자신의 브라우저로 접속하지 말 것.** 출력이 BlackHole이면 받은 소리가 다시 캡처되어
> 하울링(피드백 루프)이 생긴다.

문제가 생기면 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)의 빠른 진단 표부터 볼 것.

## 파일

| 파일 | 역할 |
|---|---|
| `server.py` | 캡처 → WebRTC(`/offer`) + MP3 스트림(`/stream.mp3`) 서버 |
| `static/index.html` | WebRTC 테스트 페이지 (연결 버튼, 수신 통계) |
| `capture_test.py` | BlackHole 캡처 검증용 WAV 녹음 |
| `CLAUDE.md` | 설계 배경, 단계별 계획, 알려진 함정 |
| `TROUBLESHOOTING.md` | 실제로 겪은 문제와 해결 기록 |

## 진행 상황

- [x] Phase 1 — BlackHole 캡처 + WebRTC (아이패드에서 확인)
- [x] Phase 2 — 모바일용 HTTP MP3 스트림 (VLC 실기 확인 중)
- [ ] Phase 3 — 크롬 확장 (CRD 화면 위 스피커 토글 / 볼륨)
- [ ] Phase 4 — Core Audio 탭(`audiotee`)으로 캡처 교체, BlackHole 의존 제거
