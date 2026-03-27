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
import Quartz

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
    launch_app,
    TV_HOST,
    TV_PORT,
)

# App icon filenames (128x128 PNGs in icons/ directory)
APP_ICON_FILES = {
    "youtube": "youtube.png",
    "plex": "plex.png",
    "appletv": "appletv.png",
    "paramount": "paramount.png",
}

# App shortcuts — (label, app_id, icon_key)
APP_SHORTCUTS = [
    ("YT",   "youtube.leanback.v4",            "youtube"),
    ("Plex", "cdp-30",                          "plex"),
    ("",     "com.apple.appletv",               "appletv"),
    ("P+",   "com.cbs-all-access.webapp.prod",  "paramount"),
]

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
WINDOW_HEIGHT = 575

# -- Color palette (Neon Accent theme) --
COLOR_BODY_BG = (0.05, 0.05, 0.08, 0.95)
COLOR_BODY_BORDER = (0.10, 0.10, 0.18, 0.5)
COLOR_BTN_NORMAL = (0.075, 0.075, 0.16, 0.9)
COLOR_BTN_HOVER = (0.33, 0.40, 1.0, 0.85)
COLOR_BTN_BORDER = (0.16, 0.16, 0.29, 0.5)
COLOR_TEXT = (0.53, 0.53, 0.80, 1.0)
COLOR_TEXT_DIM = (0.33, 0.33, 0.50, 0.7)
COLOR_ACCENT_BLUE = (0.33, 0.40, 1.0, 1.0)
COLOR_ACCENT_RED = (1.0, 0.20, 0.33, 1.0)
COLOR_POWER_BG = (0.10, 0.04, 0.04, 0.9)
COLOR_CONNECTED = (0.0, 1.0, 0.53, 0.9)
COLOR_DISCONNECTED = (1.0, 0.20, 0.33, 0.9)
COLOR_DPAD_BG = (0.067, 0.067, 0.13, 0.9)
COLOR_DPAD_SEGMENT = (0.10, 0.10, 0.20, 0.9)
COLOR_DPAD_ACTIVE = (0.33, 0.40, 1.0, 0.85)
COLOR_OK_BG = (0.10, 0.10, 0.21, 0.95)
COLOR_VOL_BG = (0.067, 0.067, 0.15, 0.9)
COLOR_VOL_DIVIDER = (0.16, 0.16, 0.29, 0.5)
COLOR_TRACKPAD_BG = (0.055, 0.055, 0.12, 0.9)
COLOR_TRACKPAD_BORDER = (0.16, 0.16, 0.29, 0.6)
COLOR_TRACKPAD_ACTIVE = (0.15, 0.20, 0.50, 0.4)

# Trackpad zone geometry
TRACKPAD_X = 20
TRACKPAD_Y = 420
TRACKPAD_W = WINDOW_WIDTH - 40
TRACKPAD_H = 110


class Sensitivity:
    """Accumulates fractional deltas so small movements aren't lost to int truncation."""
    def __init__(self, multiplier=2.0):
        self.multiplier = multiplier
        self.accum_x = 0.0
        self.accum_y = 0.0

    def apply(self, raw_dx, raw_dy):
        self.accum_x += raw_dx * self.multiplier
        self.accum_y += raw_dy * self.multiplier
        dx = int(self.accum_x)
        dy = int(self.accum_y)
        self.accum_x -= dx
        self.accum_y -= dy
        return dx, dy

    def reset(self):
        self.accum_x = 0.0
        self.accum_y = 0.0


pointer_sensitivity = Sensitivity(multiplier=2.0)


def make_color(r, g, b, a=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)


def make_color_t(tup):
    return make_color(*tup)


def load_icon_images():
    """Load PNG icon files from the icons/ directory into NSImage objects."""
    from AppKit import NSImage
    icons_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
    icons = {}
    for name, filename in APP_ICON_FILES.items():
        path = os.path.join(icons_dir, filename)
        if os.path.exists(path):
            img = NSImage.alloc().initWithContentsOfFile_(path)
            if img:
                icons[name] = img
    return icons


