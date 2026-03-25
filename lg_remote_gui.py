#!/usr/bin/env python3
"""LG TV Remote — floating transparent GUI."""

import os
import sys
import json
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
    NSImage,
    NSImageView,
    NSTimer,
)
from Foundation import NSMakeRect, NSMakeSize, NSObject, NSDictionary
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

# Key code to TV button mapping
# macOS virtual key codes
KEYCODE_MAP = {
    126: "UP",       # up arrow
    125: "DOWN",     # down arrow
    123: "LEFT",     # left arrow
    124: "RIGHT",    # right arrow
    36: "ENTER",     # return
    51: "BACK",      # delete/backspace
    49: "MENU",      # space
    115: "HOME",     # home key
    53: None,        # escape = quit
    103: "VOLUMEUP",   # F11
    111: "VOLUMEDOWN", # F12
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

# Button display labels
LABEL_MAP = {
    "UP": "\u25B2", "DOWN": "\u25BC", "LEFT": "\u25C0", "RIGHT": "\u25B6",
    "ENTER": "OK", "BACK": "\u232B", "MENU": "\u2630", "HOME": "\u2302",
    "VOLUMEUP": "Vol+", "VOLUMEDOWN": "Vol-", "MUTE": "\U0001F507",
    "INFO": "i", "POWER": "\u23FB",
}

WINDOW_WIDTH = 280
WINDOW_HEIGHT = 340


class RemoteView(NSView):
    """Custom view for the remote control UI."""

    def initWithFrame_(self, frame):
        self = objc.super(RemoteView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._status = "Connecting..."
        self._last_button = ""
        self._flash_timer = None
        return self

    def drawRect_(self, rect):
        # Background with rounded corners
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            self.bounds(), 16, 16
        )
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.08, 0.08, 0.12, 0.92).set()
        path.fill()

        # Border
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.3, 0.3, 0.4, 0.6).set()
        path.setLineWidth_(1.5)
        path.stroke()

        # Title
        self._draw_text("LG Remote", 0, 295, WINDOW_WIDTH, 30,
                        size=18, bold=True,
                        color=NSColor.colorWithCalibratedRed_green_blue_alpha_(0.9, 0.4, 0.4, 1.0))

        # Status
        if self._status:
            sc = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.5, 0.8, 0.5, 0.8)
            if "error" in self._status.lower() or "fail" in self._status.lower():
                sc = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.9, 0.4, 0.4, 0.8)
            self._draw_text(self._status, 0, 272, WINDOW_WIDTH, 20,
                            size=11, color=sc)

        # D-pad area
        cx, cy = 90, 190
        self._draw_button("\u25B2", cx, cy + 45, 40, 35, "UP")
        self._draw_button("\u25BC", cx, cy - 45, 40, 35, "DOWN")
        self._draw_button("\u25C0", cx - 50, cy, 40, 35, "LEFT")
        self._draw_button("\u25B6", cx + 50, cy, 40, 35, "RIGHT")
        self._draw_button("OK", cx, cy, 44, 44, "ENTER", circle=True)

        # Right side controls
        rx = 210
        self._draw_button("\u232B", rx, cy + 45, 50, 30, "BACK")
        self._draw_button("\u2630", rx, cy, 50, 30, "MENU")
        self._draw_button("\u2302", rx, cy - 45, 50, 30, "HOME")

        # Bottom row - volume
        by = 90
        self._draw_button("Vol-", 40, by, 55, 28, "VOLUMEDOWN")
        self._draw_button("Mute", 115, by, 55, 28, "MUTE")
        self._draw_button("Vol+", 190, by, 55, 28, "VOLUMEUP")

        # Power
        self._draw_button("\u23FB", 210, by - 40, 50, 28, "POWER")

        # Help text
        self._draw_text("Esc to close", 0, 15, WINDOW_WIDTH, 16,
                        size=10, color=NSColor.colorWithCalibratedRed_green_blue_alpha_(0.4, 0.4, 0.5, 0.7))

        # Last button flash
        if self._last_button:
            self._draw_text(">> " + self._last_button, 0, 38, WINDOW_WIDTH, 20,
                            size=12, bold=True,
                            color=NSColor.colorWithCalibratedRed_green_blue_alpha_(0.3, 0.8, 0.3, 0.9))

    def _draw_button(self, label, x, y, w, h, btn_id, circle=False):
        is_active = self._last_button == btn_id
        if circle:
            path = NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(x - w/2, y - h/2, w, h))
        else:
            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(x - w/2, y - h/2, w, h), 6, 6)

        if is_active:
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.3, 0.5, 0.9, 0.7).set()
        else:
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.15, 0.15, 0.22, 0.8).set()
        path.fill()

        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.35, 0.35, 0.45, 0.6).set()
        path.setLineWidth_(1.0)
        path.stroke()

        tc = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.85, 0.85, 0.9, 1.0)
        if is_active:
            tc = NSColor.whiteColor()
        self._draw_text(label, x - w/2, y - 8, w, 20, size=13, bold=is_active, color=tc)

    def _draw_text(self, text, x, y, w, h, size=14, bold=False, color=None):
        if color is None:
            color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.8, 0.8, 0.85, 1.0)
        font = NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size)
        style = NSMutableParagraphStyle.alloc().init()
        style.setAlignment_(1)  # center
        attrs = {
            NSForegroundColorAttributeName: color,
            NSFontAttributeName: font,
            NSParagraphStyleAttributeName: style,
        }
        astr = NSAttributedString.alloc().initWithString_attributes_(text, attrs)
        astr.drawInRect_(NSMakeRect(x, y, w, h))

    def flash_button(self, btn_name):
        self._last_button = btn_name
        self.setNeedsDisplay_(True)
        if self._flash_timer:
            self._flash_timer.invalidate()
        self._flash_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.3, self, "clearFlash:", None, False)

    def clearFlash_(self, timer):
        self._last_button = ""
        self.setNeedsDisplay_(True)

    def set_status(self, text):
        self._status = text
        self.setNeedsDisplay_(True)


