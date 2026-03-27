#!/usr/bin/env python3
"""LG TV Remote — keyboard-driven remote control via SSAP WebSocket API."""

import socket
import ssl
import base64
import os
import sys
import json
import tty
import termios
import signal

CONFIG_PATH = os.path.expanduser("~/.config/lg-remote/config.json")
DEFAULT_CONFIG = {
    "host": "192.168.30.10",
    "port": 3001,
    "client_key": "",
}


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    return DEFAULT_CONFIG


def save_config(config):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


config = load_config()
TV_HOST = config["host"]
TV_PORT = config["port"]
CLIENT_KEY = config["client_key"]

# ANSI colors
DIM = "\033[2m"
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RED = "\033[31m"
RESET = "\033[0m"

KEY_MAP = {
    # Arrow keys (escape sequences)
    "\x1b[A": ("UP", "Up"),
    "\x1b[B": ("DOWN", "Down"),
    "\x1b[C": ("RIGHT", "Right"),
    "\x1b[D": ("LEFT", "Left"),
    # Enter
    "\r": ("ENTER", "OK"),
    "\n": ("ENTER", "OK"),
    # Backspace
    "\x7f": ("BACK", "Back"),
    "\x08": ("BACK", "Back"),
    # Space = menu
    " ": ("MENU", "Menu"),
    # F11/F12 (common terminal escape sequences)
    "\x1b[23~": ("VOLUMEDOWN", "Vol-"),
    "\x1b[24~": ("VOLUMEUP", "Vol+"),
    # Alternative volume: - and = (next to backspace, easy to reach)
    "-": ("VOLUMEDOWN", "Vol-"),
    "=": ("VOLUMEUP", "Vol+"),
    # Extra useful keys
    "h": ("HOME", "Home"),
    "m": ("MUTE", "Mute"),
    "i": ("INFO", "Info"),
    "p": ("POWER", "Power"),
    "1": ("1", "1"),
    "2": ("2", "2"),
    "3": ("3", "3"),
    "4": ("4", "4"),
    "5": ("5", "5"),
    "6": ("6", "6"),
    "7": ("7", "7"),
    "8": ("8", "8"),
    "9": ("9", "9"),
    "0": ("0", "0"),
}


