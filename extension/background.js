// 시그널링(/offer POST)만 대신 보낸다. https인 CRD 페이지의 content script가 http://<맥미니IP>로
// 직접 fetch하면 mixed content로 차단되기 때문. 연결·재생은 content script에 있으므로
// 이 worker가 유휴 종료(약 30초)돼도 오디오는 끊기지 않는다.
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg.type !== 'offer') return;
  fetch(`${msg.server}/offer`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(msg.offer),
  })
    .then(r => r.ok ? r.json() : Promise.reject(new Error(`서버 응답 ${r.status}`)))
    .then(answer => reply({ answer }),
          e => reply({ error: e instanceof TypeError ? '서버 연결 실패 (서버 실행·팝업 권한 확인)' : e.message }));
  return true;  // 비동기 응답
});
