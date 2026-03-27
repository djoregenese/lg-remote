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
    launch_app,
    TV_HOST,
    TV_PORT,
)

# App shortcuts — must match IDs from the GUI remote
APP_SHORTCUTS = {
    "youtube": "youtube.leanback.v4",
    "plex": "cdp-30",
    "appletv": "com.apple.appletv",
    "paramount": "com.cbs-all-access.webapp.prod",
}

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
                # Give user 60s to accept the pairing prompt on the TV
                tv_ws.sock.settimeout(60)
                authenticate(tv_ws)
                tv_ws.sock.settimeout(10)
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


def send_launch(app_key):
    """Launch an app on the TV."""
    app_id = APP_SHORTCUTS.get(app_key)
    if not app_id:
        return False, "Unknown app"
    with tv_lock:
        if not tv_ws or not tv_status["connected"]:
            return False, "Not connected"
        try:
            launch_app(tv_ws, app_id)
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
  background: #0d0d14;
  color: #8888cc;
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
  font-size: 13px; color: #6666aa;
}
.status .dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: #ff3355; transition: background 0.3s;
  box-shadow: 0 0 6px #ff3355;
}
.status.connected .dot { background: #00ff88; box-shadow: 0 0 8px #00ff88; }
.title {
  font-size: 20px; font-weight: 700; color: #e0e4ff;
  padding: 4px 0 16px;
  letter-spacing: -0.5px;
}

/* Remote container */
.remote {
  display: flex; flex-direction: column; align-items: center;
  justify-content: space-evenly;
  gap: 0; width: 100%; max-width: 400px;
  flex: 1;
  padding: 0 8px;
}

/* D-Pad */
.dpad {
  position: relative;
  width: min(72vw, 280px); height: min(72vw, 280px);
}
.dpad-ring {
  position: absolute; inset: 0;
  border-radius: 50%;
  background: #111122;
  border: 1px solid #2a2a4a;
  box-shadow: inset 0 0 20px rgba(80,80,255,0.05);
}
.dpad-btn {
  position: absolute;
  display: flex; align-items: center; justify-content: center;
  color: #5566ff; font-size: 26px;
  cursor: pointer;
  z-index: 2;
  border-radius: 12px;
  transition: background 0.1s, color 0.1s;
}
.dpad-btn:active { color: #fff; background: rgba(85, 102, 255, 0.3); }
.dpad-up    { top: 6px;    left: 50%; transform: translateX(-50%); width: 80px; height: 72px; }
.dpad-down  { bottom: 6px; left: 50%; transform: translateX(-50%); width: 80px; height: 72px; }
.dpad-left  { left: 6px;   top: 50%; transform: translateY(-50%); width: 72px; height: 80px; }
.dpad-right { right: 6px;  top: 50%; transform: translateY(-50%); width: 72px; height: 80px; }
.dpad-ok {
  position: absolute;
  top: 50%; left: 50%; transform: translate(-50%, -50%);
  width: 80px; height: 80px; border-radius: 50%;
  background: #1a1a35;
  border: 2px solid #5566ff;
  display: flex; align-items: center; justify-content: center;
  font-size: 17px; font-weight: 600; color: #5566ff;
  cursor: pointer; z-index: 3;
  transition: background 0.1s;
  box-shadow: 0 0 12px rgba(85,102,255,0.3);
}
.dpad-ok:active { background: #5566ff; color: #fff; }

/* Nav row */
.nav-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  width: 100%; max-width: 340px;
}
.nav-btn {
  height: 50px; border-radius: 25px;
  background: #13132a;
  border: 1px solid #2a2a4a;
  color: #8888cc;
  font-size: 16px; font-weight: 500;
  cursor: pointer;
  transition: background 0.1s;
  display: flex; align-items: center; justify-content: center;
}
.nav-btn:active { background: #5566ff; color: #fff; }

/* App shortcuts */
.app-row {
  display: flex; justify-content: center; gap: 12px;
  width: 100%; max-width: 340px;
}
.app-btn {
  width: 56px; height: 56px; border-radius: 14px;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 2px;
  cursor: pointer;
  border: 1px solid rgba(255,255,255,0.15);
  transition: opacity 0.1s;
}
.app-btn:active { opacity: 0.6; }
.app-btn .app-icon { font-size: 20px; line-height: 1; }
.app-btn .app-label { font-size: 9px; color: rgba(255,255,255,0.7); font-weight: 500; }

/* Bottom controls */
.bottom-row {
  display: flex; align-items: center; justify-content: space-between;
  width: 100%; max-width: 340px;
  gap: 20px;
}

/* Volume rocker */
.vol-rocker {
  display: flex; flex-direction: column;
  border-radius: 32px;
  background: #111125;
  border: 1px solid #2a2a4a;
  overflow: hidden;
  width: 68px;
}
.vol-btn {
  height: 58px;
  display: flex; align-items: center; justify-content: center;
  font-size: 24px; font-weight: 600; color: #8888cc;
  cursor: pointer;
  transition: background 0.1s;
}
.vol-btn:active { background: #5566ff; color: #fff; }
.vol-divider {
  height: 1px; background: #2a2a4a; margin: 0 10px;
}

/* Mute */
.mute-btn {
  width: 68px; height: 40px; border-radius: 20px;
  background: #111125; border: 1px solid #2a2a4a;
  color: #8888cc; font-size: 13px; font-weight: 500;
  cursor: pointer; display: flex; align-items: center; justify-content: center;
  transition: background 0.1s;
}
.mute-btn:active { background: #5566ff; color: #fff; }

/* Power */
.power-btn {
  width: 64px; height: 64px; border-radius: 50%;
  background: #1a0a0a;
  border: 2px solid #ff3355;
  color: #ff3355;
  font-size: 28px;
  cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: background 0.1s;
  box-shadow: 0 0 12px rgba(255,51,85,0.3);
}
.power-btn:active { background: #ff3355; color: #fff; }

/* Channel rocker */
.ch-rocker {
  display: flex; flex-direction: column;
  border-radius: 32px;
  background: #111125;
  border: 1px solid #2a2a4a;
  overflow: hidden;
  width: 68px;
}
.ch-btn {
  height: 58px;
  display: flex; align-items: center; justify-content: center;
  font-size: 14px; font-weight: 600; color: #8888cc;
  cursor: pointer;
  transition: background 0.1s;
}
.ch-btn:active { background: #5566ff; color: #fff; }

/* Feedback toast */
.toast {
  position: fixed; bottom: calc(40px + env(safe-area-inset-bottom)); left: 50%; transform: translateX(-50%);
  background: rgba(85, 102, 255, 0.85);
  color: #fff; font-size: 14px; font-weight: 600;
  padding: 8px 20px; border-radius: 20px;
  opacity: 0; transition: opacity 0.15s;
  pointer-events: none;
}
.toast.show { opacity: 1; }

/* Haptic feedback — opacity flash avoids clobbering translate transforms on d-pad */
[data-btn] { transition: opacity 0.1s, background 0.1s, color 0.1s; }
[data-btn].pressing { opacity: 0.5; }
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

  <!-- App shortcuts -->
  <div class="app-row">
    <div class="app-btn" data-app="youtube" style="background:rgba(230,30,30,0.9)">
      <span class="app-icon">&#9654;</span><span class="app-label">YT</span>
    </div>
    <div class="app-btn" data-app="plex" style="background:rgba(230,166,12,0.9)">
      <span class="app-icon">&#9654;</span><span class="app-label">Plex</span>
    </div>
    <div class="app-btn" data-app="appletv" style="background:rgba(30,30,36,0.9)">
      <span class="app-icon">&#63743;</span><span class="app-label">TV+</span>
    </div>
    <div class="app-btn" data-app="paramount" style="background:rgba(0,87,212,0.9)">
      <span class="app-icon">&#9968;</span><span class="app-label">P+</span>
    </div>
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

// Bind app shortcut buttons
document.querySelectorAll('[data-app]').forEach(el => {
  function fire(e) {
    e.preventDefault();
    const app = el.getAttribute('data-app');
    el.style.opacity = '0.5';
    setTimeout(() => el.style.opacity = '1', 150);
    if (navigator.vibrate) navigator.vibrate(10);
    fetch('/api/launch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({app: app})
    }).then(r => r.json()).then(d => {
      showToast(d.ok ? app : 'ERR: ' + d.error);
    }).catch(() => showToast('Network error'));
  }
  el.addEventListener('touchstart', fire, {passive: false});
  el.addEventListener('mousedown', fire);
});

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
        elif self.path == "/api/launch":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            app_key = body.get("app", "")
            ok, msg = send_launch(app_key)
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
