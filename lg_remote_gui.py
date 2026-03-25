#!/usr/bin/env python3
"""LG TV Remote — floating transparent GUI (pill-shaped modern design)."""

import os
import sys
import json
import math
import threading
import time

import AppKit
import objc

from AppKit import (
    NSApplication,
    NSApp,
    NSWindow,
    NSPanel,
    NSView,
    NSColor,
    NSFont,
    NSTextField,
    NSMutableParagraphStyle,
    NSAttributedString,
    NSBezierPath,
    NSFloatingWindowLevel,
    NSBackingStoreBuffered,
    NSEvent,
    NSKeyDownMask,
    NSTimer,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSMakeRect, NSMakePoint, NSMakeSize, NSObject, NSDictionary
from AppKit import (
    NSForegroundColorAttributeName,
    NSFontAttributeName,
    NSParagraphStyleAttributeName,
    NSMutableAttributedString,
)

# Import our WebSocket client
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lg_remote import (
    WebSocketClient,
    WebSocketInputClient,
    authenticate,
    get_input_socket_url,
    TV_HOST,
    TV_PORT,
)

# Key code to TV button mapping (macOS virtual key codes)
KEYCODE_MAP = {
    126: "UP",
    125: "DOWN",
    123: "LEFT",
    124: "RIGHT",
    36: "ENTER",
    51: "BACK",
    49: "MENU",
    53: None,          # escape = quit
    27: "VOLUMEDOWN",  # - key
    24: "VOLUMEUP",    # = key
    46: "MUTE",        # m
    34: "INFO",        # i
    4: "HOME",         # h
    35: "POWER",       # p
    # Number keys
    29: "0", 18: "1", 19: "2", 20: "3", 21: "4",
    23: "5", 22: "6", 26: "7", 28: "8", 25: "9",
}

WINDOW_WIDTH = 220
WINDOW_HEIGHT = 420

# -- Color palette --
COLOR_BODY_BG = (0.10, 0.10, 0.13, 0.95)
COLOR_BODY_BORDER = (0.25, 0.25, 0.32, 0.5)
COLOR_BTN_NORMAL = (0.18, 0.18, 0.24, 0.9)
COLOR_BTN_HOVER = (0.30, 0.45, 0.85, 0.85)
COLOR_BTN_BORDER = (0.30, 0.30, 0.38, 0.5)
COLOR_TEXT = (0.82, 0.82, 0.88, 1.0)
COLOR_TEXT_DIM = (0.45, 0.45, 0.55, 0.7)
COLOR_ACCENT_BLUE = (0.35, 0.55, 0.95, 1.0)
COLOR_ACCENT_RED = (0.85, 0.25, 0.25, 1.0)
COLOR_POWER_BG = (0.35, 0.12, 0.12, 0.9)
COLOR_CONNECTED = (0.35, 0.75, 0.45, 0.9)
COLOR_DISCONNECTED = (0.85, 0.35, 0.35, 0.9)
COLOR_DPAD_BG = (0.14, 0.14, 0.19, 0.9)
COLOR_DPAD_SEGMENT = (0.20, 0.20, 0.28, 0.9)
COLOR_DPAD_ACTIVE = (0.30, 0.50, 0.90, 0.85)
COLOR_OK_BG = (0.25, 0.25, 0.35, 0.95)
COLOR_VOL_BG = (0.16, 0.16, 0.22, 0.9)
COLOR_VOL_DIVIDER = (0.30, 0.30, 0.38, 0.5)


def make_color(r, g, b, a=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)


def make_color_t(tup):
    return make_color(*tup)


class RemotePanel(NSPanel):
    """NSPanel subclass that terminates the app on close."""

    def close(self):
        NSApp.terminate_(None)


class RemoteView(NSView):
    """Custom drawn view for the remote control UI."""

    def initWithFrame_(self, frame):
        self = objc.super(RemoteView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.statusText = "Connecting..."
        self.statusConnected = False
        self.activeButton = ""
        self.flashTimer = None
        self.dragOrigin = None
        return self

    def isFlipped(self):
        return True

    def isOpaque(self):
        return False

    def acceptsFirstResponder(self):
        return True

    # -- Drawing entry point --

    def drawRect_(self, rect):
        self.draw_body()
        self.draw_status_indicator()
        self.draw_title()
        self.draw_dpad()
        self.draw_nav_buttons()
        self.draw_volume_rocker()
        self.draw_power_button()
        self.draw_footer()
        self.draw_flash_indicator()

    # -- Body --

    def draw_body(self):
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            self.bounds(), 28, 28
        )
        make_color_t(COLOR_BODY_BG).set()
        path.fill()
        make_color_t(COLOR_BODY_BORDER).set()
        path.setLineWidth_(1.0)
        path.stroke()

    # -- Status indicator (small dot + text) --

    def draw_status_indicator(self):
        dot_x, dot_y = 16, 16
        dot_r = 5
        dot_rect = NSMakeRect(dot_x, dot_y, dot_r * 2, dot_r * 2)
        dot_path = NSBezierPath.bezierPathWithOvalInRect_(dot_rect)
        if self.statusConnected:
            make_color_t(COLOR_CONNECTED).set()
        else:
            make_color_t(COLOR_DISCONNECTED).set()
        dot_path.fill()

        label = self.statusText if self.statusText else ""
        color = make_color_t(COLOR_CONNECTED) if self.statusConnected else make_color_t(COLOR_DISCONNECTED)
        self.draw_label(label, 30, 14, 170, 16, size=10, color=color, align=0)

    # -- Title --

    def draw_title(self):
        self.draw_label("LG Remote", 0, 38, WINDOW_WIDTH, 24,
                        size=16, bold=True, color=make_color_t(COLOR_TEXT))

    # -- D-pad (circular with arc segments) --

    def draw_dpad(self):
        cx = WINDOW_WIDTH / 2
        cy = 120
        outer_r = 60
        inner_r = 24

        # Outer circle background
        outer_rect = NSMakeRect(cx - outer_r, cy - outer_r, outer_r * 2, outer_r * 2)
        outer_path = NSBezierPath.bezierPathWithOvalInRect_(outer_rect)
        make_color_t(COLOR_DPAD_BG).set()
        outer_path.fill()

        # Draw four arc segments (UP, RIGHT, DOWN, LEFT)
        directions = [
            ("UP",    -135, -45),
            ("RIGHT",  -45,  45),
            ("DOWN",    45, 135),
            ("LEFT",   135, 225),
        ]

        for btn_id, start_angle, end_angle in directions:
            is_active = self.activeButton == btn_id
            self.draw_arc_segment(cx, cy, inner_r + 4, outer_r - 3,
                                  start_angle, end_angle, is_active)

        # Draw directional arrows on each segment
        arrow_dist = (inner_r + outer_r) / 2
        arrows = [
            ("UP",    cx,              cy - arrow_dist, "\u25B2"),
            ("DOWN",  cx,              cy + arrow_dist, "\u25BC"),
            ("LEFT",  cx - arrow_dist, cy,              "\u25C0"),
            ("RIGHT", cx + arrow_dist, cy,              "\u25B6"),
        ]
        for btn_id, ax, ay, symbol in arrows:
            is_active = self.activeButton == btn_id
            tc = make_color(1, 1, 1, 1) if is_active else make_color_t(COLOR_TEXT)
            self.draw_label(symbol, ax - 10, ay - 8, 20, 16, size=12, color=tc)

        # OK button in center
        ok_active = self.activeButton == "ENTER"
        ok_rect = NSMakeRect(cx - inner_r, cy - inner_r, inner_r * 2, inner_r * 2)
        ok_path = NSBezierPath.bezierPathWithOvalInRect_(ok_rect)
        if ok_active:
            make_color_t(COLOR_DPAD_ACTIVE).set()
        else:
            make_color_t(COLOR_OK_BG).set()
        ok_path.fill()
        make_color_t(COLOR_BTN_BORDER).set()
        ok_path.setLineWidth_(1.0)
        ok_path.stroke()

        tc = make_color(1, 1, 1, 1) if ok_active else make_color_t(COLOR_TEXT)
        self.draw_label("OK", cx - 15, cy - 8, 30, 16, size=13, bold=True, color=tc)

    def draw_arc_segment(self, cx, cy, r_inner, r_outer, start_deg, end_deg, active):
        """Draw an arc segment between two radii. Angles in degrees, flipped-Y."""
        path = NSBezierPath.alloc().init()

        # In flipped coordinates, we negate angles to match expected visual direction
        sa_rad = math.radians(-start_deg)
        ea_rad = math.radians(-end_deg)

        # Outer arc (counterclockwise in flipped = clockwise visually)
        path.moveToPoint_(NSMakePoint(
            cx + r_outer * math.cos(sa_rad),
            cy - r_outer * math.sin(sa_rad)
        ))
        path.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
            NSMakePoint(cx, cy), r_outer, -start_deg, -end_deg, True
        )

        # Line to inner arc
        path.lineToPoint_(NSMakePoint(
            cx + r_inner * math.cos(ea_rad),
            cy - r_inner * math.sin(ea_rad)
        ))

        # Inner arc (clockwise in flipped = counterclockwise visually)
        path.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
            NSMakePoint(cx, cy), r_inner, -end_deg, -start_deg, False
        )

        path.closePath()

        if active:
            make_color_t(COLOR_DPAD_ACTIVE).set()
        else:
            make_color_t(COLOR_DPAD_SEGMENT).set()
        path.fill()

    # -- Navigation buttons (Menu, Back, Home, Info) --

    def draw_nav_buttons(self):
        y = 195
        btn_w = 80
        btn_h = 28
        gap = 10
        # Two columns, two rows
        col1_x = WINDOW_WIDTH / 2 - btn_w - gap / 2
        col2_x = WINDOW_WIDTH / 2 + gap / 2

        buttons = [
            ("Menu",  "MENU", col1_x, y),
            ("Back",  "BACK", col2_x, y),
            ("Home",  "HOME", col1_x, y + btn_h + 8),
            ("Info",  "INFO", col2_x, y + btn_h + 8),
        ]

        for label, btn_id, bx, by in buttons:
            self.draw_pill_button(label, btn_id, bx, by, btn_w, btn_h)

    def draw_pill_button(self, label, btn_id, x, y, w, h):
        is_active = self.activeButton == btn_id
        radius = h / 2
        btn_rect = NSMakeRect(x, y, w, h)
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            btn_rect, radius, radius)

        if is_active:
            make_color_t(COLOR_BTN_HOVER).set()
        else:
            make_color_t(COLOR_BTN_NORMAL).set()
        path.fill()

        make_color_t(COLOR_BTN_BORDER).set()
        path.setLineWidth_(0.8)
        path.stroke()

        tc = make_color(1, 1, 1, 1) if is_active else make_color_t(COLOR_TEXT)
        self.draw_label(label, x, y + 5, w, h - 8, size=12, color=tc)

    # -- Volume rocker (tall pill with +/- sections) --

    def draw_volume_rocker(self):
        vx = 30
        vy = 275
        vw = 50
        vh = 70
        radius = vw / 2

        # Outer pill
        vol_rect = NSMakeRect(vx, vy, vw, vh)
        vol_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            vol_rect, radius, radius)

        make_color_t(COLOR_VOL_BG).set()
        vol_path.fill()
        make_color_t(COLOR_BTN_BORDER).set()
        vol_path.setLineWidth_(0.8)
        vol_path.stroke()

        # Divider line
        div_y = vy + vh / 2
        div_path = NSBezierPath.bezierPath()
        div_path.moveToPoint_(NSMakePoint(vx + 6, div_y))
        div_path.lineToPoint_(NSMakePoint(vx + vw - 6, div_y))
        make_color_t(COLOR_VOL_DIVIDER).set()
        div_path.setLineWidth_(1.0)
        div_path.stroke()

        # + top half
        vol_up_active = self.activeButton == "VOLUMEUP"
        if vol_up_active:
            clip_rect = NSMakeRect(vx, vy, vw, vh / 2)
            clip_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                vol_rect, radius, radius)
            AppKit.NSGraphicsContext.currentContext().saveGraphicsState()
            NSBezierPath.clipRect_(clip_rect)
            make_color_t(COLOR_BTN_HOVER).set()
            clip_path.fill()
            AppKit.NSGraphicsContext.currentContext().restoreGraphicsState()

        tc_up = make_color(1, 1, 1, 1) if vol_up_active else make_color_t(COLOR_TEXT)
        self.draw_label("+", vx, vy + 6, vw, 20, size=16, bold=True, color=tc_up)

        # - bottom half
        vol_down_active = self.activeButton == "VOLUMEDOWN"
        if vol_down_active:
            clip_rect = NSMakeRect(vx, vy + vh / 2, vw, vh / 2)
            clip_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                vol_rect, radius, radius)
            AppKit.NSGraphicsContext.currentContext().saveGraphicsState()
            NSBezierPath.clipRect_(clip_rect)
            make_color_t(COLOR_BTN_HOVER).set()
            clip_path.fill()
            AppKit.NSGraphicsContext.currentContext().restoreGraphicsState()

        tc_dn = make_color(1, 1, 1, 1) if vol_down_active else make_color_t(COLOR_TEXT)
        self.draw_label("\u2212", vx, vy + vh / 2 + 6, vw, 20, size=16, bold=True, color=tc_dn)

        # Mute button below volume rocker
        mute_y = vy + vh + 10
        self.draw_pill_button("Mute", "MUTE", vx - 2, mute_y, vw + 4, 26)

    # -- Power button --

    def draw_power_button(self):
        px = WINDOW_WIDTH - 78
        py = 300
        pw, ph = 50, 50

        is_active = self.activeButton == "POWER"
        power_rect = NSMakeRect(px, py, pw, ph)
        power_path = NSBezierPath.bezierPathWithOvalInRect_(power_rect)

        if is_active:
            make_color(0.95, 0.2, 0.2, 0.9).set()
        else:
            make_color_t(COLOR_POWER_BG).set()
        power_path.fill()

        make_color(0.6, 0.2, 0.2, 0.6).set()
        power_path.setLineWidth_(1.5)
        power_path.stroke()

        tc = make_color(1, 1, 1, 1) if is_active else make_color_t(COLOR_ACCENT_RED)
        self.draw_label("\u23FB", px, py + 14, pw, 22, size=18, bold=True, color=tc)

    # -- Footer --

    def draw_footer(self):
        self.draw_label("Q or Esc to close",
                        0, WINDOW_HEIGHT - 28, WINDOW_WIDTH, 16,
                        size=9, color=make_color_t(COLOR_TEXT_DIM))

    # -- Flash indicator --

    def draw_flash_indicator(self):
        if self.activeButton:
            label = self.activeButton
            self.draw_label(label, 0, WINDOW_HEIGHT - 48, WINDOW_WIDTH, 18,
                            size=11, bold=True, color=make_color_t(COLOR_ACCENT_BLUE))

    # -- Text drawing helper (safe name: no underscore prefix) --

    def draw_label(self, text, x, y, w, h, size=14, bold=False, color=None, align=1):
        """Draw text. align: 0=left, 1=center, 2=right."""
        if color is None:
            color = make_color_t(COLOR_TEXT)
        font = NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size)
        style = NSMutableParagraphStyle.alloc().init()
        style.setAlignment_(align)
        attrs = {
            NSForegroundColorAttributeName: color,
            NSFontAttributeName: font,
            NSParagraphStyleAttributeName: style,
        }
        astr = NSAttributedString.alloc().initWithString_attributes_(text, attrs)
        astr.drawInRect_(NSMakeRect(x, y, w, h))

    # -- Button flash animation --

    def flash_button(self, btn_name):
        self.activeButton = btn_name
        self.setNeedsDisplay_(True)
        if self.flashTimer:
            self.flashTimer.invalidate()
        self.flashTimer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.25, self, "clearFlash:", None, False)

    def clearFlash_(self, timer):
        self.activeButton = ""
        self.setNeedsDisplay_(True)

    def update_status(self, text, connected=False):
        self.statusText = text
        self.statusConnected = connected
        self.setNeedsDisplay_(True)

    # -- Dragging support --

    def mouseDown_(self, event):
        # Activate the app so local key monitor works
        NSApp.activateIgnoringOtherApps_(True)
        self.window().makeKeyWindow()
        self.dragOrigin = event.locationInWindow()

    def mouseDragged_(self, event):
        if self.dragOrigin is None:
            return
        window = self.window()
        screen_loc = event.locationInWindow()
        current_frame = window.frame()
        dx = screen_loc.x - self.dragOrigin.x
        dy = screen_loc.y - self.dragOrigin.y
        new_origin = (current_frame.origin.x + dx, current_frame.origin.y + dy)
        window.setFrameOrigin_(new_origin)


