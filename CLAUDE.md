# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# 크롬 원격데스크톱 오디오 사이드카 — 작업 인계서

> **현재 상태 (2026-10-06):** Phase 1 완료 — 시스템 출력(BlackHole) → 아이패드 크롬에서 소리 확인됨
> (손실 0, 지터버퍼 약 128ms).
> Phase 2 구현됨 — `/stream.mp3` (48kHz 스테레오 192kbps) curl 수신 검증. 휴대폰 VLC 실기 확인 필요.

## 명령어

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt   # Python 3.14는 aiortc/av 휠 때문에 피함
.venv/bin/python capture_test.py --self-test   # BlackHole 출력에 440Hz 톤을 재생하며 녹음 → 시스템 출력 설정 없이 캡처 경로 검증
.venv/bin/python capture_test.py -s 5          # 실제 시스템 오디오 녹음 (시스템 출력이 BlackHole/Multi-Output이어야 함)
.venv/bin/python server.py --port 8080         # 데스크톱: http://<맥미니IP>:8080 → '연결'
                                               # 모바일 VLC: http://<맥미니IP>:8080/stream.mp3
curl -s -m 6 -o /tmp/t.mp3 http://127.0.0.1:8080/stream.mp3 && ffprobe /tmp/t.mp3   # HTTP 스트림 단독 확인
```

## 코드 구조
- `server.py` — sounddevice 콜백(별도 스레드)이 20ms(960샘플) int16 프레임을 `Broadcaster`에
  넣고, 피어마다 `CaptureTrack`이 자기 큐(최대 5프레임, 넘치면 오래된 것 버림)에서 꺼내
  `av.AudioFrame`으로 aiortc에 넘긴다. 캡처 스트림은 서버 시작 시 1개만 열고 모든 피어가 공유.
  `Mp3Encoder`도 같은 Broadcaster를 구독해 ffmpeg 1개(stdin PCM → stdout MP3)로 인코딩하고
  `/stream.mp3` 청취자들에게 나눠준다. ffmpeg가 BlackHole을 따로 열지 않는 이유: 캡처 지점을 하나로 유지.
  **Phase 4에서는 `start_capture()`만 audiotee 파이프로 교체하면 WebRTC/HTTP 둘 다 바뀐다.**
  외부 의존: Homebrew `ffmpeg` (libmp3lame 포함).
- `static/index.html` — 테스트 페이지. aiortc가 opus fmtp에 `stereo=1`을 넣지 않아 Chrome이
  모노로 다운믹스하므로 offer/answer SDP를 둘 다 수정한다. aiortc는 trickle ICE를 안 하므로
  ICE 수집 완료 후 offer를 한 번에 보낸다. iOS/iPadOS(WebKit)는 클릭 직후에만 재생을 허용해서
  `await` 이후에 `srcObject`를 붙이면 stats의 level은 올라오는데 무음이 된다 → 클릭 시점에 빈
  `MediaStream`으로 `play()`를 먼저 걸고 `ontrack`에서 트랙을 추가한다.
  Phase 3 확장 content script로 옮길 때 이 세 가지 유지할 것.

## 1. 한 줄 요약

크롬 원격데스크톱(CRD)은 macOS 호스트에서 오디오를 전송하지 않는다.
화면은 CRD 그대로 쓰고, **오디오만 별도 경로로 흘려보내는 사이드카**를 만든다.

---

## 2. 배경 / 왜 이걸 만드는가

### 문제
- 맥미니(M4, 16GB)를 헤드리스 원격 워크스테이션으로 운용 중
- CRD로 접속하면 화면/입력은 되는데 **소리가 전혀 안 들림**
- Jump Desktop은 오디오를 지원하지만 렉이 심해 환불함

### 원인 (기술적 배경)
CRD는 Windows / Linux 호스트에서는 오디오를 전송한다.

| OS | 시스템 오디오 캡처 통로 | CRD 오디오 |
|---|---|---|
| Windows | WASAPI 루프백 (OS 내장) | 지원 |
| Linux | PulseAudio monitor 소스 | 지원 |
| macOS | **과거엔 공식 API 없음** | 미지원 |

macOS는 오랫동안 시스템 오디오 탭 API가 없어서, 하려면 커널 확장(kext) 수준의
가상 오디오 드라이버를 직접 설치해야 했다 (BlackHole / Soundflower / Loopback이
존재하는 이유). 구글 입장에선 CRD 설치 시 커널 드라이버 설치 + 보안 승인 + 재부팅을
요구해야 해서 구현을 포기한 것으로 보인다.

**단, 지금은 상황이 바뀌었다:**
- macOS 13 (Ventura) — ScreenCaptureKit에 시스템 오디오 캡처 추가
- macOS 14.2 / 14.4 — Core Audio에 **프로세스 탭 API** 정식 추가
  (`AudioHardwareCreateProcessTap`, `CATapDescription`)

즉 이제는 커널 드라이버 없이 권한 승인만으로 시스템 오디오를 가져올 수 있다.
구글이 안 하는 것일 뿐, 우리가 직접 만드는 건 가능하다.

### 왜 확장프로그램 단독으로는 불가능한가
크롬 확장은 **클라이언트(보는 쪽) 브라우저**에서 동작한다. 소리는 **호스트(맥미니)**에서
난다. 확장은 맥미니의 오디오에 접근할 방법이 없고, CRD의 WebRTC 세션은 구글이 협상해서
만든 것이라 오디오 트랙을 끼워넣을 수도 없다.
→ **호스트 측 캡처 서버가 반드시 한 짝으로 필요하다.**

---

## 3. 아키텍처

```
┌─ 맥미니 (호스트) ─────────────────────────┐
│                                           │
│  시스템 오디오 출력                        │
│  (유튜브, Ableton, 알림음)                 │
│         ↓                                 │
│  캡처 레이어                               │
│   - Phase 1: BlackHole 2ch (가상 루프백)   │
│   - Phase 2: Core Audio 프로세스 탭        │
│         ↓                                 │
│  인코딩 + 서버 (Opus)                      │
│         ↓                                 │
└─────────┼─────────────────────────────────┘
          ↓
   WebRTC 스트림 (로컬망 또는 Tailscale)
          ↓
