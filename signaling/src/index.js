// 시그널링 중계. 호스트(맥)마다 Durable Object 방 1개, WebSocket으로 메시지를 그대로 전달만 한다.
// 메시지 본문(SDP)은 페어링 키로 종단간 암호화되어 있어 이 서버는 내용을 읽거나 바꿀 수 없다.
// 저장하는 것은 호스트 인증값의 해시 하나뿐 (같은 hostId로 다른 사람이 호스트 행세하는 것 방지).
import { DurableObject } from 'cloudflare:workers';

const MAX_CLIENTS = 8;
const MAX_MESSAGE = 16 * 1024;

export default {
  async fetch(req, env) {
    const m = new URL(req.url).pathname.match(/^\/ws\/([0-9a-f]{32})$/);
    if (!m) return new Response('not found', { status: 404 });
    if (req.headers.get('Upgrade') !== 'websocket') return new Response('websocket only', { status: 426 });
    return env.ROOM.get(env.ROOM.idFromName(m[1])).fetch(req);
  },
};

async function sha256hex(s) {
  const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
  return [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, '0')).join('');
}

export class Room extends DurableObject {
  async fetch(req) {
    const q = new URL(req.url).searchParams;
    const role = q.get('role');
    let peer;
    if (role === 'host') {
      const auth = q.get('auth') ?? '';
      if (!/^[0-9a-f]{64}$/.test(auth)) return new Response('bad auth', { status: 400 });
      const h = await sha256hex(auth);
      const stored = await this.ctx.storage.get('hostAuth');
      if (stored && stored !== h) return new Response('forbidden', { status: 403 });
      if (!stored) await this.ctx.storage.put('hostAuth', h);  // 처음 접속한 호스트를 기억 (TOFU)
      for (const ws of this.ctx.getWebSockets('host')) ws.close(4000, 'replaced');
      peer = 'host';
    } else if (role === 'client') {
      peer = q.get('peer') ?? '';
      if (!/^[0-9a-f]{16}$/.test(peer)) return new Response('bad peer', { status: 400 });
      if (this.ctx.getWebSockets('client').length >= MAX_CLIENTS) return new Response('busy', { status: 429 });
    } else {
      return new Response('bad role', { status: 400 });
    }
    const [client, server] = Object.values(new WebSocketPair());
    // Hibernation API: 메시지가 없을 땐 DO가 메모리에서 내려가 과금되지 않는다
    this.ctx.acceptWebSocket(server, [role, 'p:' + peer]);
    server.serializeAttachment({ peer });
    return new Response(null, { status: 101, webSocket: client });
  }

  async webSocketMessage(ws, raw) {
    if (typeof raw !== 'string' || raw.length > MAX_MESSAGE) return;
    let m;
    try { m = JSON.parse(raw); } catch { return; }
    const { peer } = ws.deserializeAttachment();
    const to = peer === 'host' ? String(m.to) : 'host';
    const targets = this.ctx.getWebSockets('p:' + to);
    if (!targets.length) {
      ws.send(JSON.stringify({ error: to === 'host' ? 'host-offline' : 'peer-gone' }));
      return;
    }
    const out = JSON.stringify({ from: peer, iv: m.iv, ct: m.ct });
    for (const t of targets) t.send(out);
  }

  async webSocketClose(ws, code, reason) {
    try { ws.close(code, reason); } catch {}
  }
}