class AppDelegate(NSObject):
    def init(self):
        self = objc.super(AppDelegate, self).init()
        self.ws = None
        self.input_ws = None
        self.panel = None
        self.remote_view = None
        self.reconnecting = False
        self.keepalive_active = False
        return self

    def applicationDidFinishLaunching_(self, notification):
        # Titled + NonactivatingPanel required for policy 2 visibility
        # (Borderless + policy 2 = invisible window)
        style = (
            AppKit.NSWindowStyleMaskTitled
            | NSWindowStyleMaskNonactivatingPanel
        )
        self.panel = RemotePanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WINDOW_WIDTH, WINDOW_HEIGHT),
            style,
            NSBackingStoreBuffered,
            False,
        )
        self.panel.setTitle_("")
        self.panel.setTitlebarAppearsTransparent_(True)
        self.panel.setTitleVisibility_(1)  # hidden
        # Hide traffic-light buttons (they don't work in accessory mode anyway)
        for btn_type in [AppKit.NSWindowCloseButton, AppKit.NSWindowMiniaturizeButton, AppKit.NSWindowZoomButton]:
            btn = self.panel.standardWindowButton_(btn_type)
            if btn:
                btn.setHidden_(True)
        self.panel.setLevel_(NSFloatingWindowLevel)
        self.panel.setAlphaValue_(0.95)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setHasShadow_(True)
        self.panel.setMovableByWindowBackground_(True)

        # Position top-right
        screen = AppKit.NSScreen.mainScreen().frame()
        x = screen.size.width - WINDOW_WIDTH - 40
        y = screen.size.height - WINDOW_HEIGHT - 80
        self.panel.setFrameOrigin_((x, y))

        # Content view
        self.remote_view = RemoteView.alloc().initWithFrame_(
            NSMakeRect(0, 0, WINDOW_WIDTH, WINDOW_HEIGHT)
        )
        self.panel.setContentView_(self.remote_view)
        self.panel.setDelegate_(self)
        self.panel.makeKeyAndOrderFront_(None)

        # Local key monitor ONLY — keys only captured when remote is focused
        NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            NSKeyDownMask, self.handle_key_event
        )

        # Connect in background
        threading.Thread(target=self.connect_tv, daemon=True).start()

    def connect_tv(self):
        """Connect to TV (runs in background thread). Auto-retries on failure."""
        # Exponential backoff: 10s, 30s, 60s — TV rate-limits per-IP
        delays = [10, 30, 60]
        for attempt in range(len(delays) + 1):
            try:
                if self.ws:
                    self.ws.close()
                self.ws = WebSocketClient(TV_HOST, TV_PORT)
                self.ws.connect()
                authenticate(self.ws)
                url = get_input_socket_url(self.ws)
                self.input_ws = WebSocketInputClient(url)
                self.input_ws.connect()
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "onConnected:", None, False)
                self.start_keepalive()
                return
            except Exception as e:
                msg = str(e)
                # Friendly messages for common errors
                if "Expecting value" in msg or "JSONDecode" in msg:
                    msg = "TV sent empty response"
                elif "no key received" in msg:
                    msg = "Auth failed"
                elif "Try Again" in msg.lower():
                    msg = "TV rate-limited"

                if attempt < len(delays):
                    wait = delays[attempt]
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        "onConnectionError:", f"{msg} — retry in {wait}s", False)
                    time.sleep(wait)
                else:
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        "onConnectionError:", f"{msg} — toggle off/on TV", False)

    def start_keepalive(self):
        """Start background keepalive pings every 15s on both sockets."""
        self.keepalive_active = True
        threading.Thread(target=self.keepalive_loop, daemon=True).start()

    def keepalive_loop(self):
        """Ping both sockets periodically. Triggers reconnect on failure."""
        while self.keepalive_active:
            time.sleep(15)
            if not self.keepalive_active:
                break
            try:
                if self.ws:
                    self.ws.send_ping()
                if self.input_ws:
                    self.input_ws.send_ping()
            except Exception:
                self.keepalive_active = False
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "onConnectionError:", "Connection lost", False)
                threading.Thread(target=self.reconnect_tv, daemon=True).start()
                return

    def reconnect_tv(self):
        """Auto-reconnect after connection loss."""
        if self.reconnecting:
            return
        self.reconnecting = True
        self.keepalive_active = False
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "onConnectionError:", "Reconnecting...", False)
        if self.input_ws:
            self.input_ws.close()
            self.input_ws = None
        if self.ws:
            self.ws.close()
            self.ws = None
        time.sleep(3)
        self.reconnecting = False
        self.connect_tv()

    def onConnected_(self, _):
        self.remote_view.update_status("Connected", True)

    def onConnectionError_(self, err):
        msg = str(err) if err else "Unknown error"
        if len(msg) > 35:
            msg = msg[:32] + "..."
        self.remote_view.update_status(msg, False)

    def handle_key_event(self, event):
        """Local monitor — only fires when remote window is focused."""
        kc = event.keyCode()

        if kc == 53 or kc == 12:  # Escape or Q = quit
            os._exit(0)
            return None

        btn = KEYCODE_MAP.get(kc)
        if btn and self.input_ws:
            try:
                self.input_ws.send_button(btn)
                self.remote_view.flash_button(btn)
            except Exception:
                self.remote_view.update_status("Connection lost", False)
                threading.Thread(target=self.reconnect_tv, daemon=True).start()
            return None

        return event

    # Window close button (red X) should quit the app
    def windowWillClose_(self, notification):
        NSApp.terminate_(None)

    def applicationShouldTerminateAfterLastWindowClosed_(self, app):
        return True

    def applicationWillTerminate_(self, notification):
        if self.input_ws:
            self.input_ws.close()
        if self.ws:
            self.ws.close()


def main():
    app = NSApplication.sharedApplication()
    # Titled + NonactivatingPanel + policy 2 = visible window, no dock icon
    app.setActivationPolicy_(2)

    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    app.activateIgnoringOtherApps_(True)
    app.run()


if __name__ == "__main__":
    main()
