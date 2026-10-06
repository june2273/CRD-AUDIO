// CRD 화면 위 스피커 오버레이. RTCPeerConnection·오디오·볼륨은 전부 여기 둔다
// (MV3 service worker는 유휴 30초쯤 종료되므로). /offer 요청만 background.js가 대신 보낸다.
(() => {
  let server = '', on = false, volume = 100;
  let pc = null, ctx = null, gain = null, src = null, audio = null, retryTimer = null;

  const host = document.createElement('div');
  const root = host.attachShadow({ mode: 'open' });
  root.innerHTML = `<style>
    :host { all: initial; position: fixed; left: 12px; bottom: 12px; z-index: 2147483647; }
    .bar { display: flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 16px;
           background: rgba(32, 33, 36, .8); color: #fff; font: 12px -apple-system, sans-serif;
           opacity: .5; transition: opacity .2s; }
    .bar:hover { opacity: 1; }
    .bar:not(:hover) input, .bar:not(:hover) .st { display: none; }
    button { all: unset; cursor: pointer; font-size: 16px; }
    input { width: 100px; }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: #888; }
    .ok { background: #3c3; } .wait { background: #fc3; } .err { background: #e44; }
  </style>
  <div class="bar"><span class="dot"></span><button title="사이드카 오디오 켜기/끄기">🔇</button>
  <input type="range" min="0" max="200" step="5"><span class="st"></span></div>`;
  const [dot, btn, vol, st] = ['.dot', 'button', 'input', '.st'].map(s => root.querySelector(s));
  // 오버레이 조작이 CRD로 넘어가 원격 클릭/키 입력이 되지 않도록 막는다
  for (const t of ['pointerdown', 'pointerup', 'mousedown', 'mouseup', 'click', 'dblclick',
                   'wheel', 'keydown', 'keyup', 'contextmenu'])
    host.addEventListener(t, e => e.stopPropagation());
  document.body.appendChild(host);
  // 전체화면이면 전체화면 엘리먼트 안에 있어야 보인다
  document.addEventListener('fullscreenchange', () => {
    const fs = document.fullscreenElement;
    (fs && !(fs instanceof HTMLMediaElement) && !(fs instanceof HTMLCanvasElement) ? fs : document.body)
      .appendChild(host);
  });

  function paint(state, text) { dot.className = 'dot ' + state; st.textContent = text; }

  function render(err) {
    btn.textContent = on ? '🔊' : '🔇';
    if (!on) return paint('', '꺼짐');
    if (err) return paint('err', err);
    if (pc?.connectionState !== 'connected') return paint('wait', '연결 중');
    ctx.state === 'running' ? paint('ok', '연결됨') : paint('wait', '페이지를 클릭하면 재생');
  }

  // aiortc는 opus fmtp에 stereo=1을 넣지 않아서 Chrome이 모노로 다운믹스한다 → 직접 추가
  function stereo(sdp) {
    const m = sdp.match(/a=rtpmap:(\d+) opus\/48000\/2/i);
    if (!m) return sdp;
    const pt = m[1];
    const fmtp = new RegExp(`a=fmtp:${pt} (.*)`);
    return fmtp.test(sdp)
      ? sdp.replace(fmtp, (_, p) => `a=fmtp:${pt} ${p};stereo=1;sprop-stereo=1`)
      : sdp.replace(m[0], `${m[0]}\r\na=fmtp:${pt} stereo=1;sprop-stereo=1`);
  }

  // 클릭 직후(사용자 제스처 안)에 불러야 AudioContext가 재생 상태로 시작한다
  function setupAudio() {
    if (!ctx) {
      ctx = new AudioContext();
      gain = ctx.createGain();
      gain.gain.value = volume / 100;
      gain.connect(ctx.destination);
      ctx.onstatechange = () => render();
    }
    if (ctx.state === 'suspended') ctx.resume();
  }
  // 자동 재연결(새로고침 후 켜짐 상태 복원)은 제스처가 없어 suspended로 시작 → 첫 클릭/키 입력에 재개
  const resume = () => ctx?.state === 'suspended' && ctx.resume();
  window.addEventListener('pointerdown', resume, true);
  window.addEventListener('keydown', resume, true);

  async function connect() {
    clearTimeout(retryTimer);
    if (!server) return render('확장 아이콘 → 서버 주소 설정');
    const p = pc = new RTCPeerConnection();
    p.addTransceiver('audio', { direction: 'recvonly' });
    p.ontrack = e => {
      const stream = new MediaStream([e.track]);
      // Chrome은 원격 WebRTC 트랙을 미디어 엘리먼트에 붙이지 않으면 Web Audio로 무음이 나온다
      // → 음소거된 <audio>에도 연결해 두고 실제 소리는 GainNode 경로로 낸다
      audio = new Audio();
      audio.muted = true;
      audio.srcObject = stream;
      audio.play().catch(() => {});
      src = ctx.createMediaStreamSource(stream);
      src.connect(gain);
    };
    p.onconnectionstatechange = () => {
      if (p !== pc) return;
      if (p.connectionState === 'failed') retry('연결 끊김 — 재시도');
      else render();
    };
    render();
    try {
      const offer = await p.createOffer();
      offer.sdp = stereo(offer.sdp);
      await p.setLocalDescription(offer);
      // aiortc는 ICE 후보를 trickle하지 않으므로 수집 완료 후 한 번에 보낸다
      await new Promise(r => {
        if (p.iceGatheringState === 'complete') return r();
        p.addEventListener('icegatheringstatechange', () => p.iceGatheringState === 'complete' && r());
      });
      const res = await chrome.runtime.sendMessage({
        type: 'offer', server, offer: { sdp: p.localDescription.sdp, type: p.localDescription.type },
      });
      if (res.error) throw new Error(res.error);
      if (p !== pc) return;
      res.answer.sdp = stereo(res.answer.sdp);
      await p.setRemoteDescription(res.answer);
    } catch (e) {
      if (p === pc) retry(e.message);
    }
  }

  function teardown() {
    clearTimeout(retryTimer);
    pc?.close(); pc = null;
    src?.disconnect(); src = null;
    if (audio) { audio.srcObject = null; audio = null; }
  }

  function retry(err) {
    teardown();
    render(err);
    retryTimer = setTimeout(connect, 3000);
  }

  function setOn(v) {
    on = v;
    chrome.storage.local.set({ on });
    teardown();
    on ? connect() : render();
  }

  btn.onclick = () => {
    if (!on) setupAudio();
    setOn(!on);
    btn.blur();
  };
  vol.oninput = () => {
    volume = +vol.value;
    vol.title = volume + '%';
    gain?.gain.setTargetAtTime(volume / 100, ctx.currentTime, 0.02);
  };
  vol.onchange = () => { chrome.storage.local.set({ volume }); vol.blur(); };

  chrome.storage.local.get({ server: '', on: false, volume: 100 }).then(s => {
    server = s.server;
    volume = s.volume;
    vol.value = volume;
    vol.title = volume + '%';
    if (s.on) { setupAudio(); setOn(true); } else render();
  });
  chrome.storage.onChanged.addListener(c => {
    if (!c.server) return;
    server = c.server.newValue ?? '';
    if (on) setOn(true);
  });
})();