┌─ 클라이언트 ──────────────────────────────┐
│  데스크톱: 크롬 확장 → Web Audio 재생      │
│  모바일:   VLC → HTTP 스트림 엔드포인트    │
└───────────────────────────────────────────┘
```

**핵심: 영상과 오디오는 완전히 분리된 경로로 흐른다.**
CRD는 손대지 않고 그대로 둔다. 오디오는 그 옆에 나란히 붙는 별도 채널.

---

## 4. 개발 단계 (이 순서를 지킬 것)

### Phase 1 — BlackHole 기반 캡처 서버 (Python) ★ 여기서 시작
가장 익숙한 언어로 **"소리가 네트워크 너머로 들린다"**를 먼저 증명한다.

- 입력: BlackHole 2ch를 오디오 입력 장치로 열기
- 인코딩: Opus
- 전송: `aiortc` (Python WebRTC) + 간단한 시그널링 HTTP 서버
- 검증: 브라우저에서 `http://<맥미니IP>:<포트>` 직접 열어서 소리 확인

예상 분량: 250줄 안쪽.

**사전 설정 (맥미니):**
- 시스템 설정 > 사운드 > 출력 → BlackHole 2ch
- 로컬에서도 동시에 듣고 싶으면 오디오 MIDI 설정에서
  Multi-Output Device (BlackHole + Topping E2x2) 생성

### Phase 2 — 모바일용 HTTP 스트림 엔드포인트 추가
iOS는 브라우저가 백그라운드로 가면 WebRTC 오디오가 끊긴다.
→ ffmpeg로 같은 입력을 HTTP 스트림(MP3/AAC)으로도 내보내고 VLC로 수신.
지연 1~3초로 늘지만 백그라운드 재생이 안정적이다.

출력 경로를 두 갈래로 설계:
| 엔드포인트 | 대상 | 특성 |
|---|---|---|
| WebRTC | 데스크톱 크롬 + 확장 | 저지연 (100~200ms) |
| HTTP 스트림 | 모바일 VLC | 안정성 우선 (1~3초) |

