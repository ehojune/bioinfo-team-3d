// Minimal WebSocket client (RFC 6455) for Node versions without a global WebSocket (before 22).
// It only has to talk to Chrome's DevTools endpoint: text messages in both directions, fragmentation,
// ping/pong, close. The surface mirrors the browser class: onopen, onmessage({data}), onerror, onclose, send, close.
'use strict';
const http = require('node:http'), crypto = require('node:crypto');

class MiniWebSocket {
  constructor(url) {
    this.onopen = this.onmessage = this.onerror = this.onclose = null;
    this.buffer = Buffer.alloc(0);
    this.fragments = [];
    this.closed = false;
    this.closeReported = false;
    const target = new URL(url);
    const key = crypto.randomBytes(16).toString('base64');
    const request = http.request({host: target.hostname, port: target.port, path: target.pathname + target.search,
      headers: {Connection: 'Upgrade', Upgrade: 'websocket', 'Sec-WebSocket-Key': key, 'Sec-WebSocket-Version': '13'}});
    this.request = request;
    request.on('upgrade', (response, socket, head) => {
      // close() already ran while the upgrade was pending (#1107): nothing is left to own, and onclose was reported.
      if (this.closed) { socket.destroy(); return; }
      const accept = crypto.createHash('sha1').update(key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
      if (response.headers['sec-websocket-accept'] !== accept) { socket.destroy(); this.onerror?.(new Error('bad WebSocket handshake')); return; }
      this.socket = socket;
      socket.on('data', chunk => this.read(chunk));
      socket.on('close', () => { this.closed = true; this.reportClosed(); });
      socket.on('error', error => this.onerror?.(error));
      this.onopen?.();
      if (head.length) this.read(head);
    });
    request.on('response', () => this.onerror?.(new Error('DevTools endpoint refused the WebSocket upgrade')));
    // A connection that fails before the upgrade never gets a socket 'close' handler, so it reports its end here.
    request.on('error', error => {
      if (this.closed) return;  // the error of the request close() itself destroyed
      this.onerror?.(error);
      if (!this.socket) { this.closed = true; this.reportClosed(); }
    });
    request.end();
  }

  // The browser class fires one close event per socket, whatever ended it (#1107).
  reportClosed() {
    if (this.closeReported) return;
    this.closeReported = true;
    this.onclose?.();
  }

  read(chunk) {
    this.buffer = Buffer.concat([this.buffer, chunk]);
    for (;;) {
      const b = this.buffer;
      if (b.length < 2) return;
      let length = b[1] & 0x7f, offset = 2;
      if (length === 126) { if (b.length < 4) return; length = b.readUInt16BE(2); offset = 4; }
      else if (length === 127) { if (b.length < 10) return; length = Number(b.readBigUInt64BE(2)); offset = 10; }
      if (b.length < offset + length) return;
      const fin = Boolean(b[0] & 0x80), opcode = b[0] & 0x0f;
      const payload = b.subarray(offset, offset + length);  // server frames are never masked
      this.buffer = b.subarray(offset + length);
      if (opcode === 0x8) { this.close(); return; }
      if (opcode === 0x9) { this.frame(0xA, payload); continue; }
      if (opcode === 0xA) continue;
      this.fragments.push(payload);
      if (fin) {
        const text = Buffer.concat(this.fragments).toString('utf8');
        this.fragments = [];
        this.onmessage?.({data: text});
      }
    }
  }

  frame(opcode, payload) {
    if (!this.socket || this.closed) return;
    const mask = crypto.randomBytes(4);
    let header;
    if (payload.length < 126) header = Buffer.from([0x80 | opcode, 0x80 | payload.length]);
    else if (payload.length < 65536) { header = Buffer.alloc(4); header[0] = 0x80 | opcode; header[1] = 0x80 | 126; header.writeUInt16BE(payload.length, 2); }
    else { header = Buffer.alloc(10); header[0] = 0x80 | opcode; header[1] = 0x80 | 127; header.writeBigUInt64BE(BigInt(payload.length), 2); }
    const masked = Buffer.alloc(payload.length);
    for (let i = 0; i < payload.length; i++) masked[i] = payload[i] ^ mask[i % 4];
    this.socket.write(Buffer.concat([header, mask, masked]));
  }

  send(text) { this.frame(0x1, Buffer.from(String(text), 'utf8')); }

  close() {
    if (this.closed) return;
    if (!this.socket) {
      // Still waiting for the upgrade: end the pending request. No socket 'close' will ever arrive, so report it now.
      this.closed = true;
      try { this.request.destroy(); } catch { /* request already gone */ }
      this.reportClosed();
      return;
    }
    try { this.frame(0x8, Buffer.alloc(0)); this.socket.end(); } catch { /* socket already gone */ }
    this.closed = true;
  }
}

module.exports = {MiniWebSocket};
