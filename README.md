# CRD Audio Sidecar

크롬 원격데스크톱(CRD)은 macOS 호스트의 소리를 전송하지 않는다.
화면은 CRD 그대로 쓰고, **소리만 별도 경로로 보내는 사이드카 서버**.

```
맥미니 시스템 오디오 → BlackHole 2ch → server.py ─┬→ WebRTC (Opus)  → 데스크톱/아이패드 브라우저  (~0.15초)
                                                 │                    └→ 크롬 확장: CRD 화면 위 스피커 토글/볼륨
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
   | 데스크톱 크롬 | 크롬 확장 (아래) — CRD 화면 위 스피커 버튼 |
   | 아이폰 / 아이패드 | Safari에서 `http://<맥미니IP>:8080` → **연결** → CRD 앱으로 전환해도 계속 재생 (약 0.13초). 볼륨은 기기 버튼 |
   | 예비 (지연 약 2초) | VLC for Mobile → 네트워크 스트림 → `http://<맥미니IP>:8080/stream.mp3` (설정에서 네트워크 캐싱 최소) |

   맥미니 IP 확인: `ipconfig getifaddr en0`

### 크롬 확장 (데스크톱 CRD 화면 위 스피커 버튼)

1. 접속하는 컴퓨터의 크롬에서 `chrome://extensions` → 우측 상단 **개발자 모드** 켜기
2. **압축해제된 확장 프로그램을 로드** → 이 저장소의 `extension/` 폴더 선택
3. 툴바의 확장 아이콘 → 서버 주소(`192.168.1.23:8080` 형식) 입력 → **저장** → 접근 권한 **허용**
4. `remotedesktop.google.com`에서 CRD 접속 → 왼쪽 아래 🔇 버튼 클릭 → 🔊 + 초록 점이면 연결됨.
   마우스를 올리면 볼륨 슬라이더가 나온다(기본 50 = 약 -12dB, 100 = 원음, 150 = 2.25배). 켜짐 상태와 볼륨은 기억되어 다음 접속 때 자동 연결
   (새로고침 직후엔 브라우저 정책상 페이지를 한 번 클릭해야 소리가 난다).

> ⚠️ **맥미니 자신의 브라우저로 접속하지 말 것.** 출력이 BlackHole이면 받은 소리가 다시 캡처되어
> 하울링(피드백 루프)이 생긴다.

문제가 생기면 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)의 빠른 진단 표부터 볼 것.

## 파일

| 파일 | 역할 |
|---|---|
| `server.py` | 캡처 → WebRTC(`/offer`) + MP3 스트림(`/stream.mp3`) 서버 |
| `static/index.html` | WebRTC 테스트 페이지 (연결 버튼, 수신 통계) |
| `extension/` | 크롬 확장 (MV3): `content.js` 오버레이·WebRTC·볼륨, `background.js` 시그널링 중계, `popup.*` 서버 주소 |
| `capture_test.py` | BlackHole 캡처 검증용 WAV 녹음 |
| `CLAUDE.md` | 설계 배경, 단계별 계획, 알려진 함정 |
| `TROUBLESHOOTING.md` | 실제로 겪은 문제와 해결 기록 |

## 진행 상황

- [x] Phase 1 — BlackHole 캡처 + WebRTC (아이패드에서 확인)
- [x] Phase 2 — 모바일용 HTTP MP3 스트림 (휴대폰 VLC에서 지연 약 2초 확인)
- [x] Phase 3 — 크롬 확장 (CRD 화면 위 스피커 토글 / 볼륨, 윈도우 노트북 크롬에서 확인)
- [ ] Phase 4 — Core Audio 탭(`audiotee`)으로 캡처 교체, BlackHole 의존 제거