### Phase 3 — 크롬 확장프로그램
- Manifest V3
- `remotedesktop.google.com/*`에 content script 주입
- CRD 화면 위에 스피커 토글 오버레이 + 볼륨 슬라이더(GainNode)

### Phase 4 — Core Audio 탭으로 캡처 교체
Phase 1이 완전히 돌아간 뒤에만 착수. **캡처 부분만** 갈아끼운다.

얻는 것:
- 출력 장치를 BlackHole로 바꾸지 않아도 됨 (평소대로 Topping으로 소리 내면서 동시 캡처)
- 가상 드라이버 의존 제거

**Swift를 직접 짤 필요는 없다.** 아래 "8. 캡처 백엔드 선택" 참고.

---

## 5. 반드시 알아야 할 함정들

### 공통
- **샘플레이트는 48kHz로 통일할 것.**
  Opus가 내부적으로 48kHz라 BlackHole이 44.1kHz면 리샘플링 때문에
  지지직거리거나 끊긴다. BlackHole / Topping / 인코더 전부 맞출 것.
- BlackHole 2ch가 불안정하면 16ch 버전으로 교체 시도 (2ch 드라이버 충돌 사례 있음)
- **호스트(맥미니) 자신의 브라우저로 사이드카 페이지에 접속한 채 두지 말 것.** 시스템 출력이
  BlackHole이면 수신 소리가 다시 캡처되어 피드백 루프(겹쳐 들리는 하울링)가 생긴다.
  호스트에서 테스트할 땐 출력을 BlackHole이 아닌 장치로 둔 상태에서만.

### Core Audio 탭 (Phase 4)
- `Info.plist`에 **`NSAudioCaptureUsageDescription`** 키 + 설명 문구 필수.
  없으면 **아무 소리도 안 들어오면서 에러도 안 난다.**
- **권한 요청용 공식 API가 없다.** 마이크처럼 `requestAuthorization()` 호출 불가.
  실제로 녹음을 시작하는 순간 시스템이 프롬프트를 띄우는 구조.
- **장시간 무음 버퍼 버그:** 수십 분 돌리면 콜백은 정상 호출되고 타임스탬프도
  멀쩡한데 모든 샘플이 `0.0f`로만 들어오는 현상이 보고됨. 저절로 복구되기도 하고
  세션 재시작이 필요하기도 함.
  → **무음 감지 워치독을 넣어 탭을 재생성하도록 설계할 것.**
- 앱 번들 + 코드 서명이 되어 있어야 권한 프롬프트가 뜬다.
  `swiftc` 단일 파일 빌드로는 안 됨.
  (→ 직접 구현할 경우에만 해당. `audiotee` 바이너리를 쓰면 이 문제를 우회한다)
- **권한은 바이너리가 아니라 "실행한 앱"에 묶인다.** 터미널에서 돌리면 iTerm2에
  권한이 부여된다. 터미널을 바꾸면 권한을 다시 받아야 한다.
- 샘플레이트 재협상(출력 장치 변경, Audio MIDI 설정에서 레이트 변경) 이후
  탭이 조용히 무음으로 빠지는 사례가 보고됨. 출력 장치 전환/절전 복귀 시
  캡처를 재시작하는 로직을 넣을 것.

### 모바일
- 휴대폰 CRD는 **네이티브 앱**이라 확장프로그램이 들어갈 자리가 없다.
  → 화면은 CRD 앱, 소리는 별도 앱(VLC 등) 2개 동시 운용 구조.
- 아이패드는 스플릿뷰로 CRD + 사파리를 나란히 띄우면 백그라운드 문제 회피 가능.

### 데스크톱 크롬
- 데스크톱은 CRD가 **웹페이지**라 화면과 오디오가 같은 탭 안에 있다. 백그라운드 문제 없음.
- **Manifest V3 service worker는 유휴 30초쯤에 강제 종료된다.**
  → `RTCPeerConnection`, `<audio>` 엘리먼트, 볼륨 제어는 **전부 content script에 둘 것.**
  service worker에 두면 연결이 툭툭 끊긴다.

---

## 6. 환경

