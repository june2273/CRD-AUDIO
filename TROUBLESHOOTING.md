# 트러블슈팅 기록

실제로 겪은 문제와 해결 과정을 남긴다. 새 문제가 생기면 아래 형식으로 맨 위 표와 본문에 추가할 것.

## 빠른 진단

| 증상 | 먼저 볼 것 | 해당 항목 |
|---|---|---|
| 겹쳐 들림, 하울링 | 맥미니에서 사이드카 페이지가 열려 있는지 | [#1](#1-소리가-여러-번-겹쳐-들림-하울링) |
| `Failed to fetch` / `ERR_CONNECTION_FAILED` | 서버가 실행 중인지 (`lsof -nP -iTCP:8080 -sTCP:LISTEN`) | [#2](#2-failed-to-fetch--err_connection_failed) |
| `Failed to parse URL from /offer` | 주소창이 `http://`인지 파일 경로인지 | [#3](#3-failed-to-parse-url-from-offer) |
| `connected`인데 무음 | 페이지 하단 `level`, `audio` 값 | [#4](#4-아이패드에서-connected인데-소리가-안-남) |
| 모바일 지연 10초 | 브라우저가 아닌 VLC로 재생 중인지 | [#5](#5-모바일-http-스트림-지연-약-10초) |
| Ctrl+C 후 서버가 1분간 안 꺼짐 | — (수정됨) | [#6](#6-서버-종료가-약-60초-걸림) |
| `git push` 실패 | SSH 키 암호, `gh auth status` | [#7](#7-github-push-실패) |
| 탭 캡처(`--tap`)가 무음 | 실행한 터미널 앱의 시스템 오디오 녹음 권한 | [#8](#8-탭-캡처가-에러-없이-무음-진행-중) |

### `level` 값으로 원인 가르기 (테스트 페이지 하단)

| `level` | `audio` | 의미 |
|---|---|---|
| 0 | — | 캡처 쪽 문제. 맥미니 시스템 출력이 BlackHole인지, 실제로 소리가 나고 있는지 확인 |
| > 0 | `paused` | 브라우저가 재생을 막음 → #4 |
| > 0 | `playing` | 기기 볼륨 / 무음 모드 확인 |

---

## 1. 소리가 여러 번 겹쳐 들림 (하울링)

- **증상:** 시스템 출력을 BlackHole로 바꾸자 소리가 0.1~0.2초 간격으로 여러 번 겹쳐 들림.
- **원인:** 맥미니 자신의 브라우저(Claude 브라우저 패널)가 사이드카에 연결된 채 재생 중이었다.
  `유튜브 → BlackHole → 캡처 → 맥미니 브라우저 재생 → BlackHole → 캡처 → ...` 피드백 루프.
- **해결:** 맥미니 쪽 연결 끊기.
- **예방:** 사이드카 소리는 항상 **다른 기기**에서 듣는다. 호스트에서 테스트할 땐 출력을 BlackHole이 아닌 장치로.

## 2. `Failed to fetch` / `ERR_CONNECTION_FAILED`

- **증상:** 맥미니 페이지에서 `TypeError: Failed to fetch`, 아이패드에서 `ERR_CONNECTION_FAILED`.
- **원인:** 서버가 꺼져 있었다 (#1 대응 중 종료한 뒤 재시작하지 않음).
- **진단:** `lsof -nP -iTCP:8080 -sTCP:LISTEN` 결과가 비어 있으면 서버 없음.
  IP는 `ipconfig getifaddr en0`, 방화벽은 `/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate`.
- **해결:** iTerm2에서 `.venv/bin/python server.py` 실행, 창은 열어둔 채 유지.

## 3. `Failed to parse URL from /offer`

- **증상:** 연결 버튼을 누르면 `Failed to execute 'fetch' on 'Window': Failed to parse URL from /offer`.
- **원인:** 서버를 거치지 않고 `static/index.html` **파일을 직접 연 상태** (주소창이 `/Volumes/...`).
  상대 경로 `/offer`를 해석할 기준 주소가 없다.
- **해결:** 항상 `http://<맥미니IP>:8080`으로 접속.

## 4. 아이패드에서 `connected`인데 소리가 안 남

- **증상:** 상태 `connected`, 패킷 손실 0, `level: 0.241`인데 무음.
- **원인:** `level > 0`이므로 데이터는 도착·디코딩됐다 → 재생 단계 문제.
  iPadOS의 크롬은 WebKit 엔진이고, WebKit은 **클릭 직후에만** 재생을 허용한다.
  기존 코드는 `await fetch(...)` 이후에 `srcObject`를 붙여서 그사이 허용이 만료됐다.
- **해결 (`static/index.html`):** 클릭 시점에 빈 `MediaStream`으로 `audio.play()`를 먼저 호출하고,
  `ontrack`에서 그 스트림에 트랙을 추가. `<audio>`에 `playsinline` 추가.
  진단용으로 통계에 `audio: playing/paused` 표시.
- **주의:** Phase 3 확장으로 옮길 때도 이 방식을 유지할 것.

## 5. 모바일 HTTP 스트림 지연 약 10초

- **증상:** 휴대폰에서 `/stream.mp3` 재생 시 약 10초 지연.
- **진단:** 서버 경로 지연을 직접 측정 — BlackHole에 톤을 재생한 시각과 스트림에서 톤이 검출된
  시각의 차이 **약 0.4초**. 서버는 원인이 아니다.
- **원인:** VLC가 아닌 휴대폰 브라우저로 직접 열어서, 브라우저의 재생 버퍼가 크게 잡힘.
- **해결:** VLC for Mobile 설치 → 설정에서 네트워크 캐싱을 가장 낮게 →
  `http://<맥미니IP>:8080/stream.mp3`. **지연 약 2초로 확인** (목표 1~3초 충족).

## 6. 서버 종료가 약 60초 걸림

- **증상:** `/stream.mp3` 청취자가 연결된 상태에서 서버를 끄면 바로 안 꺼짐.
- **원인:** 종료 시 MP3 인코더가 먼저 멈추면 청취자 핸들러가 `q.get()`에서 영원히 대기 →
  클라이언트 끊김도 감지 못 하고 aiohttp가 shutdown 타임아웃(60초)까지 기다림.
- **해결 (`server.py`):** 종료·ffmpeg 종료 시 청취자 큐에 `None`을 넣어 핸들러를 끝낸다.
  청취자 연결 상태에서 종료 0초 확인.

## 7. GitHub push 실패

- **증상 1:** HTTPS push 시 `could not read Username for 'https://github.com'`.
  → 이 맥에 HTTPS용 GitHub 자격 증명이 없음.
- **증상 2:** SSH 키 등록 후에도 `Permission denied (publickey)` (Claude Code 셸에서).
  → `~/.ssh/id_ed25519`에 **암호(passphrase)**가 걸려 있어 비대화형 셸에서 키를 풀 수 없음.
  확인: `ssh-keygen -y -P "" -f ~/.ssh/id_ed25519` 실패 시 암호 있음.
- **해결:** remote는 SSH(`git@github.com:june2273/CRD-AUDIO.git`) 유지.
  - iTerm2에서 직접 push: SSH 키 암호 입력.
  - Claude Code에서 push: 로그인된 `gh` 토큰을 1회성으로 사용 (전역 설정 변경 없음)
    ```bash
    git -c credential.helper= -c "credential.helper=!/opt/homebrew/bin/gh auth git-credential" \
      push https://github.com/june2273/CRD-AUDIO.git main
    ```

## 8. 탭 캡처가 에러 없이 무음 (진행 중)

- **증상:** `audiotee`가 탭 생성·장치 준비까지 정상 로그를 내고 PCM도 나오는데 전부 0.
  440Hz 톤을 재생하며 녹음해도 peak 0.
- **원인:** TCC(시스템 오디오 녹음) 권한 없음. 권한은 **실행한 앱**(iTerm2, Claude Code 셸 등)에 묶이고,
  미허용이면 에러 없이 0만 들어온다. iTerm2는 프롬프트를 안 띄우는 경우가 있다 (audiotee README).
- **해결:** 시스템 설정 > 개인정보 보호 및 보안 > 화면 및 시스템 오디오 녹음 > 시스템 오디오 녹음 전용에
  iTerm2 추가·허용 → iTerm2 재시작 → `capture_test.py --tap`.
- **상태:** Claude Code 셸에서 재현(무음). iTerm2 권한 부여 후 결과 확인 필요.

---

## 예방적으로 처리한 것 (문제가 드러나기 전에 대응)

- **Chrome 모노 다운믹스:** aiortc는 Opus fmtp에 `stereo=1`을 넣지 않아 Chrome이 모노로 재생한다.
  → 테스트 페이지에서 offer/answer SDP 모두에 `stereo=1;sprop-stereo=1` 추가. `getStats()`로 `ch=2` 확인.
- **aiortc는 trickle ICE 미지원:** ICE 수집 완료 후 offer를 한 번에 전송.
- **확장의 mixed content / 사설망 접근:** CRD는 https 페이지라 content script에서 `http://192.168.x.x`로
  fetch하면 차단된다. → 시그널링 POST만 service worker(`background.js`)가 호스트 권한으로 대신 보낸다.
  WebRTC 미디어 자체는 mixed content 대상이 아니라 content script에서 그대로 연결된다.
- **Chrome의 원격 WebRTC 트랙 → Web Audio 무음:** `createMediaStreamSource`만 쓰면 무음이 되는 Chrome 동작이 있어
  음소거된 `<audio>`에도 같은 스트림을 붙인다. 로컬 테스트에서 GainNode 앞 분석기로 440Hz 수신 확인.
- **Python 3.14 회피:** aiortc/PyAV 휠 호환성 때문에 3.12로 가상환경 구성.
- **서버 로그의 `.local could not be resolved` 경고:** Chrome의 mDNS 후보 은닉 때문.
  peer reflexive 후보로 연결되므로 무시해도 된다. 다른 기기에서 연결이 안 될 때만 의심.