class AppDelegate(NSObject):
    def init(self):
        self = objc.super(AppDelegate, self).init()
        self.ws = None
        self.input_ws = None
        self.panel = None
        self.remote_view = None
        return self

    def applicationDidFinishLaunching_(self, notification):
        # Create floating panel
        style = (
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskNonactivatingPanel
        )
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WINDOW_WIDTH, WINDOW_HEIGHT),
            style,
            NSBackingStoreBuffered,
            False,
        )
        self.panel.setTitle_("")
        self.panel.setTitlebarAppearsTransparent_(True)
        self.panel.setTitleVisibility_(1)  # hidden
        self.panel.setLevel_(NSFloatingWindowLevel)
        self.panel.setAlphaValue_(0.92)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setMovableByWindowBackground_(True)
        self.panel.setHasShadow_(True)

        # Position in top-right corner
        screen = AppKit.NSScreen.mainScreen().frame()
        x = screen.size.width - WINDOW_WIDTH - 30
        y = screen.size.height - WINDOW_HEIGHT - 80
        self.panel.setFrameOrigin_((x, y))

        # Custom view
        self.remote_view = RemoteView.alloc().initWithFrame_(
            NSMakeRect(0, 0, WINDOW_WIDTH, WINDOW_HEIGHT)
        )
        self.panel.setContentView_(self.remote_view)
        self.panel.makeKeyAndOrderFront_(None)

        # Key event monitor
        NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            NSKeyDownMask, self.handleKeyEvent_
        )

        # Connect in background
        threading.Thread(target=self.connect_tv, daemon=True).start()

    def connect_tv(self):
        try:
            self.ws = WebSocketClient(TV_HOST, TV_PORT)
            self.ws.connect()
            authenticate(self.ws)
            url = get_input_socket_url(self.ws)
            self.input_ws = WebSocketInputClient(url)
            self.input_ws.connect()
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "setConnected:", None, False)
        except Exception as e:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "setError:", str(e), False)

    def setConnected_(self, _):
        self.remote_view.set_status("Connected")

    def setError_(self, err):
        self.remote_view.set_status(f"Error: {err}")

    def handleKeyEvent_(self, event):
        kc = event.keyCode()

        # Escape = quit
        if kc == 53:
            NSApp.terminate_(None)
            return None

        btn = KEYCODE_MAP.get(kc)
        if btn and self.input_ws:
            try:
                self.input_ws.send_button(btn)
                self.remote_view.flash_button(btn)
            except Exception:
                self.remote_view.set_status("Connection lost")
                threading.Thread(target=self.connect_tv, daemon=True).start()
            return None

        return event

    def applicationWillTerminate_(self, notification):
        if self.input_ws:
            self.input_ws.close()
        if self.ws:
            self.ws.close()


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(0)  # regular app (shows in dock briefly)

    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    app.activateIgnoringOtherApps_(True)
    app.run()


if __name__ == "__main__":
    main()