| 항목 | 값 |
|---|---|
| 호스트 | Mac Mini M4, RAM 16GB, 헤드리스 |
| 오디오 인터페이스 | Topping E2x2 |
| 가상 오디오 | BlackHole 2ch (설치됨) |
| 터미널 | iTerm2 |
| 외장 SSD | `/Volumes/june2_07ssd` |
| 보유 IDE | PyCharm, IntelliJ, VS Code |
| OS | **macOS 27.0.1** |
| Swift 직접 개발 시 | Xcode 설치 필요 (App Store, 10GB+) — 단, 8번 참고 |

### 사용자 배경
- 사용 가능 언어: C, Python, Java
- Swift 미경험 → **전 Phase를 Python/JS로 완주 가능** (8번 참고)
- C 경험이 있어 Core Audio의 포인터 패턴(`&tapID`)에는 적응이 빠를 것

### 오디오 인터페이스 루프백은 쓰지 않는다
Topping E2x2의 하드웨어 루프백은 이 파이프라인에 불필요하다.
- 불필요한 D/A → A/D 변환 가능성
- 인터페이스 버퍼만큼 지연 추가
- 게인 노브가 신호 레벨에 영향 → 원본과 다른 소리
- 헤드리스 운용인데 하드웨어 상태에 의존하게 됨

BlackHole은 순수 소프트웨어라 디지털 도메인 안에서만 이동 → 더 짧고 깨끗하고 예측 가능.
(단, 마이크 + 시스템 사운드를 믹스해야 할 경우엔 Ableton을 믹서로 쓰는 게 유연함)

---

## 7. OS 버전별 가용 기능 (호스트는 macOS 27.0.1)

| 버전 | 추가된 것 | 이 프로젝트에서의 의미 |
|---|---|---|
| 14.2 | `AudioHardwareCreateProcessTap`, `CATapDescription` | 탭 캡처의 기본. 최소 요구 버전 |
| 14.4 | 전체 시스템 오디오 캡처로 확대 | 특정 앱이 아닌 시스템 전체 캡처 가능 |
| **26** | `CATapDescription.bundleIDs` | **프로세스를 PID 대신 번들 ID로 지정** |
| **26** | `CATapDescription.isProcessRestoreEnabled` | **앱이 죽었다 살아나면 탭에 자동 복원** |
| 27 | MusicUnderstanding, NowPlaying | 분석/메타데이터용 — **이 프로젝트와 무관** |

### macOS 26 속성을 쓰는 이유
기존 `processes` 속성은 PID 기반 `AudioObjectID`를 받는다. 앱을 껐다 켜면 PID가
바뀌므로 탭이 그 앱을 놓친다. `bundleIDs`는 고정 문자열이라 이 문제가 없고,
`isProcessRestoreEnabled = true`로 두면 프로세스 종료 시 번들 ID로 저장해뒀다가
재시작될 때 탭에 자동으로 다시 붙여준다.

원격으로 장시간 켜두는 용도에서 의미 있는 개선이다.
(시스템 전체를 캡처한다면 굳이 필요 없고, "크롬 소리만" 같은 선별 캡처를 할 때 유용)

**주의:** 이 두 속성은 Apple 문서에 한 줄 설명뿐이고 실사용 예제가 거의 없다.
공식 샘플 코드에도 아직 반영되지 않았을 가능성이 높다.
→ **먼저 기존 `processes` 방식 또는 전체 캡처로 동작시킨 뒤 교체할 것.**

### macOS 27의 오디오 업데이트는 해당 없음
macOS 27은 MusicUnderstanding(온디바이스 오디오 분석, BS.1770 라우드니스 측정)과
NowPlaying(잠금화면/제어센터 연동)을 추가했다. 투자 방향이 **캡처·전송이 아니라
분석·메타데이터** 쪽이므로 이 파이프라인에는 쓸 일이 없다.

---

## 8. 캡처 백엔드 선택 (Phase 4)

**Swift를 직접 짜지 않아도 된다.** 세 가지 선택지:

### 옵션 A — `audiotee` 바이너리 + 파이프 ★ 추천
https://github.com/makeusabrew/audiotee

