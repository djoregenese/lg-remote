#!/usr/bin/env python3
"""LG TV Remote — web server for iPhone/mobile access."""

import json
import os
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lg_remote import (
    WebSocketClient,
    WebSocketInputClient,
    authenticate,
    get_input_socket_url,
    TV_HOST,
    TV_PORT,
)

WEB_PORT = int(os.environ.get("WEB_PORT", "8888"))

# Shared TV connection state
tv_lock = threading.Lock()
tv_ws = None
tv_input = None
tv_status = {"connected": False, "message": "Disconnected"}


def connect_tv():
    """Connect to TV with retry logic."""
    global tv_ws, tv_input
    delays = [5, 15, 30]
    for attempt in range(len(delays) + 1):
        try:
            with tv_lock:
                if tv_ws:
                    tv_ws.close()
                tv_ws = WebSocketClient(TV_HOST, TV_PORT)
                tv_ws.connect()
                authenticate(tv_ws)
                url = get_input_socket_url(tv_ws)
                tv_input = WebSocketInputClient(url)
                tv_input.connect()
                tv_status["connected"] = True
                tv_status["message"] = "Connected"
            print(f"Connected to TV at {TV_HOST}")
            return
        except Exception as e:
            msg = str(e)
            if "Expecting value" in msg or "JSONDecode" in msg:
                msg = "TV sent empty response"
            elif "no key received" in msg:
                msg = "Auth failed"
            if attempt < len(delays):
                wait = delays[attempt]
                tv_status["message"] = f"{msg} — retry in {wait}s"
                print(f"Connection failed: {msg} — retrying in {wait}s")
                time.sleep(wait)
            else:
                tv_status["message"] = f"{msg} — giving up"
                print(f"Connection failed: {msg}")


def keepalive_loop():
    """Ping TV every 15s, reconnect on failure."""
    while True:
        time.sleep(15)
        with tv_lock:
            if not tv_status["connected"]:
                continue
            try:
                if tv_ws:
                    tv_ws.send_ping()
                if tv_input:
                    tv_input.send_ping()
            except Exception:
                tv_status["connected"] = False
                tv_status["message"] = "Connection lost"
                print("Connection lost — reconnecting...")
                threading.Thread(target=connect_tv, daemon=True).start()


def send_button(name):
    """Send a button press to the TV."""
    with tv_lock:
        if not tv_input or not tv_status["connected"]:
            return False, "Not connected"
        try:
            tv_input.send_button(name)
            return True, "OK"
        except Exception as e:
            tv_status["connected"] = False
            tv_status["message"] = "Connection lost"
            threading.Thread(target=connect_tv, daemon=True).start()
            return False, str(e)


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="LG Remote">
<title>LG Remote</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
html, body {
  height: 100%; width: 100%;
  background: #0d0d11;
  color: #d0d0d8;
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro', system-ui, sans-serif;
  overflow: hidden;
  touch-action: manipulation;
  user-select: none; -webkit-user-select: none;
}
body {
  display: flex; flex-direction: column; align-items: center;
  padding: env(safe-area-inset-top) 16px env(safe-area-inset-bottom);
}