# Pre-load icons at import time
APP_ICONS = load_icon_images()


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
        self.trackpadSelected = False  # Modal: True when trackpad mode is active
        self.onPointerMove = None      # callback(dx, dy)
        self.onPointerClick = None     # callback()
        self.onPointerScroll = None    # callback(dx, dy)
        self.onTrackpadToggle = None   # callback(bool) — notify delegate of mode change
        self.onAppLaunch = None        # callback(app_id)
        self.onButtonPress = None      # callback(btn_name) — send button to TV
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
        self.draw_app_shortcuts()
        self.draw_volume_rocker()
        self.draw_power_button()
        self.draw_trackpad_zone()
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
                        size=16, bold=True, color=make_color(1, 1, 1, 1))

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

        # Draw highlight for active d-pad direction using simple clip rects
        active_dir = self.activeButton
        if active_dir in ("UP", "DOWN", "LEFT", "RIGHT"):
            AppKit.NSGraphicsContext.currentContext().saveGraphicsState()
            # Clip to d-pad ring (between inner and outer circles)
            ring_path = NSBezierPath.bezierPathWithOvalInRect_(outer_rect)
            inner_clip = NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(cx - inner_r - 2, cy - inner_r - 2,
                           (inner_r + 2) * 2, (inner_r + 2) * 2))
            ring_path.appendBezierPath_(inner_clip.bezierPathByReversingPath())
            ring_path.addClip()
            # Clip to the correct quadrant
            if active_dir == "UP":
                clip_rect = NSMakeRect(cx - outer_r, cy - outer_r, outer_r * 2, outer_r)
            elif active_dir == "DOWN":
                clip_rect = NSMakeRect(cx - outer_r, cy, outer_r * 2, outer_r)
            elif active_dir == "LEFT":
                clip_rect = NSMakeRect(cx - outer_r, cy - outer_r, outer_r, outer_r * 2)
            else:  # RIGHT
                clip_rect = NSMakeRect(cx, cy - outer_r, outer_r, outer_r * 2)
            NSBezierPath.clipRect_(clip_rect)
            make_color_t(COLOR_DPAD_ACTIVE).set()
            NSBezierPath.fillRect_(NSMakeRect(cx - outer_r, cy - outer_r,
                                              outer_r * 2, outer_r * 2))
            AppKit.NSGraphicsContext.currentContext().restoreGraphicsState()

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
            tc = make_color(1, 1, 1, 1) if is_active else make_color_t(COLOR_ACCENT_BLUE)
            self.draw_label(symbol, ax - 10, ay - 8, 20, 16, size=12, color=tc)

        # OK button in center — bright blue neon ring
        ok_active = self.activeButton == "ENTER"
        ok_rect = NSMakeRect(cx - inner_r, cy - inner_r, inner_r * 2, inner_r * 2)
        ok_path = NSBezierPath.bezierPathWithOvalInRect_(ok_rect)
        if ok_active:
            make_color_t(COLOR_DPAD_ACTIVE).set()
        else:
            make_color_t(COLOR_OK_BG).set()
        ok_path.fill()
        make_color_t(COLOR_ACCENT_BLUE).set()
        ok_path.setLineWidth_(2.0)
        ok_path.stroke()

        tc = make_color(1, 1, 1, 1) if ok_active else make_color_t(COLOR_ACCENT_BLUE)
        self.draw_label("OK", cx - 15, cy - 8, 30, 16, size=13, bold=True, color=tc)


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

    # -- App shortcut buttons --

    APP_ROW_Y = 262
    APP_BTN_SIZE = 38
    APP_BTN_GAP = 10

    def _app_layout(self):
        """Return list of (x, y, app_tuple) for each app button."""
        n = len(APP_SHORTCUTS)
        s = self.APP_BTN_SIZE
        gap = self.APP_BTN_GAP
        total_w = n * s + (n - 1) * gap
        start_x = (WINDOW_WIDTH - total_w) / 2
        result = []
        for i, app in enumerate(APP_SHORTCUTS):
            x = start_x + i * (s + gap)
            result.append((x, self.APP_ROW_Y, app))
        return result

    def draw_app_shortcuts(self):
        s = self.APP_BTN_SIZE

        for x, y, (label, app_id, icon_key) in self._app_layout():
            is_active = self.activeButton == f"APP:{app_id}"
            rect = NSMakeRect(x, y, s, s)

            # Draw the icon image (rounded via clipping, with padding)
            icon_img = APP_ICONS.get(icon_key)
            if icon_img:
                pad = 4
                icon_rect = NSMakeRect(x + pad, y + pad, s - 2 * pad, s - 2 * pad)
                AppKit.NSGraphicsContext.currentContext().saveGraphicsState()
                clip_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(icon_rect, 8, 8)
                clip_path.addClip()
                # Flip vertically to correct upside-down rendering
                xform = AppKit.NSAffineTransform.transform()
                xform.translateXBy_yBy_(0, y + pad + (s - 2 * pad))
                xform.scaleXBy_yBy_(1.0, -1.0)
                xform.translateXBy_yBy_(0, -(y + pad))
                xform.concat()
                icon_img.drawInRect_fromRect_operation_fraction_(
                    icon_rect, ((0, 0), icon_img.size()), AppKit.NSCompositeSourceOver, 1.0)
                AppKit.NSGraphicsContext.currentContext().restoreGraphicsState()

            # Active overlay
            if is_active:
                path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, 10, 10)
                make_color(1, 1, 1, 0.3).set()
                path.fill()

    def _app_button_hit(self, event):
        """Return the app_id if click is on an app button, else None."""
        loc = self.convertPoint_fromView_(event.locationInWindow(), None)
        s = self.APP_BTN_SIZE
        for x, y, (label, app_id, icon_key) in self._app_layout():
            if x <= loc.x <= x + s and y <= loc.y <= y + s:
                return app_id
        return None

    # -- Volume rocker (tall pill with +/- sections) --

    def draw_volume_rocker(self):
        vx = 30
        vy = 310
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
        py = 335
        pw, ph = 50, 50

        is_active = self.activeButton == "POWER"
        power_rect = NSMakeRect(px, py, pw, ph)
        power_path = NSBezierPath.bezierPathWithOvalInRect_(power_rect)

        if is_active:
            make_color(0.95, 0.2, 0.2, 0.9).set()
        else:
            make_color_t(COLOR_POWER_BG).set()
        power_path.fill()

        make_color_t(COLOR_ACCENT_RED).set()
        power_path.setLineWidth_(2.0)
        power_path.stroke()

        tc = make_color(1, 1, 1, 1) if is_active else make_color_t(COLOR_ACCENT_RED)
        self.draw_label("\u23FB", px, py + 14, pw, 22, size=18, bold=True, color=tc)

    # -- Trackpad zone --

    def draw_trackpad_zone(self):
        rect = NSMakeRect(TRACKPAD_X, TRACKPAD_Y, TRACKPAD_W, TRACKPAD_H)
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, 12, 12)

        if self.trackpadSelected:
            make_color_t(COLOR_TRACKPAD_ACTIVE).set()
        else:
            make_color_t(COLOR_TRACKPAD_BG).set()
        path.fill()

        # Highlighted border when selected
        if self.trackpadSelected:
            make_color_t(COLOR_ACCENT_BLUE).set()
            path.setLineWidth_(2.0)
        else:
            make_color_t(COLOR_TRACKPAD_BORDER).set()
            path.setLineWidth_(1.0)
        path.stroke()

        if self.trackpadSelected:
            self.draw_label("Pointer Active", TRACKPAD_X, TRACKPAD_Y + 4, TRACKPAD_W, 14,
                            size=9, bold=True, color=make_color_t(COLOR_ACCENT_BLUE))
            self.draw_label("ESC to exit", TRACKPAD_X, TRACKPAD_Y + TRACKPAD_H - 18,
                            TRACKPAD_W, 14, size=8, color=make_color_t(COLOR_TEXT_DIM))
            # Pointer icon
            self.draw_label("\u25C7", TRACKPAD_X, TRACKPAD_Y + TRACKPAD_H / 2 - 12,
                            TRACKPAD_W, 20, size=18, color=make_color_t(COLOR_ACCENT_BLUE))
        else:
            self.draw_label("Trackpad", TRACKPAD_X, TRACKPAD_Y + 4, TRACKPAD_W, 14,
                            size=9, color=make_color_t(COLOR_TEXT_DIM))
            self.draw_label("\u25C7", TRACKPAD_X, TRACKPAD_Y + TRACKPAD_H / 2 - 12,
                            TRACKPAD_W, 20, size=18, color=make_color_t(COLOR_TEXT_DIM))
            self.draw_label("Click to select", TRACKPAD_X, TRACKPAD_Y + TRACKPAD_H - 18,
                            TRACKPAD_W, 14, size=8, color=make_color_t(COLOR_TEXT_DIM))

    # -- Footer --

    def draw_footer(self):
        if self.trackpadSelected:
            self.draw_label("Move to aim · Click to select · Esc exits",
                            0, WINDOW_HEIGHT - 28, WINDOW_WIDTH, 16,
                            size=8, color=make_color_t(COLOR_ACCENT_BLUE))
        else:
            self.draw_label("Q/Esc close · Click trackpad for pointer",
                            0, WINDOW_HEIGHT - 28, WINDOW_WIDTH, 16,
                            size=8, color=make_color_t(COLOR_TEXT_DIM))

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

    # -- Mouse / trackpad support --

    def _point_in_trackpad(self, event):
        """Check if an event location (in flipped view coords) is in the trackpad zone."""
        loc = self.convertPoint_fromView_(event.locationInWindow(), None)
        return (TRACKPAD_X <= loc.x <= TRACKPAD_X + TRACKPAD_W and
                TRACKPAD_Y <= loc.y <= TRACKPAD_Y + TRACKPAD_H)

    def enterTrackpadMode(self):
        """Enter modal trackpad pointer mode."""
        self.trackpadSelected = True
        if self.onTrackpadToggle:
            self.onTrackpadToggle(True)
        self.setNeedsDisplay_(True)

    def exitTrackpadMode(self):
        """Exit modal trackpad pointer mode."""
        self.trackpadSelected = False
        if self.onTrackpadToggle:
            self.onTrackpadToggle(False)
        self.setNeedsDisplay_(True)

    def _button_hit(self, event):
        """Return button ID if click lands on a clickable element, else None."""
        loc = self.convertPoint_fromView_(event.locationInWindow(), None)
        x, y = loc.x, loc.y

        # D-pad (circular)
        cx, cy = WINDOW_WIDTH / 2, 120
        outer_r, inner_r = 60, 24
        dist = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
        if dist <= outer_r:
            if dist <= inner_r:
                return "ENTER"
            # Determine direction by angle
            angle = math.degrees(math.atan2(y - cy, x - cx))
            if -45 <= angle < 45:
                return "RIGHT"
            elif 45 <= angle < 135:
                return "DOWN"
            elif angle >= 135 or angle < -135:
                return "LEFT"
            else:
                return "UP"

        # Nav buttons (Menu, Back, Home, Info)
        nav_y = 195
        btn_w, btn_h, gap = 80, 28, 10
        col1_x = WINDOW_WIDTH / 2 - btn_w - gap / 2
        col2_x = WINDOW_WIDTH / 2 + gap / 2
        nav_buttons = [
            ("MENU", col1_x, nav_y),
            ("BACK", col2_x, nav_y),
            ("HOME", col1_x, nav_y + btn_h + 8),
            ("INFO", col2_x, nav_y + btn_h + 8),
        ]
        for btn_id, bx, by in nav_buttons:
            if bx <= x <= bx + btn_w and by <= y <= by + btn_h:
                return btn_id

        # Volume rocker
        vx, vy, vw, vh = 30, 310, 50, 70
        if vx <= x <= vx + vw and vy <= y <= vy + vh:
            if y < vy + vh / 2:
                return "VOLUMEUP"
            else:
                return "VOLUMEDOWN"

        # Mute button
        mute_y = vy + vh + 10
        if vx - 2 <= x <= vx + vw + 2 and mute_y <= y <= mute_y + 26:
            return "MUTE"

        # Power button
        px, py, pw = WINDOW_WIDTH - 78, 335, 50
        pcx, pcy = px + pw / 2, py + pw / 2
        if math.sqrt((x - pcx) ** 2 + (y - pcy) ** 2) <= pw / 2:
            return "POWER"

        return None

    def mouseDown_(self, event):
        # Activate the app so local key monitor works
        NSApp.activateIgnoringOtherApps_(True)
        self.window().makeKeyWindow()

        if self.trackpadSelected:
            # While in trackpad mode, click = TV click
            if self.onPointerClick:
                self.onPointerClick()
            return

        # Check app shortcut buttons
        app_id = self._app_button_hit(event)
        if app_id:
            if self.onAppLaunch:
                self.onAppLaunch(app_id)
            self.flash_button(f"APP:{app_id}")
            return

        if self._point_in_trackpad(event):
            # Click on trackpad zone → enter pointer mode
            self.enterTrackpadMode()
            return

        # Check all other buttons (d-pad, nav, volume, power)
        btn = self._button_hit(event)
        if btn and self.onButtonPress:
            self.onButtonPress(btn)
            self.flash_button(btn)
            return

        # Window drag mode
        self.dragOrigin = event.locationInWindow()

    def mouseDragged_(self, event):
        if self.trackpadSelected:
            # In trackpad mode, drags also move the pointer
            dx, dy = pointer_sensitivity.apply(event.deltaX(), event.deltaY())
            if self.onPointerMove and (dx or dy):
                self.onPointerMove(dx, dy)
            return

        # Window drag
        if self.dragOrigin is None:
            return
        window = self.window()
        screen_loc = event.locationInWindow()
        current_frame = window.frame()
        dx = screen_loc.x - self.dragOrigin.x
        dy = screen_loc.y - self.dragOrigin.y
        new_origin = (current_frame.origin.x + dx, current_frame.origin.y + dy)
        window.setFrameOrigin_(new_origin)

    def mouseUp_(self, event):
        if not self.trackpadSelected:
            self.dragOrigin = None