Core Audio 탭으로 시스템 오디오를 캡처해 **PCM 청크를 stdout으로 뿌리는 CLI 도구**.
로그/메타데이터는 stderr로 분리되어 나온다.

```
audiotee > output.pcm        # 가장 단순한 사용례
```

출력 형식:
- Raw PCM, 기본 모노 (스테레오 모드 있음)
- 샘플레이트: 기본은 출력 장치를 따라감 (설정 가능 → **48000으로 고정할 것**)
- 비트심도: 기본 32-bit float (샘플레이트 변환 시 16-bit)
- 리틀엔디안, 기본 200ms 청크 (설정 가능)

파이썬에서 `subprocess`로 띄워 stdout을 읽으면 끝. **Swift도 Xcode도 불필요.**

⚠️ 저장소가 스스로 **"API가 불안정하며 예고 없이 변경될 수 있다"**고 경고 중.
→ **커밋 해시나 릴리스 태그로 버전을 고정해서 쓸 것.**

### 옵션 B — Python 래퍼 라이브러리
Core Audio Tap API의 파이썬 래퍼가 몇 개 공개되어 있다 (macOS 14.2+ 지원).
프로세스 경계 없이 인프로세스로 다룰 수 있지만, 성숙도와 유지보수 상태를 먼저 확인할 것.

### 옵션 C — Swift 직접 구현
`insidegui/AudioCap`에서 시작. 가장 제어력이 높지만 Xcode + Swift + Core Audio +
WebRTC를 한꺼번에 상대하게 된다. A나 B가 막힐 때만 선택.

---

## 9. 참고 자료

- Apple — Capturing system audio with Core Audio taps
  https://developer.apple.com/documentation/coreaudio/capturing-system-audio-with-core-audio-taps
- Apple — `CATapDescription.bundleIDs` (macOS 26+)
  https://developer.apple.com/documentation/coreaudio/catapdescription/bundleids
- Apple — `CATapDescription.isProcessRestoreEnabled` (macOS 26+)
  https://developer.apple.com/documentation/coreaudio/catapdescription/isprocessrestoreenabled
- `makeusabrew/audiotee` — 탭 캡처 CLI (옵션 A)
  https://github.com/makeusabrew/audiotee
- `insidegui/AudioCap` — Swift 샘플 (옵션 C)
  https://github.com/insidegui/AudioCap
- Apple Developer Forums — 무음 버퍼 이슈
  https://developer.apple.com/forums/thread/825780
- `aiortc` — Python WebRTC 구현체

---

## 10. 첫 작업 지시

Phase 1을 시작한다. 구체적으로:

1. Python 가상환경 구성, `aiortc` + `sounddevice`(또는 `pyaudio`) 설치
2. BlackHole 2ch를 입력 장치로 열어 48kHz 스테레오 PCM을 읽는 최소 코드 작성
   → 먼저 WAV 파일로 몇 초 녹음해 **소리가 실제로 들어오는지 검증**
3. 검증되면 Opus 인코딩 + `aiortc` 피어 연결 추가
4. 시그널링용 최소 HTTP 서버 + 테스트용 HTML 페이지 (`<audio>` + 연결 버튼)
5. 아이패드/다른 기기 브라우저에서 접속해 소리 확인

**2번에서 막히면 그 위로 진행하지 말 것.** 캡처가 안 되는 상태에서 WebRTC를 얹으면
어느 쪽 문제인지 분리가 안 된다.

### 왜 Phase 4(탭)부터 바로 안 가는가
호스트가 macOS 27이고 `audiotee`를 쓰면 Phase 1을 건너뛰고 싶어질 수 있다. 하지만:
- 탭은 TCC 권한 프롬프트가 끼어 있어 **"소리가 안 들린다"의 원인이 캡처인지 전송인지
  판별하기 어렵다** (권한 미승인 시 에러 없이 무음으로만 나온다)
- BlackHole 경로는 권한 이슈가 전혀 없어 전송 레이어만 순수하게 검증 가능하다

→ Phase 1로 전송 파이프라인을 확정한 뒤, 캡처 소스만 `audiotee`로 교체하면
문제가 생겨도 어느 쪽인지 즉시 안다.