/* Status bar */
.status {
  display: flex; align-items: center; gap: 8px;
  padding: 12px 0 4px;
  font-size: 13px; color: #888;
}
.status .dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: #d44; transition: background 0.3s;
}
.status.connected .dot { background: #4c4; }
.title {
  font-size: 20px; font-weight: 700; color: #e0e0e8;
  padding: 4px 0 16px;
  letter-spacing: -0.5px;
}

/* Remote container */
.remote {
  display: flex; flex-direction: column; align-items: center;
  gap: 20px; width: 100%; max-width: 320px;
  flex: 1;
}

/* D-Pad */
.dpad {
  position: relative;
  width: 220px; height: 220px;
}
.dpad-ring {
  position: absolute; inset: 0;
  border-radius: 50%;
  background: #1a1a22;
  border: 1px solid #2a2a35;
}
.dpad-btn {
  position: absolute;
  display: flex; align-items: center; justify-content: center;
  color: #c0c0c8; font-size: 22px;
  cursor: pointer;
  z-index: 2;
  transition: color 0.1s;
}
.dpad-btn:active { color: #fff; }
.dpad-up    { top: 8px;   left: 50%; transform: translateX(-50%); width: 70px; height: 60px; }
.dpad-down  { bottom: 8px; left: 50%; transform: translateX(-50%); width: 70px; height: 60px; }
.dpad-left  { left: 8px;  top: 50%; transform: translateY(-50%); width: 60px; height: 70px; }
.dpad-right { right: 8px; top: 50%; transform: translateY(-50%); width: 60px; height: 70px; }
.dpad-ok {
  position: absolute;
  top: 50%; left: 50%; transform: translate(-50%, -50%);
  width: 72px; height: 72px; border-radius: 50%;
  background: #222233;
  border: 1px solid #333345;
  display: flex; align-items: center; justify-content: center;
  font-size: 15px; font-weight: 600; color: #d0d0d8;
  cursor: pointer; z-index: 3;
  transition: background 0.1s;
}
.dpad-ok:active { background: #3355aa; color: #fff; }

/* Touch highlight for d-pad arrows */
.dpad-btn:active::after {
  content: '';
  position: absolute; inset: 0;
  border-radius: 50%;
  background: rgba(60, 100, 200, 0.25);
}

/* Nav row */
.nav-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  width: 100%; max-width: 260px;
}
.nav-btn {
  height: 44px; border-radius: 22px;
  background: #1e1e28;
  border: 1px solid #2a2a35;
  color: #c0c0c8;
  font-size: 14px; font-weight: 500;
  cursor: pointer;
  transition: background 0.1s;
  display: flex; align-items: center; justify-content: center;
}
.nav-btn:active { background: #3355aa; color: #fff; }

/* Bottom controls */
.bottom-row {
  display: flex; align-items: center; justify-content: space-between;
  width: 100%; max-width: 280px;
  gap: 16px;
}

/* Volume rocker */
.vol-rocker {
  display: flex; flex-direction: column;
  border-radius: 28px;
  background: #1a1a22;
  border: 1px solid #2a2a35;
  overflow: hidden;
  width: 60px;
}
.vol-btn {
  height: 52px;
  display: flex; align-items: center; justify-content: center;
  font-size: 22px; font-weight: 600; color: #c0c0c8;
  cursor: pointer;
  transition: background 0.1s;
}
.vol-btn:active { background: #3355aa; color: #fff; }
.vol-divider {
  height: 1px; background: #2a2a35; margin: 0 8px;
}

/* Mute */
.mute-btn {
  width: 60px; height: 36px; border-radius: 18px;
  background: #1e1e28; border: 1px solid #2a2a35;
  color: #c0c0c8; font-size: 12px; font-weight: 500;
  cursor: pointer; display: flex; align-items: center; justify-content: center;
  transition: background 0.1s;
}
.mute-btn:active { background: #3355aa; color: #fff; }

/* Power */
.power-btn {
  width: 56px; height: 56px; border-radius: 50%;
  background: #3a1515;
  border: 2px solid #5a2020;
  color: #e04040;
  font-size: 24px;
  cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: background 0.1s;
}
.power-btn:active { background: #c02020; color: #fff; }

/* Channel rocker */
.ch-rocker {
  display: flex; flex-direction: column;
  border-radius: 28px;
  background: #1a1a22;
  border: 1px solid #2a2a35;
  overflow: hidden;
  width: 60px;
}
.ch-btn {
  height: 52px;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; font-weight: 600; color: #c0c0c8;
  cursor: pointer;
  transition: background 0.1s;
}
.ch-btn:active { background: #3355aa; color: #fff; }

/* Feedback toast */
.toast {
  position: fixed; bottom: 40px; left: 50%; transform: translateX(-50%);
  background: rgba(50, 80, 180, 0.85);
  color: #fff; font-size: 13px; font-weight: 600;
  padding: 6px 18px; border-radius: 20px;
  opacity: 0; transition: opacity 0.15s;
  pointer-events: none;
}
.toast.show { opacity: 1; }

/* Haptic feedback visual */
@keyframes press {
  0% { transform: scale(1); }
  50% { transform: scale(0.92); }
  100% { transform: scale(1); }
}
.pressing { animation: press 0.15s ease; }
</style>
</head>
<body>

<div class="status" id="status">
  <div class="dot"></div>
  <span id="status-text">Connecting...</span>
</div>
<div class="title">LG Remote</div>

<div class="remote">
  <!-- D-Pad -->
  <div class="dpad">
    <div class="dpad-ring"></div>
    <div class="dpad-btn dpad-up" data-btn="UP">&#9650;</div>
    <div class="dpad-btn dpad-down" data-btn="DOWN">&#9660;</div>
    <div class="dpad-btn dpad-left" data-btn="LEFT">&#9664;</div>
    <div class="dpad-btn dpad-right" data-btn="RIGHT">&#9654;</div>
    <div class="dpad-ok" data-btn="ENTER">OK</div>
  </div>

  <!-- Nav buttons -->
  <div class="nav-row">
    <div class="nav-btn" data-btn="MENU">Menu</div>
    <div class="nav-btn" data-btn="BACK">Back</div>
    <div class="nav-btn" data-btn="HOME">Home</div>
    <div class="nav-btn" data-btn="INFO">Info</div>
  </div>

  <!-- Bottom: Volume, Power, Channels -->
  <div class="bottom-row">
    <div style="display:flex;flex-direction:column;align-items:center;gap:8px;">
      <div class="vol-rocker">
        <div class="vol-btn" data-btn="VOLUMEUP">+</div>
        <div class="vol-divider"></div>
        <div class="vol-btn" data-btn="VOLUMEDOWN">&minus;</div>
      </div>
      <div class="mute-btn" data-btn="MUTE">Mute</div>
    </div>

    <div class="power-btn" data-btn="POWER">&#9211;</div>

    <div style="display:flex;flex-direction:column;align-items:center;gap:8px;">
      <div class="ch-rocker">
        <div class="ch-btn" data-btn="CHANNELUP">CH &#9650;</div>
        <div class="vol-divider"></div>
        <div class="ch-btn" data-btn="CHANNELDOWN">CH &#9660;</div>
      </div>
      <div style="height:36px;"></div>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const toast = document.getElementById('toast');
const statusEl = document.getElementById('status');
const statusText = document.getElementById('status-text');
let toastTimer;

function showToast(msg) {
  toast.textContent = msg;
  toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('show'), 400);
}

async function sendBtn(name) {
  try {
    const r = await fetch('/api/button', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({button: name})
    });
    const d = await r.json();
    if (d.ok) {
      showToast(name);
    } else {
      showToast('ERR: ' + d.error);
    }
  } catch (e) {
    showToast('Network error');
  }
}

// Poll status
async function pollStatus() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    statusText.textContent = d.message;
    if (d.connected) {
      statusEl.classList.add('connected');
    } else {
      statusEl.classList.remove('connected');
    }
  } catch (e) {
    statusText.textContent = 'Server unreachable';
    statusEl.classList.remove('connected');
  }
}
setInterval(pollStatus, 3000);
pollStatus();

// Bind all buttons
document.querySelectorAll('[data-btn]').forEach(el => {
  function fire(e) {
    e.preventDefault();
    const btn = el.getAttribute('data-btn');
    el.classList.add('pressing');
    setTimeout(() => el.classList.remove('pressing'), 150);
    // Haptic feedback if available
    if (navigator.vibrate) navigator.vibrate(10);
    sendBtn(btn);
  }
  el.addEventListener('touchstart', fire, {passive: false});
  el.addEventListener('mousedown', fire);
});
</script>
</body>
</html>"""


class RemoteHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Quieter logs
        pass

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode())
        elif self.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(tv_status).encode())
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/button":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            button = body.get("button", "")
            valid = {
                "UP", "DOWN", "LEFT", "RIGHT", "ENTER",
                "BACK", "MENU", "HOME", "INFO", "MUTE",
                "VOLUMEUP", "VOLUMEDOWN", "CHANNELUP", "CHANNELDOWN",
                "POWER", "PLAY", "PAUSE", "FASTFORWARD", "REWIND",
                "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
            }
            if button not in valid:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "Invalid button"}).encode())
                return

            ok, msg = send_button(button)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": ok, "error": msg if not ok else None}).encode())
        else:
            self.send_error(404)


def main():
    # Connect to TV in background
    threading.Thread(target=connect_tv, daemon=True).start()
    threading.Thread(target=keepalive_loop, daemon=True).start()

    server = HTTPServer(("0.0.0.0", WEB_PORT), RemoteHandler)
    print(f"LG Remote web UI: http://0.0.0.0:{WEB_PORT}")
    print(f"Open on iPhone: http://<your-mac-ip>:{WEB_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