class AppDelegate(NSObject):
    def init(self):
        self = objc.super(AppDelegate, self).init()
        self.ws = None
        self.input_ws = None
        self.panel = None
        self.remote_view = None
        self.reconnecting = False
        self.keepalive_active = False
        self._tap = None
        self._tapSource = None
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

        # Wire trackpad mode toggle
        self.remote_view.onTrackpadToggle = self.handle_trackpad_toggle

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
        # Wire up trackpad pointer callbacks
        self.remote_view.onPointerMove = self.send_pointer_move
        self.remote_view.onPointerClick = self.send_pointer_click
        self.remote_view.onPointerScroll = self.send_pointer_scroll
        self.remote_view.onAppLaunch = self.send_app_launch
        self.remote_view.onButtonPress = self.send_button_press

    def send_button_press(self, btn_name):
        if self.input_ws:
            try:
                self.input_ws.send_button(btn_name)
            except Exception:
                self.remote_view.update_status("Connection lost", False)
                threading.Thread(target=self.reconnect_tv, daemon=True).start()

    def send_app_launch(self, app_id):
        if self.ws:
            try:
                launch_app(self.ws, app_id)
            except Exception:
                self.remote_view.update_status("Connection lost", False)
                threading.Thread(target=self.reconnect_tv, daemon=True).start()

    def send_pointer_move(self, dx, dy):
        if self.input_ws:
            try:
                self.input_ws.send_move(dx, dy)
            except Exception:
                self.remote_view.update_status("Connection lost", False)
                threading.Thread(target=self.reconnect_tv, daemon=True).start()

    def send_pointer_click(self):
        if self.input_ws:
            try:
                self.input_ws.send_click()
                self.remote_view.flash_button("CLICK")
            except Exception:
                self.remote_view.update_status("Connection lost", False)
                threading.Thread(target=self.reconnect_tv, daemon=True).start()

    def handle_trackpad_toggle(self, entering):
        """Called when trackpad mode is entered/exited."""
        if entering:
            # Reset accumulator and hide/freeze cursor
            pointer_sensitivity.reset()
            Quartz.CGAssociateMouseAndMouseCursorPosition(False)
            AppKit.NSCursor.hide()

            # Use a CGEventTap to capture raw mouse/scroll deltas — this is
            # reliable even with the cursor dissociated, unlike NSEvent monitors
            def tap_callback(_proxy, event_type, event, _refcon):
                if event_type == Quartz.kCGEventMouseMoved:
                    raw_dx = Quartz.CGEventGetDoubleValueField(event, Quartz.kCGMouseEventDeltaX)
                    raw_dy = Quartz.CGEventGetDoubleValueField(event, Quartz.kCGMouseEventDeltaY)
                    dx, dy = pointer_sensitivity.apply(raw_dx, raw_dy)
                    if dx or dy:
                        self.send_pointer_move(dx, dy)
                elif event_type == Quartz.kCGEventScrollWheel:
                    dx = int(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGScrollWheelEventPointDeltaAxis2))
                    dy = int(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGScrollWheelEventPointDeltaAxis1))
                    if dx or dy:
                        self.send_pointer_scroll(dx, dy)
                return event

            event_mask = (
                (1 << Quartz.kCGEventMouseMoved) |
                (1 << Quartz.kCGEventScrollWheel)
            )
            self._tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,
                Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionDefault,
                event_mask,
                tap_callback,
                None,
            )
            if self._tap:
                self._tapSource = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
                Quartz.CFRunLoopAddSource(
                    Quartz.CFRunLoopGetCurrent(),
                    self._tapSource,
                    Quartz.kCFRunLoopCommonModes,
                )
                Quartz.CGEventTapEnable(self._tap, True)
        else:
            # Tear down the event tap
            if hasattr(self, '_tap') and self._tap:
                Quartz.CGEventTapEnable(self._tap, False)
                Quartz.CFRunLoopRemoveSource(
                    Quartz.CFRunLoopGetCurrent(),
                    self._tapSource,
                    Quartz.kCFRunLoopCommonModes,
                )
                self._tap = None
                self._tapSource = None
            # Restore cursor
            Quartz.CGAssociateMouseAndMouseCursorPosition(True)
            AppKit.NSCursor.unhide()

    def send_pointer_scroll(self, dx, dy):
        if self.input_ws:
            try:
                self.input_ws.send_scroll(dx, dy)
            except Exception:
                self.remote_view.update_status("Connection lost", False)
                threading.Thread(target=self.reconnect_tv, daemon=True).start()

    def onConnectionError_(self, err):
        msg = str(err) if err else "Unknown error"
        if len(msg) > 35:
            msg = msg[:32] + "..."
        self.remote_view.update_status(msg, False)

    def handle_key_event(self, event):
        """Local monitor — only fires when remote window is focused."""
        kc = event.keyCode()

        if kc == 53:  # Escape
            if self.remote_view.trackpadSelected:
                # Exit trackpad mode, don't quit
                self.remote_view.exitTrackpadMode()
                return None
            else:
                os._exit(0)
                return None

        if kc == 12:  # Q = always quit
            os._exit(0)
            return None

        # In trackpad mode, spacebar = click, ignore other keys
        if self.remote_view.trackpadSelected:
            if kc == 49:  # Space
                self.send_pointer_click()
                return None
            return event

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
        # Restore cursor if still in trackpad mode
        if self.remote_view and self.remote_view.trackpadSelected:
            self.handle_trackpad_toggle(False)
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
