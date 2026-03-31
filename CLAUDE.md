# CLAUDE.md — LG Remote

Full-featured LG webOS TV remote via SSAP WebSocket API. Three interfaces: terminal TUI, web server, native macOS GUI.

## Entry points

- `lg_remote.py` — core library + terminal TUI (raw-mode keyboard, arrow keys). Esc to quit.
- `lg_remote_web.py` — HTTP server for mobile/iPhone (port 8888)
- `lg_remote_gui.py` — native macOS floating transparent pill GUI (PyObjC/AppKit/Quartz)
- `LG-TV-Remote.alfredworkflow` — Alfred integration

## Running

```bash
python lg_remote.py       # Terminal TUI
python lg_remote_web.py   # Web server (WEB_PORT=8888)
python lg_remote_gui.py   # macOS GUI
```

Config stored at `~/.config/lg-remote/config.json` (TV host, client key cached after first pairing).

## Key gotchas

- **Hand-rolled WebSocket client** — no library dependency, raw frame construction with masking
- **Two WebSocket connections**: main control socket + separate `WebSocketInputClient` for pointer/mouse (different protocol: `type:button`, `type:move`, `type:scroll`)
- **First connection** requires accepting a pairing prompt on the TV screen. Client key cached after that.
- **TV target**: 192.168.30.10:3001 (SSL, CERT_NONE)
- **GUI** uses `NSPanel` with `NSFloatingWindowLevel` for always-on-top overlay
