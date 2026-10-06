const input = document.getElementById('server');
const msg = document.getElementById('msg');

chrome.storage.local.get({ server: '' }).then(s => { input.value = s.server; });

document.getElementById('save').onclick = async () => {
  let s = input.value.trim().replace(/\/+$/, '');
  if (!/^https?:\/\//.test(s)) s = 'http://' + s;
  let url;
  try { url = new URL(s); } catch { msg.textContent = '주소 형식이 잘못됨'; return; }
  // permissions.request는 클릭 직후에 호출해야 하므로 await보다 먼저 건다
  const granted = chrome.permissions.request({ origins: [`${url.protocol}//${url.hostname}/*`] });
  chrome.storage.local.set({ server: url.origin });
  input.value = url.origin;
  msg.textContent = await granted
    ? '저장됨 — CRD 화면 왼쪽 아래 스피커 버튼으로 연결'
    : '권한이 거부됨 — 서버에 접속할 수 없음';
};