class WebSocketClient:
    """Minimal WebSocket client for LG SSAP API."""

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.sock = None

    def connect(self):
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.settimeout(10)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self.sock = ctx.wrap_socket(raw, server_hostname=self.host)
        self.sock.connect((self.host, self.port))

        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET / HTTP/1.1\r\n"
            f"Host: {self.host}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        self.sock.send(handshake.encode())
        resp = self.sock.recv(4096).decode()
        if "101" not in resp:
            raise ConnectionError(f"WebSocket upgrade failed: {resp[:100]}")

    def send(self, message):
        payload = message.encode()
        frame = bytearray([0x81])
        mask_key = os.urandom(4)
        length = len(payload)
        if length < 126:
            frame.append(0x80 | length)
        elif length < 65536:
            frame.append(0x80 | 126)
            frame.extend(length.to_bytes(2, "big"))
        else:
            frame.append(0x80 | 127)
            frame.extend(length.to_bytes(8, "big"))
        frame.extend(mask_key)
        frame.extend(
            bytearray(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        )
        self.sock.send(frame)

    def recv(self):
        data = self.sock.recv(8192)
        if len(data) < 2:
            return ""
        opcode = data[0] & 0x0F
        if opcode == 0x8:
            return ""
        if opcode == 0x9:  # ping -> pong
            self.sock.send(bytearray([0x8A, 0x80]) + os.urandom(4))
            return self.recv()
        payload_len = data[1] & 0x7F
        offset = 2
        if payload_len == 126:
            payload_len = int.from_bytes(data[2:4], "big")
            offset = 4
        elif payload_len == 127:
            payload_len = int.from_bytes(data[2:10], "big")
            offset = 10
        return data[offset : offset + payload_len].decode("utf-8", errors="replace")

    def send_ping(self):
        """Send a WebSocket ping frame. Raises on dead connection."""
        if self.sock:
            ping_frame = bytearray([0x89, 0x80]) + os.urandom(4)
            self.sock.send(ping_frame)

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass


class WebSocketInputClient:
    """WebSocket client for the input/pointer socket (plain WS, no SSL)."""

    def __init__(self, url):
        # Parse ws://host:port/path or wss://host:port/path
        self.url = url
        self.use_ssl = url.startswith("wss://")
        url_body = url.replace("wss://", "").replace("ws://", "")
        host_port, self.path = url_body.split("/", 1)
        self.path = "/" + self.path
        if ":" in host_port:
            self.host, port_str = host_port.split(":")
            self.port = int(port_str)
        else:
            self.host = host_port
            self.port = 3001 if self.use_ssl else 3000
        self.sock = None

    def connect(self):
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.settimeout(5)
        if self.use_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self.sock = ctx.wrap_socket(raw, server_hostname=self.host)
        else:
            self.sock = raw
        self.sock.connect((self.host, self.port))

        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        self.sock.send(handshake.encode())
        resp = self.sock.recv(4096).decode()
        if "101" not in resp:
            raise ConnectionError(f"Input socket upgrade failed: {resp[:100]}")

    def _send_raw(self, msg):
        """Send a raw text message over the WebSocket."""
        payload = msg.encode()
        frame = bytearray([0x81])
        mask_key = os.urandom(4)
        length = len(payload)
        if length < 126:
            frame.append(0x80 | length)
        elif length < 65536:
            frame.append(0x80 | 126)
            frame.extend(length.to_bytes(2, "big"))
        else:
            frame.append(0x80 | 127)
            frame.extend(length.to_bytes(8, "big"))
        frame.extend(mask_key)
        frame.extend(
            bytearray(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        )
        self.sock.send(frame)

    def send_button(self, button_name):
        self._send_raw(f"type:button\nname:{button_name}\n\n")

    def send_move(self, dx, dy, drag=0):
        """Send pointer move. dx/dy are relative pixel deltas."""
        self._send_raw(f"type:move\ndx:{dx}\ndy:{dy}\ndrag:{drag}\n\n")

    def send_click(self):
        """Send pointer click at current position."""
        self._send_raw(f"type:click\n\n")

    def send_scroll(self, dx, dy):
        """Send scroll event. dy>0 = scroll down, dy<0 = scroll up."""
        self._send_raw(f"type:scroll\ndx:{dx}\ndy:{dy}\n\n")

    def send_ping(self):
        """Send a WebSocket ping frame. Raises on dead connection."""
        if self.sock:
            ping_frame = bytearray([0x89, 0x80]) + os.urandom(4)
            self.sock.send(ping_frame)

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass


PERMISSIONS = [
    "LAUNCH", "LAUNCH_WEBAPP", "APP_TO_APP",
    "CONTROL_AUDIO", "CONTROL_DISPLAY",
    "CONTROL_INPUT_JOYSTICK", "CONTROL_INPUT_MEDIA_RECORDING",
    "CONTROL_INPUT_MEDIA_PLAYBACK", "CONTROL_INPUT_TV",
    "CONTROL_MOUSE_AND_KEYBOARD", "CONTROL_INPUT_TEXT",
    "CONTROL_POWER", "READ_APP_STATUS", "READ_CURRENT_CHANNEL",
    "READ_INPUT_DEVICE_LIST", "READ_NETWORK_STATE",
    "READ_RUNNING_APPS", "READ_TV_CHANNEL_LIST",
    "WRITE_NOTIFICATION", "READ_POWER_STATE", "READ_COUNTRY_INFO",
    "READ_SETTINGS", "CONTROL_TV_SCREEN", "CONTROL_TV_STANDY",
]


def authenticate(ws):
    """Authenticate with the TV. Prompts for pairing on first use."""
    global CLIENT_KEY
    payload = {"pairingType": "PROMPT"}
    if CLIENT_KEY:
        payload["client-key"] = CLIENT_KEY
    payload["manifest"] = {
        "manifestVersion": 1,
        "appVersion": "1.1",
        "signed": {
            "created": "20140509",
            "appId": "com.lge.test",
            "vendorId": "com.lge",
            "localizedAppNames": {"": "LG Remote"},
            "localizedVendorNames": {"": "LG Electronics"},
            "permissions": PERMISSIONS,
            "serial": "SN123456",
        },
        "permissions": PERMISSIONS,
    }
    ws.send(json.dumps({"type": "register", "id": "reg", "payload": payload}))

    if not CLIENT_KEY:
        print(f"{YELLOW}Accept the pairing prompt on your TV...{RESET}")

    # May get a pairingType response first, then the key
    for _ in range(5):
        raw = ws.recv()
        if not raw:
            continue  # skip empty frames
        try:
            resp = json.loads(raw)
        except json.JSONDecodeError:
            continue  # skip non-JSON frames
        ck = resp.get("payload", {}).get("client-key", "")
        if ck:
            if ck != CLIENT_KEY:
                CLIENT_KEY = ck
                config["client_key"] = ck
                save_config(config)
                print(f"{GREEN}Paired and saved!{RESET}")
            return ck
        # Check for error responses
        err = resp.get("error")
        if err:
            raise ConnectionError(f"TV rejected: {err}")
    raise ConnectionError("Authentication failed — no key received")


def get_input_socket_url(ws):
    """Request the pointer/input WebSocket URL."""
    ws.send(
        json.dumps(
            {
                "type": "request",
                "id": "input",
                "uri": "ssap://com.webos.service.networkinput/getPointerInputSocket",
            }
        )
    )
    raw = ws.recv()
    if not raw:
        raise ConnectionError("Empty response for input socket")
    resp = json.loads(raw)
    url = resp.get("payload", {}).get("socketPath", "")
    if not url:
        raise ConnectionError("Failed to get input socket")
    return url


def read_key():
    """Read a keypress, handling escape sequences for special keys."""
    ch = sys.stdin.read(1)
    if ch == "\x1b":
        # Could be an escape sequence
        ch2 = sys.stdin.read(1)
        if ch2 == "[":
            ch3 = sys.stdin.read(1)
            if ch3 == "2":
                ch4 = sys.stdin.read(1)
                if ch4 == "3":
                    sys.stdin.read(1)  # consume ~
                    return "\x1b[23~"  # F11
                elif ch4 == "4":
                    sys.stdin.read(1)  # consume ~
                    return "\x1b[24~"  # F12
                return "\x1b[2" + ch4
            return "\x1b[" + ch3
        elif ch2 == "\x1b":
            return "\x1b"  # double escape = quit
        return "\x1b" + ch2
    return ch


def print_banner():
    """Print the remote control layout."""
    os.system("clear")
    print(f"{BOLD}{CYAN}LG TV Remote{RESET}  {DIM}(OLED65C5){RESET}")
    print(f"{DIM}{'─' * 40}{RESET}")
    print()
    print(f"  {BOLD}Navigation{RESET}")
    print(f"    {GREEN}↑ ↓ ← →{RESET}  Navigate")
    print(f"    {GREEN}Enter{RESET}    OK / Select")
    print(f"    {GREEN}⌫{RESET}        Back")
    print()
    print(f"  {BOLD}Controls{RESET}")
    print(f"    {GREEN}Space{RESET}    Menu")
    print(f"    {GREEN}h{RESET}        Home")
    print(f"    {GREEN}- ={RESET}      Volume Down / Up")
    print(f"    {GREEN}m{RESET}        Mute")
    print(f"    {GREEN}i{RESET}        Info")
    print(f"    {GREEN}p{RESET}        Power")
    print(f"    {GREEN}0-9{RESET}      Number keys")
    print()
    print(f"    {YELLOW}Esc{RESET}      Quit remote")
    print(f"{DIM}{'─' * 40}{RESET}")
    print()


def main():
    print(f"{CYAN}Connecting to LG TV...{RESET}")

    # Connect and authenticate
    ws = WebSocketClient(TV_HOST, TV_PORT)
    try:
        ws.connect()
        authenticate(ws)
    except Exception as e:
        print(f"{RED}Failed to connect: {e}{RESET}")
        sys.exit(1)

    # Get input socket
    try:
        input_url = get_input_socket_url(ws)
        input_ws = WebSocketInputClient(input_url)
        input_ws.connect()
    except Exception as e:
        print(f"{RED}Failed to get input socket: {e}{RESET}")
        ws.close()
        sys.exit(1)

    print_banner()

    # Save terminal settings and enter raw mode
    old_settings = termios.tcgetattr(sys.stdin)

    def cleanup(signum=None, frame=None):
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        input_ws.close()
        ws.close()
        print(f"\n{DIM}Disconnected.{RESET}")
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    try:
        tty.setraw(sys.stdin.fileno())

        while True:
            key = read_key()

            # Escape to quit
            if key == "\x1b" or key == "\x1b\x1b" or key == "\x03":  # Esc or Ctrl+C
                break

            if key in KEY_MAP:
                button, label = KEY_MAP[key]
                input_ws.send_button(button)
                # Move cursor to status line and show feedback
                sys.stdout.write(f"\r\033[K  {GREEN}>> {label}{RESET}")
                sys.stdout.flush()
            else:
                sys.stdout.write(f"\r\033[K  {DIM}(unmapped key){RESET}")
                sys.stdout.flush()

    finally:
        cleanup()


if __name__ == "__main__":
    main()
