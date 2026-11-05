"""
CharaChorder Duo (Windows)

Two-mice helper: one device drives the system cursor; the other can move a
separate on-screen cursor (split) or contribute to merged movement. Raw Input
separates relative motion by device.

Modes
    split/merge — split shows a 2nd cursor overlay (UpdateLayeredWindow,
    click-through). Merged keeps one Windows cursor with per-device move scaling.
    The 2nd cursor follows the secondary device immediately. Per profile,
    optional ring shockwaves (checkbox “ring animations” under Change application,
    on by default) play when entering split, merging, pinning 1st/2nd, or
    warping to a pin in merged mode.

Profiles
    Four presets: merged, split, merged shift, split shift. Each stores its own
    move %, scroll/zoom/volume/tab strength %, and gesture mappings. The profile combo
    and the split/merge + duo shift checkboxes stay in sync.

Gestures
    When two mice are configured, coordinated motion (e.g. both moving north)
    can trigger actions.     Row labels list the secondary direction first, then the
    primary (North-South = secondary north while primary moves south). Sixteen
    cardinal pairs are listed (same-axis and orthogonal mixes); extras default
    to (none) until mapped. Each row
    maps to vertical wheel, horizontal wheel, Ctrl+wheel zoom, volume keys,
    Ctrl+Tab / Ctrl+Shift+Tab, Win+Tab, Win+D (show desktop), split/merge, profile
    slot, warps, or (none).     Warp / pin model: two invisible saved points — “3rd cursor” (pin 2nd / warp 2nd
    in merged) and “4th cursor” (pin 1st / warp 1st in merged). Pin 2nd stores the
    3rd at the 2nd cursor (split) or at the merged cursor (merged). Pin 1st stores
    the 4th at the primary (split) or merged cursor (merged). Warp 1st in split moves
    both logical cursors to the primary and merges; Warp 2nd in split moves to the
    2nd cursor and merges. In merged mode, Warp 1st / 2nd jump the cursor to the
    4th / 3rd saved point respectively.
    Gestures work in merged or split mode. Merged / merged-shift keep North–South
    scroll and split-toggle on the first rows and set compass pairs (e.g. East–North
    volume up, West–South show desktop). Split defaults East–East / West–West to
    warp 1st / warp 2nd; split shift defaults all rows to (none).

Sensitivity
    Per profile: primary vs 2nd-device move % (merged and merged-shift profiles
    default the 2nd device to 50%; split profiles default both to 100%), plus
    separate strength % for vertical scroll, horizontal scroll, zoom, volume (40
    default), tab (40 default), and change-application (also scales Win+D
    show-desktop gestures).
    Gesture axis tightness (%) controls how closely each mouse’s motion must
    align with N/S vs E/W before dual-mice gestures can arm (150 default, 600 on
    the split profile only; higher = stricter). The same ratio applies to orthogonal
    compass pairs (each mouse’s stroke must be axis-pure enough). The gesture field allows up to
    a very large % so the maximum can make dual-mice gestures nearly impossible
    to fire; a 1-device-pixel cross-axis floor applies so “pure” dx=0 / dy=0
    shortcuts cannot bypass strict aim. After a profile switch, dual-mice
    gestures can be suppressed for a configurable time (milliseconds; 0 = no
    suppress, default 5); pending gesture accumulators are still cleared on switch.

Top toggles
    split/merge and duo shift are checkboxes. Standard push buttons below them
    include merge/warp, pin, 2nd left/right/middle, and placeholder.
    “2nd Left/Right/Middle Click” share the same rules: in merged / merged-shift a
    click at the system cursor; in split / split-shift warp to the ghost, press the
    chosen button, track with the secondary mouse while held, release and restore
    the primary. Hold-to-drag in either mode uses injected move events (not only
    SetCursorPos) after the synthetic button down.
    “placeholder” remains a stub control.

    F15–F24 are handled via a system-wide low-level keyboard hook: they mirror the
    same ten controls even when this window is not focused (other apps will not
    receive those function keys while CC-Duo is running). F15/F16 toggle split/merge
    and duo shift; F17–F24 press the eight pushbuttons on key-down and release on
    key-up (pin actions on release, matching mouse-up BN_CLICKED). Minimizing or
    using only the taskbar is optional UX; global F-keys do not require it.

Limits
    Wheel and extra-button events from the 2nd device may still go to whatever
    is under the primary cursor; only movement is fully separated.

Run: python dual_mouse_hands.py — a notification-area (tray) icon stays running;
use the tray menu Exit or close from the console to quit. The main window hides
to the tray when minimized or closed (×); double-click the tray icon or choose
Open to show it again. 2nd-cursor size applies on leaving the size field; color
applies from the picker on OK.
"""

from __future__ import annotations

import copy
import ctypes
import math
import os
import re
import sys
import time
from ctypes import wintypes

# --- Win32 setup ---
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32

# Window message types: on Win64, wParam/lParam are pointer-wide; mismatched
# ctypes defaults cause "int too long to convert" in DefWindowProcW.
if ctypes.sizeof(ctypes.c_void_p) == 8:
    wintypes.WPARAM = ctypes.c_ulonglong
    wintypes.LPARAM = ctypes.c_longlong
if not hasattr(wintypes, "LRESULT"):
    wintypes.LRESULT = ctypes.c_ssize_t

# Per-monitor v2 DPI awareness (cursor coords match screen pixels)
try:
    user32.SetProcessDpiAwarenessContext(-4)  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
except OSError:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except OSError:
        pass

# --- Constants ---
WM_CREATE = 0x0001
WM_DESTROY = 0x0002
WM_ERASEBKGND = 0x0014
WM_PAINT = 0x000F
WM_NCPAINT = 0x0085
WM_INITDIALOG = 0x0110
WM_HSCROLL = 0x0114
WM_VSCROLL = 0x0115
WM_INPUT = 0x00FF
WM_COMMAND = 0x0111
WM_TIMER = 0x0113
WM_NCHITTEST = 0x0084
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CAPTURECHANGED = 0x0215
WM_CTLCOLORSTATIC = 0x0138
HTTRANSPARENT = -1
WM_APP = 0x8000
# Posted from ChooseColor hook so we read rgbResult after the dialog updates it.
WM_GHOST_COLOR_SYNC = WM_APP + 120
# Posted from WH_KEYBOARD_LL: wParam = VK, lParam 0 = down / 1 = up.
WM_CCOS_FKEY = WM_APP + 121
# Tray icon (Shell_NotifyIcon callback).
WM_TRAY_CALLBACK = WM_APP + 115

WH_KEYBOARD_LL = 13
HC_ACTION = 0
LLKHF_INJECTED = 0x10
LLKHF_UP = 0x80
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

BN_CLICKED = 0

WS_CHILD = 0x40000000
WS_TABSTOP = 0x00010000
BS_PUSHBUTTON = 0x00000000
BS_AUTOCHECKBOX = 0x00000003
BS_GROUPBOX = 0x00000007

BM_GETCHECK = 0x00F0
BM_SETCHECK = 0x00F1
BM_SETSTATE = 0x00F3
BM_CLICK = 0x00F5
BST_UNCHECKED = 0
BST_CHECKED = 1

VK_F15 = 0x7E
VK_F16 = 0x7F
VK_F17 = 0x80
VK_F18 = 0x81
VK_F19 = 0x82
VK_F20 = 0x83
VK_F21 = 0x84
VK_F22 = 0x85
VK_F23 = 0x86
VK_F24 = 0x87

IDC_TOGGLE_POINTERS = 1001
IDC_GHOST_SIZE_EDIT = 1002
IDC_GHOST_COLOR_BTN = 1003
IDC_GHOST_RGB_STATIC = 1004
IDC_SECONDARY_MOUSE_COMBO = 1005
IDC_DUO_SHIFT_TOGGLE = 1006
# Dual-mice gesture pattern → action (one combo per row)
IDC_GESTURE_SHEAR = 1010
IDC_GESTURE_REVERSE_SHEAR = 1011
IDC_GESTURE_PINCH = 1012
IDC_GESTURE_PULL = 1013
IDC_GESTURE_DOUBLE_UP = 1014
IDC_GESTURE_DOUBLE_DOWN = 1015
IDC_GESTURE_DOUBLE_LEFT = 1016
IDC_GESTURE_DOUBLE_RIGHT = 1017
# Orthogonal dual-mice pairs (secondary direction first, then primary); default (none).
IDC_GESTURE_NORTH_EAST = 1018
IDC_GESTURE_NORTH_WEST = 1019
IDC_SENS_MOVE_PRIMARY_EDIT = 1020
IDC_SENS_MOVE_SECONDARY_EDIT = 1021
IDC_GESTURE_SOUTH_EAST = 1022
IDC_GESTURE_SOUTH_WEST = 1023
IDC_SENS_SCROLL_EDIT = 1024
IDC_SENS_SCROLL_H_EDIT = 1025
IDC_SENS_ZOOM_EDIT = 1026
IDC_SENS_TAB_EDIT = 1027
IDC_SENS_CHANGE_APP_EDIT = 1028
IDC_PROFILE_COMBO = 1029
IDC_PROFILE_DIVIDER = 1030
IDC_MERGE_WARP_FIRST = 1031
IDC_MERGE_WARP_SECOND = 1032
IDC_PIN_FIRST = 1033
IDC_PIN_SECOND = 1034
IDC_SECOND_LEFT_CLICK = 1035
IDC_SECOND_RIGHT_CLICK = 1036
IDC_SECOND_MIDDLE_CLICK = 1037
IDC_ULTIMATE_TOGGLE = 1038
IDC_SENS_GESTURE_AIM_EDIT = 1039
IDC_GESTURE_EAST_NORTH = 1040
IDC_GESTURE_EAST_SOUTH = 1041
IDC_GESTURE_WEST_NORTH = 1042
IDC_GESTURE_WEST_SOUTH = 1043
IDC_SENS_PROFILE_GESTURE_COOLDOWN_EDIT = 1044
IDC_RING_ANIMATIONS_TOGGLE = 1045
IDC_SENS_VOLUME_EDIT = 1046

# CCOS Inputs group: F15–F24 when the main window or a child has focus.
CCOS_FKEY_TO_IDC: dict[int, int] = {
    VK_F15: IDC_TOGGLE_POINTERS,
    VK_F16: IDC_DUO_SHIFT_TOGGLE,
    VK_F17: IDC_MERGE_WARP_FIRST,
    VK_F18: IDC_MERGE_WARP_SECOND,
    VK_F19: IDC_PIN_FIRST,
    VK_F20: IDC_PIN_SECOND,
    VK_F21: IDC_SECOND_LEFT_CLICK,
    VK_F22: IDC_SECOND_RIGHT_CLICK,
    VK_F23: IDC_SECOND_MIDDLE_CLICK,
    VK_F24: IDC_ULTIMATE_TOGGLE,
}
CCOS_FKEY_CHECKBOX_IDCS: frozenset[int] = frozenset(
    (IDC_TOGGLE_POINTERS, IDC_DUO_SHIFT_TOGGLE)
)
# BS_PUSHBUTTON still uses WM_LBUTTONDOWN/UP for activation (including F-key synth).
CCOS_FKEY_SECOND_GHOST_BTN_IDCS: frozenset[int] = frozenset(
    (IDC_SECOND_LEFT_CLICK, IDC_SECOND_RIGHT_CLICK, IDC_SECOND_MIDDLE_CLICK)
)


def ccos_input_label(base: str, idc: int) -> str:
    """CCOS Inputs control caption with matching F-key hint, e.g. pin 1st (f19)."""
    for vk, mapped_idc in CCOS_FKEY_TO_IDC.items():
        if mapped_idc == idc:
            return f"{base} (f{vk - VK_F15 + 15})"
    return base


PLACEHOLDER_TOGGLE_IDCS: tuple[int, ...] = (IDC_ULTIMATE_TOGGLE,)
PLACEHOLDER_TOGGLE_INDEX: dict[int, int] = {
    idc: i for i, idc in enumerate(PLACEHOLDER_TOGGLE_IDCS)
}

DEFAULT_SENSITIVITY_PCT = 100
DEFAULT_MERGED_SECONDARY_PCT = 50
DEFAULT_SCROLL_PCT = 300
DEFAULT_TAB_PCT = 40
DEFAULT_VOLUME_PCT = 40
DEFAULT_CHANGE_APP_PCT = 50
DEFAULT_GESTURE_AIM_PCT = 150
MIN_SENSITIVITY_PCT = 1
MAX_SENSITIVITY_PCT = 500
# Gesture axis tightness: upper range much larger than other strength % fields.
MAX_GESTURE_AIM_PCT = 250_000
DEFAULT_PROFILE_GESTURE_COOLDOWN_MS = 5
MIN_PROFILE_GESTURE_COOLDOWN_MS = 0
MAX_PROFILE_GESTURE_COOLDOWN_MS = 10_000

# Gesture combo item indices (same list on every gesture dropdown):
# 1–2 vertical scroll, 3–4 horizontal wheel, 5–6 zoom, 7–8 volume, 9–10 tab, 11 Win+Tab,
# 12 toggle split/merge (profile + UI), 13–14 merge at primary / at 2nd cursor, 15 Win+D.
GESTURE_SCROLL_NORTH_ITEM = 1
GESTURE_SCROLL_SOUTH_ITEM = 2
GESTURE_SCROLL_WEST_ITEM = 3
GESTURE_SCROLL_EAST_ITEM = 4
GESTURE_ZOOM_IN_ITEM = 5
GESTURE_ZOOM_OUT_ITEM = 6
GESTURE_VOLUME_UP_ITEM = 7
GESTURE_VOLUME_DOWN_ITEM = 8
GESTURE_TAB_RIGHT_ITEM = 9
GESTURE_TAB_LEFT_ITEM = 10
GESTURE_CHANGE_APP_ITEM = 11
GESTURE_SPLIT_MERGE_ITEM = 12
GESTURE_WARP_FIRST_ITEM = 13
GESTURE_WARP_SECOND_ITEM = 14
GESTURE_SHOW_DESKTOP_ITEM = 15

ALL_GESTURE_IDCS: tuple[int, ...] = (
    IDC_GESTURE_SHEAR,
    IDC_GESTURE_REVERSE_SHEAR,
    IDC_GESTURE_PINCH,
    IDC_GESTURE_PULL,
    IDC_GESTURE_DOUBLE_UP,
    IDC_GESTURE_DOUBLE_DOWN,
    IDC_GESTURE_DOUBLE_LEFT,
    IDC_GESTURE_DOUBLE_RIGHT,
    IDC_GESTURE_NORTH_EAST,
    IDC_GESTURE_NORTH_WEST,
    IDC_GESTURE_SOUTH_EAST,
    IDC_GESTURE_SOUTH_WEST,
    IDC_GESTURE_EAST_NORTH,
    IDC_GESTURE_EAST_SOUTH,
    IDC_GESTURE_WEST_NORTH,
    IDC_GESTURE_WEST_SOUTH,
)

PROFILE_LABELS: tuple[str, ...] = (
    "merged",
    "split",
    "merged shift",
    "split shift",
)


def default_input_sensitivity_dict() -> dict[str, int]:
    return {
        "move_primary_pct": DEFAULT_SENSITIVITY_PCT,
        "move_secondary_pct": DEFAULT_SENSITIVITY_PCT,
        "scroll_pct": DEFAULT_SCROLL_PCT,
        "scroll_horizontal_pct": DEFAULT_SCROLL_PCT,
        "zoom_pct": DEFAULT_SCROLL_PCT,
        "volume_pct": DEFAULT_VOLUME_PCT,
        "tab_pct": DEFAULT_TAB_PCT,
        "change_app_pct": DEFAULT_CHANGE_APP_PCT,
        "gesture_aim_pct": DEFAULT_GESTURE_AIM_PCT,
        "profile_gesture_cooldown_ms": DEFAULT_PROFILE_GESTURE_COOLDOWN_MS,
    }


def default_input_sensitivity_for_profile_slot(slot: int) -> dict[str, int]:
    """Merged / merged-shift (slots 0, 2): 2nd-device move % defaults to 50; split slots stay 100.

    Split slot (1) uses tighter default gesture axis (600 vs 150).
    """
    d = default_input_sensitivity_dict()
    if slot in (0, 2):
        d["move_secondary_pct"] = DEFAULT_MERGED_SECONDARY_PCT
    if slot == 1:
        d["gesture_aim_pct"] = 600
    return d


def migrate_input_sensitivity_dict(d: dict[str, int]) -> None:
    """Fold legacy split/merged move keys into move_* (per-profile single row)."""
    if "move_primary_pct" in d:
        for old in (
            "split_primary_pct",
            "split_secondary_pct",
            "merged_primary_pct",
            "merged_secondary_pct",
        ):
            d.pop(old, None)
        d["gesture_aim_pct"] = clamp_gesture_aim_pct(
            int(d.get("gesture_aim_pct", DEFAULT_GESTURE_AIM_PCT))
        )
        d["profile_gesture_cooldown_ms"] = clamp_profile_gesture_cooldown_ms(
            int(
                d.get(
                    "profile_gesture_cooldown_ms",
                    DEFAULT_PROFILE_GESTURE_COOLDOWN_MS,
                )
            )
        )
        d.setdefault("volume_pct", DEFAULT_VOLUME_PCT)
        return
    mp = int(
        d.get(
            "split_primary_pct",
            d.get("merged_primary_pct", DEFAULT_SENSITIVITY_PCT),
        )
    )
    ms = int(
        d.get(
            "split_secondary_pct",
            d.get("merged_secondary_pct", DEFAULT_MERGED_SECONDARY_PCT),
        )
    )
    for old in (
        "split_primary_pct",
        "split_secondary_pct",
        "merged_primary_pct",
        "merged_secondary_pct",
    ):
        d.pop(old, None)
    d["move_primary_pct"] = mp
    d["move_secondary_pct"] = ms
    d["gesture_aim_pct"] = clamp_gesture_aim_pct(
        int(d.get("gesture_aim_pct", DEFAULT_GESTURE_AIM_PCT))
    )
    d["profile_gesture_cooldown_ms"] = clamp_profile_gesture_cooldown_ms(
        int(
            d.get(
                "profile_gesture_cooldown_ms",
                DEFAULT_PROFILE_GESTURE_COOLDOWN_MS,
            )
        )
    )
    d.setdefault("volume_pct", DEFAULT_VOLUME_PCT)


def default_gesture_selections() -> dict[int, int]:
    return {
        IDC_GESTURE_SHEAR: GESTURE_SPLIT_MERGE_ITEM,
        IDC_GESTURE_REVERSE_SHEAR: GESTURE_CHANGE_APP_ITEM,
        IDC_GESTURE_DOUBLE_UP: GESTURE_SCROLL_NORTH_ITEM,
        IDC_GESTURE_DOUBLE_DOWN: GESTURE_SCROLL_SOUTH_ITEM,
        IDC_GESTURE_DOUBLE_LEFT: GESTURE_TAB_LEFT_ITEM,
        IDC_GESTURE_DOUBLE_RIGHT: GESTURE_TAB_RIGHT_ITEM,
        IDC_GESTURE_PULL: GESTURE_ZOOM_IN_ITEM,
        IDC_GESTURE_PINCH: GESTURE_ZOOM_OUT_ITEM,
    }


def empty_gesture_selections() -> dict[int, int]:
    return {idc: 0 for idc in ALL_GESTURE_IDCS}


def split_profile_gesture_selections() -> dict[int, int]:
    """Split profile: East-East → merge at 1st, West-West → merge at 2nd; other rows (none)."""
    d = empty_gesture_selections()
    d[IDC_GESTURE_DOUBLE_RIGHT] = GESTURE_WARP_FIRST_ITEM
    d[IDC_GESTURE_DOUBLE_LEFT] = GESTURE_WARP_SECOND_ITEM
    return d


def merged_profile_gesture_selections() -> dict[int, int]:
    """Merged / merged-shift: compass defaults on top of N–S scroll / split-toggle base."""
    d = dict(default_gesture_selections())
    d[IDC_GESTURE_EAST_NORTH] = GESTURE_VOLUME_UP_ITEM
    d[IDC_GESTURE_EAST_SOUTH] = GESTURE_VOLUME_DOWN_ITEM
    d[IDC_GESTURE_DOUBLE_LEFT] = GESTURE_SCROLL_WEST_ITEM
    d[IDC_GESTURE_DOUBLE_RIGHT] = GESTURE_SCROLL_EAST_ITEM
    d[IDC_GESTURE_WEST_NORTH] = GESTURE_CHANGE_APP_ITEM
    d[IDC_GESTURE_WEST_SOUTH] = GESTURE_SHOW_DESKTOP_ITEM
    d[IDC_GESTURE_SOUTH_EAST] = GESTURE_WARP_FIRST_ITEM
    d[IDC_GESTURE_SOUTH_WEST] = GESTURE_WARP_SECOND_ITEM
    d[IDC_GESTURE_NORTH_WEST] = GESTURE_TAB_LEFT_ITEM
    d[IDC_GESTURE_NORTH_EAST] = GESTURE_TAB_RIGHT_ITEM
    return d


def gesture_defaults_for_profile_slot(slot: int) -> dict[int, int]:
    """Profile slot defaults for gesture combo indices."""
    if slot == 1:
        return split_profile_gesture_selections()
    if slot == 3:
        return empty_gesture_selections()
    if slot in (0, 2):
        return merged_profile_gesture_selections()
    return default_gesture_selections()


def profile_idx_from_flags(split: bool, duo_shift_on: bool) -> int:
    """0 merged, 1 split, 2 merged shift, 3 split shift."""
    return (1 if split else 0) + (2 if duo_shift_on else 0)


MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
WHEEL_DELTA = 120

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_TAB = 0x09
VK_D = 0x44
VK_LWIN = 0x5B
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
# MapVirtualKey map type: VK → scan code for keybd_event bScan.
MAPVK_VK_TO_VSC = 0

# Combo box (comctl / user)
CBS_DROPDOWNLIST = 0x0003
CB_ADDSTRING = 0x0143
CB_RESETCONTENT = 0x014B
CB_SETCURSEL = 0x014E
CB_GETCURSEL = 0x0147
CBN_SELCHANGE = 1

# Common dialog: ChooseColor
CC_RGBINIT = 0x00000001
CC_FULLOPEN = 0x00000002
CC_ANYCOLOR = 0x00000100
CC_ENABLEHOOK = 0x00000010

# Edit control notifications (WM_COMMAND hiword)
EN_KILLFOCUS = 0x0200

WS_EX_CLIENTEDGE = 0x00000200
ES_AUTOHSCROLL = 0x0080
ES_NUMBER = 0x2000

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

SW_HIDE = 0
SW_SHOW = 5
SW_RESTORE = 9
SW_SHOWNOACTIVATE = 4

WM_CLOSE = 0x0010
WM_SYSCOMMAND = 0x0112
SC_MINIMIZE = 0xF020
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

NIM_ADD = 0
NIM_DELETE = 2
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

TPM_RIGHTBUTTON = 0x0002
TPM_BOTTOMALIGN = 0x0020
MF_STRING = 0x0000
MF_ENABLED = 0x0000

IDI_APPLICATION = 32512
TRAY_ICON_ID = 1
IDM_TRAY_OPEN = 3001
IDM_TRAY_EXIT = 3002

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOREDRAW = 0x0008
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040

GWL_STYLE = -16
GWLP_WNDPROC = -4

WS_BORDER = 0x00800000
WS_DLGFRAME = 0x00400000
WS_THICKFRAME = 0x00040000

HWND_TOPMOST = -1
GA_ROOT = 2

NULL_BRUSH_STOCK = 5

# 2nd-cursor overlay: WindowFromPoint can return this HWND; skip moving focus to it for tab gestures.
_TAB_FOCUS_SKIP_HWND: list[int] = [0]

# Layered window: UpdateLayeredWindow + 32-bpp alpha bitmap (no color-key box).
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0
DIB_RGB_COLORS = 0
DI_NORMAL = 0x0003
SRCCOPY = 0x00CC0020
HALFTONE_STRETCH = 4

# Optional high-res PNG next to this script (white fill + dark outline works best).
SECOND_CURSOR_PNG = "second_cursor.png"
GHOST_CURSOR_PNG = "ghost_cursor.png"  # legacy filename, also checked

# Defaults for 2nd cursor overlay (overridden from UI / overlay_state).
DEFAULT_GHOST_SIZE_PX = 28
MIN_GHOST_SIZE_PX = 16
MAX_GHOST_SIZE_PX = 160
# Minimum high-res scratch side (must fit draw_large + margin).
GHOST_TEMP_MIN_PX = 192
# Overall opacity of the 2nd cursor (0–255).
GHOST_ALPHA = 200

# Elastic “snap off” when entering split: ghost overlay offset from logical pos.
SPLIT_SNAP_TIMER_ID = 16002
SPLIT_SNAP_STIFFNESS = 280.0
SPLIT_SNAP_DAMPING = 22.0
SPLIT_SNAP_IMPULSE_VX = 540.0
SPLIT_SNAP_IMPULSE_VY = -415.0
SPLIT_SNAP_DT_CAP_S = 0.048
SPLIT_SNAP_MAX_S = 0.38
# Shockwave rings only (split FX layered window).
SPLIT_SNAP_RIPPLE_DURATION_S = 0.26
# Merge (contracting) shockwave: faster than split expand.
MERGE_SNAP_RIPPLE_DURATION_S = SPLIT_SNAP_RIPPLE_DURATION_S * 0.35
# Layered window centered on split point for shockwave FX (not on ghost cursor).
SPLIT_FX_CANVAS_PX = 176

RIM_TYPEMOUSE = 0
RID_INPUT = 0x10000003

RIDEV_INPUTSINK = 0x00000100

RIDI_DEVICENAME = 0x20000007

MOUSE_MOVE_RELATIVE = 0x0000
MOUSE_MOVE_ABSOLUTE = 0x0001
MOUSE_VIRTUAL_DESKTOP = 0x0002
MOUSE_ATTRIBUTES_CHANGED = 0x0004

# HID generic desktop / mouse
HID_USAGE_PAGE_GENERIC = 0x01
HID_USAGE_GENERIC_MOUSE = 0x02

# Window class
CS_HREDRAW = 0x0002
CS_VREDRAW = 0x0001
CW_USEDEFAULT = 0x80000000

WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE = 0x10000000

IDC_ARROW = 32512

# --- Structures ---


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class RAWMOUSE(ctypes.Structure):
    _fields_ = [
        ("usFlags", wintypes.USHORT),
        ("usButtonFlags", wintypes.USHORT),
        ("usButtonData", wintypes.USHORT),
        ("ulRawButtons", wintypes.DWORD),
        ("lLastX", wintypes.LONG),
        ("lLastY", wintypes.LONG),
        ("ulExtraInformation", wintypes.DWORD),
    ]


class RAWINPUT_UNION(ctypes.Union):
    _fields_ = [("mouse", RAWMOUSE)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("data", RAWINPUT_UNION)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HANDLE),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HANDLE),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class WRECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HANDLE),
        ("fErase", wintypes.BOOL),
        ("rcPaint", WRECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", wintypes.BYTE * 32),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 1),
    ]


class WIN_SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", wintypes.BYTE),
        ("BlendFlags", wintypes.BYTE),
        ("SourceConstantAlpha", wintypes.BYTE),
        ("AlphaFormat", wintypes.BYTE),
    ]


class CHOOSECOLOR(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD),
        ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HWND),
        ("rgbResult", wintypes.DWORD),
        ("lpCustColors", ctypes.POINTER(wintypes.DWORD)),
        ("Flags", wintypes.DWORD),
        ("lCustData", ctypes.c_void_p),
        ("lpfnHook", ctypes.c_void_p),
        ("lpTemplateName", wintypes.LPCWSTR),
    ]


# ChooseColor hook: return 0 so the default dialog procedure still runs.
LPCCHOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_size_t,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class WNDCLASSW(ctypes.Structure):
    # Python 3.12+ omits HICON/HCURSOR/HBRUSH from wintypes; HANDLE fits these fields.
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


PM_RAWINPUT = ctypes.POINTER(RAWINPUT)

user32.GetRawInputData.argtypes = [
    wintypes.HANDLE,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.UINT),
    wintypes.UINT,
]
user32.GetRawInputData.restype = wintypes.UINT

user32.RegisterRawInputDevices.argtypes = [
    ctypes.POINTER(RAWINPUTDEVICE),
    wintypes.UINT,
    wintypes.UINT,
]
user32.RegisterRawInputDevices.restype = wintypes.BOOL

user32.DefWindowProcW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.DefWindowProcW.restype = wintypes.LRESULT

user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.BeginPaint.restype = wintypes.HANDLE
user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.EndPaint.restype = wintypes.BOOL
user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
user32.InvalidateRect.restype = wintypes.BOOL
user32.UpdateWindow.argtypes = [wintypes.HWND]
user32.UpdateWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL

user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t

user32.WindowFromPoint.argtypes = [POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wintypes.BOOL

user32.CreatePopupMenu.argtypes = []
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = [
    wintypes.HMENU,
    wintypes.UINT,
    ctypes.c_size_t,
    wintypes.LPCWSTR,
]
user32.AppendMenuW.restype = wintypes.BOOL
user32.TrackPopupMenu.argtypes = [
    wintypes.HMENU,
    wintypes.UINT,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    ctypes.c_void_p,
]
user32.TrackPopupMenu.restype = wintypes.BOOL
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.DestroyMenu.restype = wintypes.BOOL
user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
user32.LoadIconW.restype = wintypes.HANDLE

shell32 = ctypes.windll.shell32
shell32.Shell_NotifyIconW.argtypes = [
    wintypes.DWORD,
    ctypes.POINTER(NOTIFYICONDATAW),
]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL

kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

# Win64 module/instance handles are pointer-sized; default ctypes conversions can
# raise OverflowError on CreateWindowExW (argument 11 = hInstance).
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE

user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    ctypes.c_void_p,
]
user32.CreateWindowExW.restype = wintypes.HWND

user32.DrawIconEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
    wintypes.HANDLE,
    wintypes.UINT,
]
user32.DrawIconEx.restype = wintypes.BOOL

user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND,
    wintypes.HANDLE,
    ctypes.POINTER(POINT),
    ctypes.POINTER(WIN_SIZE),
    wintypes.HANDLE,
    ctypes.POINTER(POINT),
    wintypes.COLORREF,
    ctypes.POINTER(BLENDFUNCTION),
    wintypes.DWORD,
]
user32.UpdateLayeredWindow.restype = wintypes.BOOL

gdi32.CreateCompatibleDC.argtypes = [wintypes.HANDLE]
gdi32.CreateCompatibleDC.restype = wintypes.HANDLE
gdi32.DeleteDC.argtypes = [wintypes.HANDLE]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.SelectObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
gdi32.SelectObject.restype = wintypes.HANDLE
gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
gdi32.DeleteObject.restype = wintypes.BOOL

gdi32.CreateDIBSection.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(BITMAPINFO),
    wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p),
    wintypes.HANDLE,
    wintypes.DWORD,
]
gdi32.CreateDIBSection.restype = wintypes.HANDLE

gdi32.SetStretchBltMode.argtypes = [wintypes.HANDLE, ctypes.c_int]
gdi32.SetStretchBltMode.restype = ctypes.c_int
gdi32.StretchBlt.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.DWORD,
]
gdi32.StretchBlt.restype = wintypes.BOOL

user32.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetDlgItem.restype = ctypes.c_void_p
user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
user32.SetWindowTextW.restype = wintypes.BOOL
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int

user32.SendMessageW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.SendMessageW.restype = wintypes.LRESULT

user32.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
user32.IsChild.restype = wintypes.BOOL

user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.HINSTANCE,
    wintypes.DWORD,
]
user32.SetWindowsHookExW.restype = ctypes.c_void_p

user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL

user32.CallNextHookEx.argtypes = [
    ctypes.c_void_p,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.CallNextHookEx.restype = wintypes.LRESULT

user32.PostMessageW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.PostMessageW.restype = wintypes.BOOL

user32.mouse_event.argtypes = [
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_ulonglong,
]
user32.mouse_event.restype = None

user32.CallWindowProcW.argtypes = [
    ctypes.c_void_p,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.CallWindowProcW.restype = wintypes.LRESULT

user32.keybd_event.argtypes = [
    ctypes.c_ubyte,
    ctypes.c_ubyte,
    wintypes.DWORD,
    ctypes.c_ulong,
]
user32.keybd_event.restype = None

user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT

user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

user32.EnableWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
user32.EnableWindow.restype = wintypes.BOOL

user32.GetRawInputDeviceInfoW.argtypes = [
    wintypes.HANDLE,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.UINT),
]
user32.GetRawInputDeviceInfoW.restype = wintypes.UINT

comdlg32 = ctypes.windll.comdlg32
comdlg32.ChooseColorW.argtypes = [ctypes.POINTER(CHOOSECOLOR)]
comdlg32.ChooseColorW.restype = wintypes.BOOL


def clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))


def clamp_gesture_aim_pct(v: int) -> int:
    return clamp(int(v), MIN_SENSITIVITY_PCT, MAX_GESTURE_AIM_PCT)


def clamp_profile_gesture_cooldown_ms(v: int) -> int:
    return clamp(
        int(v), MIN_PROFILE_GESTURE_COOLDOWN_MS, MAX_PROFILE_GESTURE_COOLDOWN_MS
    )


def _short_device_path(path: str) -> str:
    m = re.search(r"VID_([0-9A-F]{4})&PID_([0-9A-F]{4})", path, re.I)
    if m:
        return f"Mouse VID {m.group(1)} PID {m.group(2)}"
    tail = path.replace("/", "\\").split("\\")[-1]
    if len(tail) > 52:
        return tail[:49] + "…"
    return tail or "Mouse"


def raw_mouse_device_caption(dev_id: int) -> str:
    """Short label for a raw-input mouse handle (numeric dev_id from hDevice)."""
    fallback = f"Mouse ({dev_id & 0xFFFFFF:06x})"
    if not dev_id:
        return "Mouse"
    h = wintypes.HANDLE(dev_id)
    sz = wintypes.UINT(0)
    ret = user32.GetRawInputDeviceInfoW(h, RIDI_DEVICENAME, None, ctypes.byref(sz))
    if ret == 0xFFFFFFFF or sz.value == 0:
        return fallback
    # pcbSize is WCHAR count including null terminator (RIDI_DEVICENAME).
    nchar = max(4, int(sz.value))
    buf = ctypes.create_unicode_buffer(nchar)
    sz2 = wintypes.UINT(nchar)
    ret2 = user32.GetRawInputDeviceInfoW(h, RIDI_DEVICENAME, buf, ctypes.byref(sz2))
    if ret2 == 0xFFFFFFFF:
        return fallback
    return _short_device_path(buf.value)


def inject_mouse_wheel_delta(delta: int) -> None:
    """Send one vertical wheel tick (delta typically ±WHEEL_DELTA)."""
    dw_data = ctypes.c_uint32(int(delta) & 0xFFFFFFFF).value
    user32.mouse_event(
        MOUSEEVENTF_WHEEL,
        0,
        0,
        dw_data,
        ctypes.c_ulonglong(0),
    )


def inject_mouse_hwheel_delta(delta: int) -> None:
    """Send one horizontal wheel tick (positive ≈ east/right, negative ≈ west/left)."""
    dw_data = ctypes.c_uint32(int(delta) & 0xFFFFFFFF).value
    user32.mouse_event(
        MOUSEEVENTF_HWHEEL,
        0,
        0,
        dw_data,
        ctypes.c_ulonglong(0),
    )


def inject_mouse_move_relative(dx: int, dy: int) -> None:
    """Relative move in mickeys (same units as raw-input deltas after scaling)."""
    if dx == 0 and dy == 0:
        return
    user32.mouse_event(
        MOUSEEVENTF_MOVE,
        int(dx),
        int(dy),
        0,
        ctypes.c_ulonglong(0),
    )


def inject_mouse_left_down() -> None:
    user32.mouse_event(
        MOUSEEVENTF_LEFTDOWN,
        0,
        0,
        0,
        ctypes.c_ulonglong(0),
    )


def inject_mouse_left_up() -> None:
    user32.mouse_event(
        MOUSEEVENTF_LEFTUP,
        0,
        0,
        0,
        ctypes.c_ulonglong(0),
    )


def inject_mouse_right_down() -> None:
    user32.mouse_event(
        MOUSEEVENTF_RIGHTDOWN,
        0,
        0,
        0,
        ctypes.c_ulonglong(0),
    )


def inject_mouse_right_up() -> None:
    user32.mouse_event(
        MOUSEEVENTF_RIGHTUP,
        0,
        0,
        0,
        ctypes.c_ulonglong(0),
    )


def inject_mouse_middle_down() -> None:
    user32.mouse_event(
        MOUSEEVENTF_MIDDLEDOWN,
        0,
        0,
        0,
        ctypes.c_ulonglong(0),
    )


def inject_mouse_middle_up() -> None:
    user32.mouse_event(
        MOUSEEVENTF_MIDDLEUP,
        0,
        0,
        0,
        ctypes.c_ulonglong(0),
    )


def _ctrl_scan_code() -> int:
    return int(user32.MapVirtualKeyW(VK_CONTROL, MAPVK_VK_TO_VSC) & 0xFF)


def _ctrl_key_is_down() -> bool:
    """True if Ctrl is physically down (GetAsyncKeyState high bit)."""
    return (int(user32.GetAsyncKeyState(VK_CONTROL)) & 0x8000) != 0


def _shift_key_is_down() -> bool:
    """True if Shift is physically down (GetAsyncKeyState high bit)."""
    return (int(user32.GetAsyncKeyState(VK_SHIFT)) & 0x8000) != 0


def _ensure_ctrl_down(scan: int) -> None:
    """Re-assert Ctrl if it is not down before sending a wheel event."""
    if not _ctrl_key_is_down():
        user32.keybd_event(VK_CONTROL, scan, 0, ctypes.c_ulong(0))


def inject_ctrl_wheel_zoom_in_burst(total_positive: int) -> None:
    """Hold Ctrl while sending positive wheel deltas (zoom-in in most apps)."""
    total = max(0, int(total_positive))
    if total <= 0:
        return
    scan = _ctrl_scan_code()
    user32.keybd_event(VK_CONTROL, scan, 0, ctypes.c_ulong(0))
    try:
        remaining = total
        while remaining > 0:
            chunk = min(remaining, WHEEL_DELTA)
            _ensure_ctrl_down(scan)
            inject_mouse_wheel_delta(chunk)
            remaining -= chunk
    finally:
        user32.keybd_event(VK_CONTROL, scan, KEYEVENTF_KEYUP, ctypes.c_ulong(0))


def inject_volume_up() -> None:
    """One system volume-up step (VK_VOLUME_UP, extended key)."""
    vk = VK_VOLUME_UP
    scan = int(user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC) & 0xFF)
    ext = KEYEVENTF_EXTENDEDKEY
    user32.keybd_event(vk, scan, ext, ctypes.c_ulong(0))
    user32.keybd_event(vk, scan, ext | KEYEVENTF_KEYUP, ctypes.c_ulong(0))


def inject_volume_down() -> None:
    """One system volume-down step (VK_VOLUME_DOWN, extended key)."""
    vk = VK_VOLUME_DOWN
    scan = int(user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC) & 0xFF)
    ext = KEYEVENTF_EXTENDEDKEY
    user32.keybd_event(vk, scan, ext, ctypes.c_ulong(0))
    user32.keybd_event(vk, scan, ext | KEYEVENTF_KEYUP, ctypes.c_ulong(0))


def register_tab_focus_skip_hwnd(hwnd: int | None) -> None:
    """Exclude the 2nd-cursor layered HWND from WindowFromPoint tab targeting."""
    _TAB_FOCUS_SKIP_HWND[0] = int(hwnd) if hwnd else 0


def _hwnd_int(h: object) -> int:
    if not h:
        return 0
    return int(ctypes.cast(h, ctypes.c_void_p).value or 0)


def _focus_window_under_cursor_for_tab() -> None:
    """Focus the window under the cursor so injected Ctrl+Tab goes there (similar to wheel routing)."""
    pt = POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return
    target = user32.WindowFromPoint(pt)
    t = _hwnd_int(target)
    if not t:
        return
    sk = _TAB_FOCUS_SKIP_HWND[0]
    if sk and t == sk:
        return
    root_h = user32.GetAncestor(target, GA_ROOT)
    root = _hwnd_int(root_h) if root_h else 0
    if not root:
        root = t
    fg = user32.GetForegroundWindow()
    pid_dummy = wintypes.DWORD()
    fg_tid = 0
    if fg:
        fg_tid = user32.GetWindowThreadProcessId(fg, ctypes.byref(pid_dummy))
    cur_tid = kernel32.GetCurrentThreadId()
    attached = False
    try:
        if fg_tid and fg_tid != cur_tid:
            attached = bool(user32.AttachThreadInput(fg_tid, cur_tid, True))
        user32.SetForegroundWindow(ctypes.c_void_p(root))
        user32.SetFocus(target)
    finally:
        if attached and fg_tid:
            user32.AttachThreadInput(fg_tid, cur_tid, False)


def inject_tab_right() -> None:
    """Ctrl+Tab — next tab in most apps. Does not release a Ctrl the user is already holding."""
    _focus_window_under_cursor_for_tab()
    ctrl_scan = _ctrl_scan_code()
    tab_scan = int(user32.MapVirtualKeyW(VK_TAB, MAPVK_VK_TO_VSC) & 0xFF)
    ctrl_was = _ctrl_key_is_down()
    if not ctrl_was:
        user32.keybd_event(VK_CONTROL, ctrl_scan, 0, ctypes.c_ulong(0))
    try:
        user32.keybd_event(VK_TAB, tab_scan, 0, ctypes.c_ulong(0))
        user32.keybd_event(VK_TAB, tab_scan, KEYEVENTF_KEYUP, ctypes.c_ulong(0))
    finally:
        if not ctrl_was:
            user32.keybd_event(VK_CONTROL, ctrl_scan, KEYEVENTF_KEYUP, ctypes.c_ulong(0))


def inject_tab_left() -> None:
    """Ctrl+Shift+Tab — previous tab. Does not release modifier keys the user is already holding."""
    _focus_window_under_cursor_for_tab()
    ctrl_scan = _ctrl_scan_code()
    shift_scan = int(user32.MapVirtualKeyW(VK_SHIFT, MAPVK_VK_TO_VSC) & 0xFF)
    tab_scan = int(user32.MapVirtualKeyW(VK_TAB, MAPVK_VK_TO_VSC) & 0xFF)
    ctrl_was = _ctrl_key_is_down()
    shift_was = _shift_key_is_down()
    if not ctrl_was:
        user32.keybd_event(VK_CONTROL, ctrl_scan, 0, ctypes.c_ulong(0))
    if not shift_was:
        user32.keybd_event(VK_SHIFT, shift_scan, 0, ctypes.c_ulong(0))
    try:
        user32.keybd_event(VK_TAB, tab_scan, 0, ctypes.c_ulong(0))
        user32.keybd_event(VK_TAB, tab_scan, KEYEVENTF_KEYUP, ctypes.c_ulong(0))
    finally:
        if not shift_was:
            user32.keybd_event(VK_SHIFT, shift_scan, KEYEVENTF_KEYUP, ctypes.c_ulong(0))
        if not ctrl_was:
            user32.keybd_event(VK_CONTROL, ctrl_scan, KEYEVENTF_KEYUP, ctypes.c_ulong(0))


def inject_change_application() -> None:
    """Win+Tab — Task View / app switcher. Does not release Win if the user is already holding it."""
    win_scan = int(user32.MapVirtualKeyW(VK_LWIN, MAPVK_VK_TO_VSC) & 0xFF)
    tab_scan = int(user32.MapVirtualKeyW(VK_TAB, MAPVK_VK_TO_VSC) & 0xFF)
    ext = KEYEVENTF_EXTENDEDKEY
    win_was = (int(user32.GetAsyncKeyState(VK_LWIN)) & 0x8000) != 0
    if not win_was:
        user32.keybd_event(VK_LWIN, win_scan, ext, ctypes.c_ulong(0))
    try:
        user32.keybd_event(VK_TAB, tab_scan, 0, ctypes.c_ulong(0))
        user32.keybd_event(VK_TAB, tab_scan, KEYEVENTF_KEYUP, ctypes.c_ulong(0))
    finally:
        if not win_was:
            user32.keybd_event(VK_LWIN, win_scan, ext | KEYEVENTF_KEYUP, ctypes.c_ulong(0))


def inject_show_desktop() -> None:
    """Win+D — show/hide desktop. Does not release Win if the user is already holding it."""
    win_scan = int(user32.MapVirtualKeyW(VK_LWIN, MAPVK_VK_TO_VSC) & 0xFF)
    d_scan = int(user32.MapVirtualKeyW(VK_D, MAPVK_VK_TO_VSC) & 0xFF)
    ext = KEYEVENTF_EXTENDEDKEY
    win_was = (int(user32.GetAsyncKeyState(VK_LWIN)) & 0x8000) != 0
    if not win_was:
        user32.keybd_event(VK_LWIN, win_scan, ext, ctypes.c_ulong(0))
    try:
        user32.keybd_event(VK_D, d_scan, 0, ctypes.c_ulong(0))
        user32.keybd_event(VK_D, d_scan, KEYEVENTF_KEYUP, ctypes.c_ulong(0))
    finally:
        if not win_was:
            user32.keybd_event(VK_LWIN, win_scan, ext | KEYEVENTF_KEYUP, ctypes.c_ulong(0))


def inject_ctrl_wheel_zoom_out_burst(total_positive_magnitude: int) -> None:
    """Hold Ctrl while sending negative wheel deltas (zoom-out in most apps)."""
    total = max(0, int(total_positive_magnitude))
    if total <= 0:
        return
    scan = _ctrl_scan_code()
    user32.keybd_event(VK_CONTROL, scan, 0, ctypes.c_ulong(0))
    try:
        remaining = total
        while remaining > 0:
            chunk = min(remaining, WHEEL_DELTA)
            _ensure_ctrl_down(scan)
            inject_mouse_wheel_delta(-chunk)
            remaining -= chunk
    finally:
        user32.keybd_event(VK_CONTROL, scan, KEYEVENTF_KEYUP, ctypes.c_ulong(0))


def scale_mouse_delta_xy(dx: int, dy: int, pct: int) -> tuple[int, int]:
    """Scale raw relative counts by percentage (100 = unchanged). Preserve sign; keep min step 1."""
    p = clamp(int(pct), MIN_SENSITIVITY_PCT, MAX_SENSITIVITY_PCT)

    def one(d: int) -> int:
        if d == 0:
            return 0
        s = int(round(d * p / 100.0))
        if s == 0:
            return 1 if d > 0 else -1
        return s

    return one(dx), one(dy)


def _configure_ghost_popup_no_frame(hwnd: int) -> None:
    """Remove thin popup/DWM frame around the layered 2nd-cursor window (no border)."""
    hwnd = int(hwnd)
    style = int(user32.GetWindowLongPtrW(hwnd, GWL_STYLE))
    style &= ~(WS_BORDER | WS_DLGFRAME | WS_THICKFRAME)
    user32.SetWindowLongPtrW(hwnd, GWL_STYLE, ctypes.c_ssize_t(style))
    user32.SetWindowPos(
        hwnd,
        None,
        0,
        0,
        0,
        0,
        SWP_NOMOVE
        | SWP_NOSIZE
        | SWP_NOZORDER
        | SWP_FRAMECHANGED
        | SWP_NOACTIVATE,
    )
    try:
        dwm = ctypes.WinDLL("dwmapi")
        DwmSetWindowAttribute = dwm.DwmSetWindowAttribute
        DwmSetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        DwmSetWindowAttribute.restype = ctypes.c_long
        ncrp = ctypes.c_int(1)  # DWMNCRP_DISABLED
        DwmSetWindowAttribute(hwnd, 2, ctypes.byref(ncrp), ctypes.sizeof(ncrp))
        corners = ctypes.c_int(1)  # DWMWCP_DONOTROUND (Win11)
        DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corners), ctypes.sizeof(corners))
    except (OSError, AttributeError):
        pass


def get_virtual_screen_bounds() -> tuple[int, int, int, int]:
    """Returns (left, top, width, height) of the virtual screen (all monitors)."""
    left = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
    top = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
    w = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
    h = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
    return left, top, w, h


msvcrt = ctypes.CDLL("msvcrt")
msvcrt.memset.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t]
msvcrt.memset.restype = ctypes.c_void_p
msvcrt.memcpy.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
msvcrt.memcpy.restype = ctypes.c_void_p


def _mirror_bitmap_horizontal(bits: ctypes.c_void_p, width: int, height: int) -> None:
    """Flip BGRA rows left–right (same arrow as system, mirrored)."""
    addr = bits.value or 0
    if not addr:
        return
    n = width * height * 4
    buf = (ctypes.c_ubyte * n).from_address(addr)
    for y in range(height):
        row = y * width * 4
        for x in range(width // 2):
            o1 = row + x * 4
            o2 = row + (width - 1 - x) * 4
            for k in range(4):
                buf[o1 + k], buf[o2 + k] = buf[o2 + k], buf[o1 + k]


def _colorref_to_rgb(cr: int) -> tuple[int, int, int]:
    """COLORREF: low byte = red, then green, then blue (not BGR)."""
    cr = int(cr) & 0xFFFFFFFF
    r = cr & 0xFF
    g = (cr >> 8) & 0xFF
    b = (cr >> 16) & 0xFF
    return r, g, b


def _rgb_to_colorref(r: int, g: int, b: int) -> int:
    return (r & 0xFF) | ((g & 0xFF) << 8) | ((b & 0xFF) << 16)


def _defringe_dark_halos(px, side: int) -> None:
    """Remove LANCZOS/chroma halos: dark gray fringe pixels outside the glyph."""
    for y in range(side):
        for x in range(side):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            mx = max(r, g, b)
            lum = (r * 299 + g * 587 + b * 114) // 1000
            if mx < 56 and lum < 62:
                px[x, y] = (0, 0, 0, 0)


def _try_load_ghost_png_to_dib(path: str, bits: ctypes.c_void_p, w: int) -> bool:
    """Load PNG into top-down BGRA DIB; black/near-black -> transparent. Needs Pillow."""
    try:
        from PIL import Image
    except ImportError:
        print("2nd cursor PNG found but Pillow is missing; run: pip install Pillow")
        return False
    try:
        lanczos = Image.Resampling.LANCZOS
    except AttributeError:
        lanczos = Image.LANCZOS  # type: ignore[attr-defined]
    try:
        im = Image.open(path).convert("RGBA")
    except OSError:
        return False
    inner = max(1, w)
    iw, ih = im.size
    scale = min(inner / iw, inner / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    im = im.resize((nw, nh), lanczos)
    canvas = Image.new("RGBA", (w, w), (0, 0, 0, 0))
    ox = (w - nw) // 2
    oy = (w - nh) // 2
    canvas.paste(im, (ox, oy), im)
    px = canvas.load()
    _defringe_dark_halos(px, w)
    addr = bits.value or 0
    if not addr:
        return False
    key = 22
    buf = (ctypes.c_ubyte * (w * w * 4)).from_address(addr)
    for y in range(w):
        for x in range(w):
            r, g, b, a = px[x, y]
            if r <= key and g <= key and b <= key:
                r = g = b = a = 0
            if a == 0:
                r = g = b = 0
            o = (y * w + x) * 4
            buf[o + 0] = b
            buf[o + 1] = g
            buf[o + 2] = r
            buf[o + 3] = a
    return True


def _prepare_ghost_cursor_pixels(
    bits: ctypes.c_void_p,
    width: int,
    height: int,
    tint_r: int,
    tint_g: int,
    tint_b: int,
    ghost_alpha: int,
) -> None:
    """Apply picker color to light pixels (fill); keep dark outline; premultiply BGRA."""
    addr = bits.value or 0
    if not addr:
        return
    n = width * height * 4
    buf = (ctypes.c_ubyte * n).from_address(addr)
    tr = clamp(int(tint_r), 0, 255)
    tg = clamp(int(tint_g), 0, 255)
    tb = clamp(int(tint_b), 0, 255)
    ga = clamp(int(ghost_alpha), 0, 255)
    lum_hi = 175
    lum_lo = 70
    for i in range(width * height):
        o = i * 4
        b, g, r, a = buf[o], buf[o + 1], buf[o + 2], buf[o + 3]
        if a == 0 and (r | g | b) != 0:
            a = 255
        if a == 0:
            continue
        lum = (r * 299 + g * 587 + b * 114) // 1000
        if lum < 0:
            lum = 0
        if lum > 255:
            lum = 255
        if lum >= lum_hi:
            br = lum / 255.0
            r = int(tr * br)
            g = int(tg * br)
            b = int(tb * br)
        elif lum <= lum_lo:
            pass
        else:
            u = (lum - lum_lo) / float(lum_hi - lum_lo)
            br = lum / 255.0
            nr = int(tr * br)
            ng = int(tg * br)
            nb = int(tb * br)
            r = int(r * (1.0 - u) + nr * u)
            g = int(g * (1.0 - u) + ng * u)
            b = int(b * (1.0 - u) + nb * u)
        a = (a * ga) // 255
        if a < 0:
            a = 0
        if a > 255:
            a = 255
        buf[o] = (b * a) // 255
        buf[o + 1] = (g * a) // 255
        buf[o + 2] = (r * a) // 255
        buf[o + 3] = a


def _build_ghost_layer_assets(overlay_state: dict[str, object]) -> bool:
    w = int(overlay_state.get("ghost_size", DEFAULT_GHOST_SIZE_PX))
    w = clamp(w, MIN_GHOST_SIZE_PX, MAX_GHOST_SIZE_PX)
    overlay_state["ghost_size"] = w
    h = w
    tr, tg, tb = overlay_state.get("ghost_tint", (220, 235, 255))
    tr, tg, tb = clamp(int(tr), 0, 255), clamp(int(tg), 0, 255), clamp(int(tb), 0, 255)
    overlay_state["ghost_tint"] = (tr, tg, tb)

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB

    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(
        None,
        ctypes.byref(bmi),
        DIB_RGB_COLORS,
        ctypes.byref(bits),
        None,
        0,
    )
    if not hbmp:
        return False

    msvcrt.memset(ctypes.c_void_p(bits.value or 0), 0, w * h * 4)

    memdc = gdi32.CreateCompatibleDC(None)
    if not memdc:
        gdi32.DeleteObject(hbmp)
        return False
    old = gdi32.SelectObject(memdc, hbmp)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    png_path = None
    for _png_name in (SECOND_CURSOR_PNG, GHOST_CURSOR_PNG):
        _p = os.path.join(script_dir, _png_name)
        if os.path.isfile(_p):
            png_path = _p
            break
    if png_path and _try_load_ghost_png_to_dib(png_path, bits, w):
        # Asset is already mirrored; no DrawIcon path.
        _prepare_ghost_cursor_pixels(bits, w, h, tr, tg, tb, GHOST_ALPHA)
        overlay_state["ghost_memdc"] = memdc
        overlay_state["ghost_hbmp"] = hbmp
        overlay_state["ghost_oldbmp"] = old
        overlay_state["ghost_bits"] = int(bits.value or 0)
        return True

    t = max(GHOST_TEMP_MIN_PX, w * 2 + 32)
    draw_large = min(t - 24, max(48, int(w * 1.28)))

    tbmi = BITMAPINFO()
    tbmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    tbmi.bmiHeader.biWidth = t
    tbmi.bmiHeader.biHeight = -t
    tbmi.bmiHeader.biPlanes = 1
    tbmi.bmiHeader.biBitCount = 32
    tbmi.bmiHeader.biCompression = BI_RGB

    tbits = ctypes.c_void_p()
    tbmp = gdi32.CreateDIBSection(
        None,
        ctypes.byref(tbmi),
        DIB_RGB_COLORS,
        ctypes.byref(tbits),
        None,
        0,
    )
    if not tbmp:
        gdi32.SelectObject(memdc, old)
        gdi32.DeleteDC(memdc)
        gdi32.DeleteObject(hbmp)
        return False

    msvcrt.memset(ctypes.c_void_p(tbits.value or 0), 0, t * t * 4)

    temp_dc = gdi32.CreateCompatibleDC(None)
    if not temp_dc:
        gdi32.DeleteObject(tbmp)
        gdi32.SelectObject(memdc, old)
        gdi32.DeleteDC(memdc)
        gdi32.DeleteObject(hbmp)
        return False
    told = gdi32.SelectObject(temp_dc, tbmp)

    hcur = user32.LoadCursorW(None, IDC_ARROW)
    cx = draw_large
    cy = draw_large
    mx = (t - cx) // 2
    my = (t - cy) // 2
    user32.DrawIconEx(temp_dc, mx, my, hcur, cx, cy, 0, None, DI_NORMAL)

    gdi32.SetStretchBltMode(memdc, HALFTONE_STRETCH)
    if not gdi32.StretchBlt(
        memdc,
        0,
        0,
        w,
        h,
        temp_dc,
        0,
        0,
        t,
        t,
        SRCCOPY,
    ):
        gdi32.SelectObject(temp_dc, told)
        gdi32.DeleteDC(temp_dc)
        gdi32.DeleteObject(tbmp)
        gdi32.SelectObject(memdc, old)
        gdi32.DeleteDC(memdc)
        gdi32.DeleteObject(hbmp)
        return False

    gdi32.SelectObject(temp_dc, told)
    gdi32.DeleteDC(temp_dc)
    gdi32.DeleteObject(tbmp)

    _mirror_bitmap_horizontal(bits, w, h)
    _prepare_ghost_cursor_pixels(bits, w, h, tr, tg, tb, GHOST_ALPHA)

    overlay_state["ghost_memdc"] = memdc
    overlay_state["ghost_hbmp"] = hbmp
    overlay_state["ghost_oldbmp"] = old
    overlay_state["ghost_bits"] = int(bits.value or 0)
    return True


def _teardown_ghost_layer_assets(overlay_state: dict[str, object]) -> None:
    memdc = overlay_state.get("ghost_memdc")
    hbmp = overlay_state.get("ghost_hbmp")
    old = overlay_state.get("ghost_oldbmp")
    if memdc:
        if old and hbmp:
            gdi32.SelectObject(memdc, old)
        gdi32.DeleteDC(memdc)
    if hbmp:
        gdi32.DeleteObject(hbmp)
    overlay_state["ghost_memdc"] = None
    overlay_state["ghost_hbmp"] = None
    overlay_state["ghost_oldbmp"] = None
    overlay_state["ghost_bits"] = 0


def _bgra_over_premul(
    buf,
    o: int,
    sr: int,
    sg: int,
    sb: int,
    sa: int,
) -> None:
    """Porter–Duff over: straight RGB + straight alpha onto premultiplied BGRA dst."""
    if sa <= 0:
        return
    sa = clamp(sa, 0, 255)
    sr = clamp(sr, 0, 255)
    sg = clamp(sg, 0, 255)
    sb = clamp(sb, 0, 255)
    sp_b = sb * sa // 255
    sp_g = sg * sa // 255
    sp_r = sr * sa // 255
    db, dg, dr, da = buf[o], buf[o + 1], buf[o + 2], buf[o + 3]
    inv = 255 - sa
    ob = sp_b + db * inv // 255
    og = sp_g + dg * inv // 255
    or_ = sp_r + dr * inv // 255
    oa = sa + da * inv // 255
    if oa > 255:
        oa = 255
    buf[o] = ob
    buf[o + 1] = og
    buf[o + 2] = or_
    buf[o + 3] = oa


def _split_snap_draw_ripple_rings(
    buf,
    side: int,
    elapsed: float,
    ripple_duration_s: float,
    tr: int,
    tg: int,
    tb: int,
    *,
    reverse: bool = False,
) -> None:
    u = elapsed / float(ripple_duration_s)
    if u >= 1.0:
        return
    fade = (1.0 - u) * (1.0 - u)
    base_a = int(115 * fade)
    if base_a <= 2:
        return
    cx = (side - 1) * 0.5
    cy = (side - 1) * 0.5
    max_r = side * 0.52
    line = 2.25
    # Forward: rings expand from center; reverse (merge): contract inward over time.
    ru = 1.0 - u if reverse else u
    rings = (
        5.0 + ru * max_r,
        5.0 + max(0.0, ru - 0.14) / 0.86 * max_r,
    )
    for ri, rad in enumerate(rings):
        if rad < 3.0:
            continue
        a_ring = base_a * (0.92 if ri == 0 else 0.65)
        for py in range(side):
            for px in range(side):
                d = math.hypot(float(px) - cx, float(py) - cy)
                if abs(d - rad) > line:
                    continue
                edge = 1.0 - abs(d - rad) / line
                a = int(a_ring * edge * edge)
                if a <= 0:
                    continue
                o = (py * side + px) * 4
                _bgra_over_premul(buf, o, tr, tg, tb, a)


def _split_snap_paint_fx_canvas(
    bits_addr: int,
    W: int,
    elapsed: float,
    ripple_duration_s: float,
    tr: int,
    tg: int,
    tb: int,
    *,
    reverse: bool = False,
) -> None:
    if not bits_addr or W <= 0:
        return
    n = W * W * 4
    msvcrt.memset(ctypes.c_void_p(bits_addr), 0, n)
    buf = (ctypes.c_ubyte * n).from_address(bits_addr)
    _split_snap_draw_ripple_rings(
        buf, W, elapsed, ripple_duration_s, tr, tg, tb, reverse=reverse
    )


def _sync_split_fx_overlay_only(
    overlay_state: dict[str, object],
    split_snap_anim: dict[str, object],
) -> None:
    """Repaint/position split FX only — never touches the ghost (keeps WM_INPUT fast)."""
    if not split_snap_anim.get("active") or "anchor_x" not in split_snap_anim:
        return
    W = int(overlay_state.get("split_fx_side") or SPLIT_FX_CANVAS_PX)
    ax = int(split_snap_anim["anchor_x"])
    ay = int(split_snap_anim["anchor_y"])
    fx_ox = ax - W // 2
    fx_oy = ay - W // 2
    fbits = int(overlay_state.get("split_fx_bits") or 0)
    if not fbits or W <= 0:
        return
    tr, tg, tb = overlay_state.get("ghost_tint", (220, 235, 255))
    tr = clamp(int(tr), 0, 255)
    tg = clamp(int(tg), 0, 255)
    tb = clamp(int(tb), 0, 255)
    elapsed = time.perf_counter() - float(split_snap_anim["start_t"])
    rev = bool(split_snap_anim.get("reverse"))
    rip_d = (
        float(MERGE_SNAP_RIPPLE_DURATION_S)
        if rev
        else float(SPLIT_SNAP_RIPPLE_DURATION_S)
    )
    _split_snap_paint_fx_canvas(fbits, W, elapsed, rip_d, tr, tg, tb, reverse=rev)
    fh = int(overlay_state.get("split_fx_hwnd") or 0)
    if not fh:
        return
    user32.SetWindowPos(
        fh,
        ctypes.c_void_p(HWND_TOPMOST),
        fx_ox,
        fx_oy,
        W,
        W,
        SWP_NOACTIVATE | SWP_SHOWWINDOW,
    )
    _present_split_fx_layer(overlay_state, fx_ox, fx_oy)
    gh = int(overlay_state.get("hwnd") or 0)
    if gh:
        user32.SetWindowPos(
            fh,
            ctypes.c_void_p(gh),
            0,
            0,
            0,
            0,
            SWP_NOMOVE
            | SWP_NOSIZE
            | SWP_NOACTIVATE
            | SWP_SHOWWINDOW,
        )


def _present_split_fx_layer(overlay_state: dict[str, object], fx_ox: int, fx_oy: int) -> None:
    fh = int(overlay_state.get("split_fx_hwnd") or 0)
    memdc = overlay_state.get("split_fx_memdc")
    if not fh or not memdc:
        return
    W = int(overlay_state.get("split_fx_side") or SPLIT_FX_CANVAS_PX)
    pt_dst = POINT()
    pt_dst.x = int(fx_ox)
    pt_dst.y = int(fx_oy)
    pt_src = POINT()
    pt_src.x = 0
    pt_src.y = 0
    sz = WIN_SIZE(W, W)
    blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
    user32.UpdateLayeredWindow(
        fh,
        None,
        ctypes.byref(pt_dst),
        ctypes.byref(sz),
        memdc,
        ctypes.byref(pt_src),
        0,
        ctypes.byref(blend),
        ULW_ALPHA,
    )


_SPLITFX_WNDPROC = ctypes.WINFUNCTYPE(
    wintypes.LRESULT,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


def _split_fx_wnd_proc_impl(hwnd, msg, wparam, lparam):
    if msg == WM_NCHITTEST:
        return HTTRANSPARENT
    if msg == WM_NCPAINT:
        return 0
    if msg == WM_ERASEBKGND:
        return 1
    if msg == WM_PAINT:
        ps = PAINTSTRUCT()
        user32.BeginPaint(hwnd, ctypes.byref(ps))
        user32.EndPaint(hwnd, ctypes.byref(ps))
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


_split_fx_wnd_proc = _SPLITFX_WNDPROC(_split_fx_wnd_proc_impl)


def _teardown_split_fx_layer(overlay_state: dict[str, object]) -> None:
    memdc = overlay_state.get("split_fx_memdc")
    hbmp = overlay_state.get("split_fx_hbmp")
    old = overlay_state.get("split_fx_oldbmp")
    fh = int(overlay_state.get("split_fx_hwnd") or 0)
    if memdc:
        if old and hbmp:
            gdi32.SelectObject(memdc, old)
        gdi32.DeleteDC(memdc)
    if hbmp:
        gdi32.DeleteObject(hbmp)
    if fh:
        user32.ShowWindow(fh, SW_HIDE)
        user32.DestroyWindow(fh)
    overlay_state["split_fx_memdc"] = None
    overlay_state["split_fx_hbmp"] = None
    overlay_state["split_fx_oldbmp"] = None
    overlay_state["split_fx_bits"] = 0
    overlay_state["split_fx_hwnd"] = None
    overlay_state["split_fx_side"] = 0


def _ensure_split_fx_layer(overlay_state: dict[str, object]) -> bool:
    W = int(SPLIT_FX_CANVAS_PX)
    if int(overlay_state.get("split_fx_hwnd") or 0) and int(
        overlay_state.get("split_fx_side") or 0
    ) == W:
        return True
    _teardown_split_fx_layer(overlay_state)
    hinst = kernel32.GetModuleHandleW(None)
    cls_name = "CharaChorderDuoSplitFxClass"
    wc = WNDCLASSW()
    wc.style = 0
    wc.lpfnWndProc = ctypes.cast(_split_fx_wnd_proc, ctypes.c_void_p)
    wc.cbClsExtra = 0
    wc.cbWndExtra = 0
    wc.hInstance = hinst
    wc.hIcon = None
    wc.hCursor = None
    wc.hbrBackground = gdi32.GetStockObject(NULL_BRUSH_STOCK)
    wc.lpszMenuName = None
    wc.lpszClassName = cls_name
    if not user32.RegisterClassW(ctypes.byref(wc)):
        if kernel32.GetLastError() != 1410:
            return False
    ex_style = (
        WS_EX_LAYERED
        | WS_EX_TOPMOST
        | WS_EX_TOOLWINDOW
        | WS_EX_NOACTIVATE
        | WS_EX_TRANSPARENT
    )
    fh = user32.CreateWindowExW(
        ex_style,
        cls_name,
        None,
        WS_POPUP,
        0,
        0,
        W,
        W,
        None,
        None,
        hinst,
        None,
    )
    if not fh:
        return False
    _configure_ghost_popup_no_frame(int(fh))
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = W
    bmi.bmiHeader.biHeight = -W
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB
    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(
        None,
        ctypes.byref(bmi),
        DIB_RGB_COLORS,
        ctypes.byref(bits),
        None,
        0,
    )
    if not hbmp:
        user32.DestroyWindow(fh)
        return False
    msvcrt.memset(ctypes.c_void_p(bits.value or 0), 0, W * W * 4)
    memdc = gdi32.CreateCompatibleDC(None)
    if not memdc:
        gdi32.DeleteObject(hbmp)
        user32.DestroyWindow(fh)
        return False
    old = gdi32.SelectObject(memdc, hbmp)
    overlay_state["split_fx_hwnd"] = int(fh)
    overlay_state["split_fx_memdc"] = memdc
    overlay_state["split_fx_hbmp"] = hbmp
    overlay_state["split_fx_oldbmp"] = old
    overlay_state["split_fx_bits"] = int(bits.value or 0)
    overlay_state["split_fx_side"] = W
    user32.ShowWindow(fh, SW_HIDE)
    return True


def _split_fx_hide(overlay_state: dict[str, object]) -> None:
    fh = int(overlay_state.get("split_fx_hwnd") or 0)
    if fh:
        user32.ShowWindow(fh, SW_HIDE)


def _present_layered_ghost(overlay_state: dict[str, object], ox: int, oy: int) -> None:
    oh = overlay_state.get("hwnd")
    memdc = overlay_state.get("ghost_memdc")
    if not oh or not memdc:
        return
    side = int(overlay_state.get("ghost_size", DEFAULT_GHOST_SIZE_PX))
    pt_dst = POINT(ox, oy)
    pt_src = POINT(0, 0)
    sz = WIN_SIZE(side, side)
    blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
    user32.UpdateLayeredWindow(
        oh,
        None,
        ctypes.byref(pt_dst),
        ctypes.byref(sz),
        memdc,
        ctypes.byref(pt_src),
        0,
        ctypes.byref(blend),
        ULW_ALPHA,
    )


def main() -> int:
    if sys.platform != "win32":
        print("This program only runs on Windows.")
        return 1

    hinst = kernel32.GetModuleHandleW(None)

    # Append-only mouse discovery order; secondary_mouse_idx picks the 2nd cursor device.
    device_order: list[int] = []
    secondary_mouse_idx = [1]
    ptr_state = {"p_pri": None, "p_sec": None}  # type: dict[str, tuple[int, int] | None]
    overlay_state: dict[str, object] = {
        "hwnd": None,
        "ghost_size": DEFAULT_GHOST_SIZE_PX,
        "ghost_tint": (220, 235, 255),
    }
    ghost_overlay_cache: dict[str, object] = {
        "shown": False,
        "x": None,
        "y": None,
        "side": None,
    }
    # Hidden damped spring: when it settles, split snap FX ends (ghost follows p_sec only).
    split_snap_anim: dict[str, object] = {
        "active": False,
        "timer_hwnd": 0,
        "start_t": 0.0,
        "last_t": 0.0,
        "off_x": 0.0,
        "off_y": 0.0,
        "vx": 0.0,
        "vy": 0.0,
    }
    dual_enabled = [False]
    # Live copy of current profile slot’s ring_animations (shockwave FX).
    ring_animations_enabled = [True]
    choosecolor_custom = (wintypes.DWORD * 16)()
    # While ChooseColor is open: .cc points at the live CHOOSECOLOR; .last_rgb for dedupe.
    choosecolor_sync: dict[str, object] = {"cc": None, "last_rgb": None}

    # Last relative dy + time per channel; gesture_sel filled in WM_CREATE / CBN_SELCHANGE.
    dual_vertical_scroll: dict[str, float | int] = {
        "pri_dy": 0,
        "sec_dy": 0,
        "pri_t": 0.0,
        "sec_t": 0.0,
        # Signed pending wheel delta (same units as inject_mouse_wheel_delta); smoothed delivery.
        "wheel_accum": 0.0,
    }
    # Signed horizontal wheel accum (MOUSEEVENTF_HWHEEL); same smoothing as vertical.
    dual_horizontal_scroll: dict[str, float] = {
        "wheel_accum": 0.0,
    }
    # West-East: secondary west / left (dx<0), primary east / right (dx>0), merged only.
    dual_horizontal_zoom: dict[str, float | int] = {
        "pri_dx": 0,
        "sec_dx": 0,
        "pri_t": 0.0,
        "sec_t": 0.0,
        "wheel_accum": 0.0,
    }
    # East-West: secondary east / right (dx>0), primary west / left (dx<0), merged only.
    dual_horizontal_zoom_out: dict[str, float | int] = {
        "pri_dx": 0,
        "sec_dx": 0,
        "pri_t": 0.0,
        "sec_t": 0.0,
        "wheel_accum": 0.0,
    }
    # West-West / East-East: both mice same horizontal direction, merged only.
    dual_horizontal_same: dict[str, float | int] = {
        "pri_dx": 0,
        "sec_dx": 0,
        "pri_t": 0.0,
        "sec_t": 0.0,
    }
    # Signed volume step demand; drained with media key taps (volume strength %).
    dual_volume: dict[str, float] = {
        "accum": 0.0,
    }
    # Tab-chord demand; drained as Ctrl+Tab / Ctrl+Shift+Tab (tab gesture %).
    dual_tab: dict[str, float] = {
        "right_accum": 0.0,
        "left_accum": 0.0,
    }
    # Win+Tab (change application); same tab gesture %.
    dual_change_app: dict[str, float] = {
        "accum": 0.0,
    }
    dual_show_desktop: dict[str, float] = {
        "accum": 0.0,
    }
    profile_store: list[dict[str, object]] = [
        {
            "input_sensitivity": copy.deepcopy(
                default_input_sensitivity_for_profile_slot(i)
            ),
            "gesture_sel": copy.deepcopy(gesture_defaults_for_profile_slot(i)),
            "ring_animations": True,
        }
        for i in range(4)
    ]
    current_profile_idx = [0]
    duo_shift = [False]
    gesture_sel: dict[int, int] = copy.deepcopy(profile_store[0]["gesture_sel"])
    input_sensitivity: dict[str, int] = copy.deepcopy(
        profile_store[0]["input_sensitivity"]
    )
    profile_combo_suppress = [False]
    red_divider_brush: list[int | None] = [None]

    DUAL_SCROLL_SYNC_S = 0.078
    DUAL_SCROLL_MIN_DY = 2
    DUAL_HZOOM_MIN_DX = 2
    # Finer than WHEEL_DELTA (120) per inject + accumulator = smoother scroll than one big step.
    WHEEL_SMOOTH_CHUNK = 24
    WHEEL_SMOOTH_MAX_BURST = 12
    WHEEL_ACCUM_GAIN = 0.52
    WHEEL_ACCUM_DECAY = 0.68
    WHEEL_ACCUM_CAP = 520.0
    VOLUME_ACCUM_CHUNK = 48
    VOLUME_ACCUM_MAX_BURST = 10
    TAB_ACCUM_CHUNK = 100
    TAB_ACCUM_MAX_BURST = 3
    # After each injected gesture output, freeze pointer positions for this long; each new
    # inject extends the window so rapid dual gestures keep movement suppressed.
    SCROLL_MOVE_LOCK_S = 0.10

    scroll_move_lock: dict[str, float | tuple[int, int] | None] = {
        "until": 0.0,
        "anchor_pri": None,
        "anchor_sec": None,
    }
    split_merge_gesture_cooldown_until = [0.0]
    warp_gesture_cooldown_until = [0.0]
    # Invisible “3rd cursor” (pin 2nd / merged Warp 2nd) and “4th cursor” (pin 1st / merged Warp 1st).
    pinned_sec_warp_point: list[tuple[int, int] | None] = [None]
    pinned_pri_warp_point: list[tuple[int, int] | None] = [None]
    # After switching profile slot: ignore dual-mice gesture patterns (see
    # profile_gesture_cooldown_ms per profile; 0 = off).
    profile_change_gesture_suppress_until = [0.0]

    def process_merged_gestures(
        config_hwnd: int,
        dev_id: int,
        sec_dev: int | None,
        dx: int,
        dy: int,
    ) -> bool:
        """When two mice are configured, each gesture row's motion dispatches that action.

        Runs in merged or split mode (primary vs secondary deltas are tracked the same way).

        Returns True if the caller should lock pointer motion (any gesture fed an action
        this frame and/or something was injected).
        """
        if sec_dev is None:
            return False

        st = dual_vertical_scroll
        st_v = dual_vertical_scroll
        st_hs = dual_horizontal_same
        st_hscr = dual_horizontal_scroll
        st_zi = dual_horizontal_zoom
        st_zo = dual_horizontal_zoom_out
        st_vol = dual_volume
        st_tab = dual_tab
        st_app = dual_change_app
        st_desktop = dual_show_desktop

        now = time.perf_counter()
        profile_gestures_ok = now >= float(
            profile_change_gesture_suppress_until[0]
        )
        stale = DUAL_SCROLL_SYNC_S * 2.5
        if now - float(st["pri_t"]) > stale:
            st["pri_dy"] = 0
        if now - float(st["sec_t"]) > stale:
            st["sec_dy"] = 0
        if dev_id == sec_dev:
            st["sec_dy"] = dy
            st["sec_t"] = now
        else:
            st["pri_dy"] = dy
            st["pri_t"] = now
        pdy = int(st["pri_dy"])
        sdy = int(st["sec_dy"])
        pt = float(st["pri_t"])
        stt = float(st["sec_t"])
        v_time = now - pt <= DUAL_SCROLL_SYNC_S and now - stt <= DUAL_SCROLL_SYNC_S
        v_ok = (
            pdy != 0
            and sdy != 0
            and abs(pdy) >= DUAL_SCROLL_MIN_DY
            and abs(sdy) >= DUAL_SCROLL_MIN_DY
        )
        aim_pct = float(
            clamp_gesture_aim_pct(
                int(
                    input_sensitivity.get(
                        "gesture_aim_pct", DEFAULT_GESTURE_AIM_PCT
                    )
                )
            )
        )
        # Treat at least 1 px of cross-axis motion so dx=0 / dy=0 cannot bypass
        # aim (old behavior: dx=0 always counted as vertical-dominant).
        ax = max(float(abs(dx)), 1.0)
        ay = max(float(abs(dy)), 1.0)
        # |dy|/|dx| must exceed aim_pct/100 for dominance when both axes move (100 ≈ 45°).
        vertical_dom = abs(dy) * 100.0 >= ax * aim_pct
        horizontal_dom = abs(dx) * 100.0 > ay * aim_pct

        def _aim_vert_contrib(dy: int, dx_cross: int) -> bool:
            """Same ratio as vertical_dom for one device's |dy| vs cross-axis |dx|."""
            ady = abs(int(dy))
            axc = max(float(abs(int(dx_cross))), 1.0)
            return ady * 100.0 >= axc * aim_pct

        def _aim_horiz_contrib(dx: int, dy_cross: int) -> bool:
            """Same ratio as horizontal_dom for one device's |dx| vs cross-axis |dy|."""
            adx = abs(int(dx))
            ayc = max(float(abs(int(dy_cross))), 1.0)
            return adx * 100.0 > ayc * aim_pct

        for zst in (st_hs, st_zi, st_zo):
            if now - float(zst["pri_t"]) > stale:
                zst["pri_dx"] = 0
            if now - float(zst["sec_t"]) > stale:
                zst["sec_dx"] = 0
        if dev_id == sec_dev:
            st_hs["sec_dx"] = dx
            st_hs["sec_t"] = now
            st_zi["sec_dx"] = dx
            st_zi["sec_t"] = now
            st_zo["sec_dx"] = dx
            st_zo["sec_t"] = now
        else:
            st_hs["pri_dx"] = dx
            st_hs["pri_t"] = now
            st_zi["pri_dx"] = dx
            st_zi["pri_t"] = now
            st_zo["pri_dx"] = dx
            st_zo["pri_t"] = now

        def mag_v() -> float:
            return float(min(abs(pdy), abs(sdy)))

        pdx_h = int(st_hs["pri_dx"])
        sdx_h = int(st_hs["sec_dx"])
        pth = float(st_hs["pri_t"])
        sth = float(st_hs["sec_t"])
        h_time = now - pth <= DUAL_SCROLL_SYNC_S and now - sth <= DUAL_SCROLL_SYNC_S
        h_ok = (
            pdx_h != 0
            and sdx_h != 0
            and abs(pdx_h) >= DUAL_HZOOM_MIN_DX
            and abs(sdx_h) >= DUAL_HZOOM_MIN_DX
        )

        pdx_zi = int(st_zi["pri_dx"])
        sdx_zi = int(st_zi["sec_dx"])
        zi_synced = (
            pdx_zi > 0
            and sdx_zi < 0
            and now - float(st_zi["pri_t"]) <= DUAL_SCROLL_SYNC_S
            and now - float(st_zi["sec_t"]) <= DUAL_SCROLL_SYNC_S
            and abs(pdx_zi) >= DUAL_HZOOM_MIN_DX
            and abs(sdx_zi) >= DUAL_HZOOM_MIN_DX
        )
        mag_zi = float(min(abs(pdx_zi), abs(sdx_zi))) if zi_synced else 0.0

        pdx_zo = int(st_zo["pri_dx"])
        sdx_zo = int(st_zo["sec_dx"])
        zo_synced = (
            pdx_zo < 0
            and sdx_zo > 0
            and now - float(st_zo["pri_t"]) <= DUAL_SCROLL_SYNC_S
            and now - float(st_zo["sec_t"]) <= DUAL_SCROLL_SYNC_S
            and abs(pdx_zo) >= DUAL_HZOOM_MIN_DX
            and abs(sdx_zo) >= DUAL_HZOOM_MIN_DX
        )
        mag_zo = float(min(abs(pdx_zo), abs(sdx_zo))) if zo_synced else 0.0

        spct_scroll = float(
            clamp(
                int(input_sensitivity.get("scroll_pct", DEFAULT_SCROLL_PCT)),
                MIN_SENSITIVITY_PCT,
                MAX_SENSITIVITY_PCT,
            )
        )
        spct_vol = float(
            clamp(
                int(input_sensitivity.get("volume_pct", DEFAULT_VOLUME_PCT)),
                MIN_SENSITIVITY_PCT,
                MAX_SENSITIVITY_PCT,
            )
        )
        spct_h = float(
            clamp(
                int(
                    input_sensitivity.get(
                        "scroll_horizontal_pct", DEFAULT_SCROLL_PCT
                    )
                ),
                MIN_SENSITIVITY_PCT,
                MAX_SENSITIVITY_PCT,
            )
        )
        spct_z = float(
            clamp(
                int(input_sensitivity.get("zoom_pct", DEFAULT_SCROLL_PCT)),
                MIN_SENSITIVITY_PCT,
                MAX_SENSITIVITY_PCT,
            )
        )
        spct_tab = float(
            clamp(
                int(input_sensitivity.get("tab_pct", DEFAULT_TAB_PCT)),
                MIN_SENSITIVITY_PCT,
                MAX_SENSITIVITY_PCT,
            )
        )
        spct_change_app = float(
            clamp(
                int(
                    input_sensitivity.get(
                        "change_app_pct", DEFAULT_CHANGE_APP_PCT
                    )
                ),
                MIN_SENSITIVITY_PCT,
                MAX_SENSITIVITY_PCT,
            )
        )
        gain_scroll = spct_scroll / 100.0 * WHEEL_ACCUM_GAIN
        gain_vol = spct_vol / 100.0 * WHEEL_ACCUM_GAIN
        gain_h = spct_h / 100.0 * WHEEL_ACCUM_GAIN
        gain_z = spct_z / 100.0 * WHEEL_ACCUM_GAIN
        gain_tab = spct_tab / 100.0 * WHEEL_ACCUM_GAIN
        gain_change_app = spct_change_app / 100.0 * WHEEL_ACCUM_GAIN

        used_v = False
        used_h = False
        used_zi = False
        used_zo = False
        used_vol = False
        used_tab_r = False
        used_tab_l = False
        used_app = False
        used_desktop = False

        def apply_action(action: int, mag: float) -> None:
            nonlocal used_v, used_h, used_zi, used_zo, used_vol, used_tab_r, used_tab_l
            nonlocal used_app, used_desktop
            if not profile_gestures_ok:
                return
            if action == 0 or mag <= 0:
                return
            if action == GESTURE_SCROLL_NORTH_ITEM:
                st_v["wheel_accum"] = float(st_v["wheel_accum"]) - mag * gain_scroll
                used_v = True
            elif action == GESTURE_SCROLL_SOUTH_ITEM:
                st_v["wheel_accum"] = float(st_v["wheel_accum"]) + mag * gain_scroll
                used_v = True
            elif action == GESTURE_SCROLL_WEST_ITEM:
                st_hscr["wheel_accum"] = float(st_hscr["wheel_accum"]) - mag * gain_h
                used_h = True
            elif action == GESTURE_SCROLL_EAST_ITEM:
                st_hscr["wheel_accum"] = float(st_hscr["wheel_accum"]) + mag * gain_h
                used_h = True
            elif action == GESTURE_ZOOM_IN_ITEM:
                nz = float(st_zi["wheel_accum"]) + mag * gain_z
                st_zi["wheel_accum"] = max(0.0, min(WHEEL_ACCUM_CAP, nz))
                used_zi = True
            elif action == GESTURE_ZOOM_OUT_ITEM:
                nz = float(st_zo["wheel_accum"]) + mag * gain_z
                st_zo["wheel_accum"] = max(0.0, min(WHEEL_ACCUM_CAP, nz))
                used_zo = True
            elif action == GESTURE_VOLUME_UP_ITEM:
                st_vol["accum"] = float(st_vol["accum"]) + mag * gain_vol
                used_vol = True
            elif action == GESTURE_VOLUME_DOWN_ITEM:
                st_vol["accum"] = float(st_vol["accum"]) - mag * gain_vol
                used_vol = True
            elif action == GESTURE_TAB_RIGHT_ITEM:
                st_tab["right_accum"] = float(st_tab["right_accum"]) + mag * gain_tab
                used_tab_r = True
            elif action == GESTURE_TAB_LEFT_ITEM:
                st_tab["left_accum"] = float(st_tab["left_accum"]) + mag * gain_tab
                used_tab_l = True
            elif action == GESTURE_CHANGE_APP_ITEM:
                st_app["accum"] = float(st_app["accum"]) + mag * gain_change_app
                used_app = True
            elif action == GESTURE_SHOW_DESKTOP_ITEM:
                st_desktop["accum"] = (
                    float(st_desktop["accum"]) + mag * gain_change_app
                )
                used_desktop = True
            elif action == GESTURE_SPLIT_MERGE_ITEM:
                now_sm = time.perf_counter()
                if mag < float(DUAL_SCROLL_MIN_DY):
                    return
                if now_sm < float(split_merge_gesture_cooldown_until[0]):
                    return
                split_merge_gesture_cooldown_until[0] = now_sm + 0.65
                if config_hwnd:
                    finish_split_merge_profile_transition(
                        config_hwnd, not dual_enabled[0]
                    )
            elif action == GESTURE_WARP_FIRST_ITEM:
                now_w = time.perf_counter()
                if mag < float(DUAL_HZOOM_MIN_DX):
                    return
                if now_w < float(warp_gesture_cooldown_until[0]):
                    return
                warp_gesture_cooldown_until[0] = now_w + 0.65
                warp_first_execute(config_hwnd)
            elif action == GESTURE_WARP_SECOND_ITEM:
                now_w2 = time.perf_counter()
                if mag < float(DUAL_HZOOM_MIN_DX):
                    return
                if now_w2 < float(warp_gesture_cooldown_until[0]):
                    return
                warp_gesture_cooldown_until[0] = now_w2 + 0.65
                warp_second_execute(config_hwnd)

        # Vertical row gestures only on vertical-dominant packets. Using only
        # "not horizontal_dom" lets both axes run when neither dominates (tiny
        # diagonal), so stale dy + active pinch both matched → scroll + zoom.
        if vertical_dom:
            if v_time and v_ok and pdy < 0 and sdy < 0:
                apply_action(gesture_sel.get(IDC_GESTURE_DOUBLE_UP, 0), mag_v())
            if v_time and v_ok and pdy > 0 and sdy > 0:
                apply_action(
                    gesture_sel.get(IDC_GESTURE_DOUBLE_DOWN, 0), mag_v()
                )
            # Require real vertical opposition on *both* mice — not secondary north +
            # primary "mostly west" with tiny +dy (that pattern is North-West, etc.).
            if (
                v_time
                and v_ok
                and sdy < 0
                and pdy > 0
                and _aim_vert_contrib(sdy, int(st_hs["sec_dx"]))
                and _aim_vert_contrib(pdy, int(st_hs["pri_dx"]))
            ):
                apply_action(gesture_sel.get(IDC_GESTURE_SHEAR, 0), mag_v())
            if (
                v_time
                and v_ok
                and sdy > 0
                and pdy < 0
                and _aim_vert_contrib(sdy, int(st_hs["sec_dx"]))
                and _aim_vert_contrib(pdy, int(st_hs["pri_dx"]))
            ):
                apply_action(
                    gesture_sel.get(IDC_GESTURE_REVERSE_SHEAR, 0), mag_v()
                )

        # Horizontal + zoom only on horizontal-dominant packets (same reason).
        if horizontal_dom:
            if h_time and h_ok and pdx_h < 0 and sdx_h < 0:
                apply_action(
                    gesture_sel.get(IDC_GESTURE_DOUBLE_LEFT, 0),
                    float(min(abs(pdx_h), abs(sdx_h))),
                )
            if h_time and h_ok and pdx_h > 0 and sdx_h > 0:
                apply_action(
                    gesture_sel.get(IDC_GESTURE_DOUBLE_RIGHT, 0),
                    float(min(abs(pdx_h), abs(sdx_h))),
                )
            if zi_synced:
                apply_action(gesture_sel.get(IDC_GESTURE_PULL, 0), mag_zi)
            if zo_synced:
                apply_action(gesture_sel.get(IDC_GESTURE_PINCH, 0), mag_zo)

        # Orthogonal pairs (secondary direction first in UI labels).
        _sync_g = DUAL_SCROLL_SYNC_S
        min_dy_g = float(DUAL_SCROLL_MIN_DY)
        min_dx_g = float(DUAL_HZOOM_MIN_DX)
        sne_time = (
            now - float(st["sec_t"]) <= _sync_g
            and now - float(st_hs["pri_t"]) <= _sync_g
        )
        epn_time = (
            now - float(st_hs["sec_t"]) <= _sync_g
            and now - float(st["pri_t"]) <= _sync_g
        )
        if sne_time:
            if (
                sdy < 0
                and abs(sdy) >= min_dy_g
                and pdx_h > 0
                and abs(pdx_h) >= min_dx_g
                and _aim_vert_contrib(sdy, int(st_hs["sec_dx"]))
                and _aim_horiz_contrib(pdx_h, int(st["pri_dy"]))
            ):
                apply_action(
                    gesture_sel.get(IDC_GESTURE_NORTH_EAST, 0),
                    float(min(abs(sdy), abs(pdx_h))),
                )
            if (
                sdy < 0
                and abs(sdy) >= min_dy_g
                and pdx_h < 0
                and abs(pdx_h) >= min_dx_g
                and _aim_vert_contrib(sdy, int(st_hs["sec_dx"]))
                and _aim_horiz_contrib(pdx_h, int(st["pri_dy"]))
            ):
                apply_action(
                    gesture_sel.get(IDC_GESTURE_NORTH_WEST, 0),
                    float(min(abs(sdy), abs(pdx_h))),
                )
            if (
                sdy > 0
                and abs(sdy) >= min_dy_g
                and pdx_h > 0
                and abs(pdx_h) >= min_dx_g
                and _aim_vert_contrib(sdy, int(st_hs["sec_dx"]))
                and _aim_horiz_contrib(pdx_h, int(st["pri_dy"]))
            ):
                apply_action(
                    gesture_sel.get(IDC_GESTURE_SOUTH_EAST, 0),
                    float(min(abs(sdy), abs(pdx_h))),
                )
            if (
                sdy > 0
                and abs(sdy) >= min_dy_g
                and pdx_h < 0
                and abs(pdx_h) >= min_dx_g
                and _aim_vert_contrib(sdy, int(st_hs["sec_dx"]))
                and _aim_horiz_contrib(pdx_h, int(st["pri_dy"]))
            ):
                apply_action(
                    gesture_sel.get(IDC_GESTURE_SOUTH_WEST, 0),
                    float(min(abs(sdy), abs(pdx_h))),
                )
        if epn_time:
            if (
                sdx_h > 0
                and abs(sdx_h) >= min_dx_g
                and pdy < 0
                and abs(pdy) >= min_dy_g
                and _aim_horiz_contrib(sdx_h, int(st["sec_dy"]))
                and _aim_vert_contrib(pdy, int(st_hs["pri_dx"]))
            ):
                apply_action(
                    gesture_sel.get(IDC_GESTURE_EAST_NORTH, 0),
                    float(min(abs(sdx_h), abs(pdy))),
                )
            if (
                sdx_h > 0
                and abs(sdx_h) >= min_dx_g
                and pdy > 0
                and abs(pdy) >= min_dy_g
                and _aim_horiz_contrib(sdx_h, int(st["sec_dy"]))
                and _aim_vert_contrib(pdy, int(st_hs["pri_dx"]))
            ):
                apply_action(
                    gesture_sel.get(IDC_GESTURE_EAST_SOUTH, 0),
                    float(min(abs(sdx_h), abs(pdy))),
                )
            if (
                sdx_h < 0
                and abs(sdx_h) >= min_dx_g
                and pdy < 0
                and abs(pdy) >= min_dy_g
                and _aim_horiz_contrib(sdx_h, int(st["sec_dy"]))
                and _aim_vert_contrib(pdy, int(st_hs["pri_dx"]))
            ):
                apply_action(
                    gesture_sel.get(IDC_GESTURE_WEST_NORTH, 0),
                    float(min(abs(sdx_h), abs(pdy))),
                )
            if (
                sdx_h < 0
                and abs(sdx_h) >= min_dx_g
                and pdy > 0
                and abs(pdy) >= min_dy_g
                and _aim_horiz_contrib(sdx_h, int(st["sec_dy"]))
                and _aim_vert_contrib(pdy, int(st_hs["pri_dx"]))
            ):
                apply_action(
                    gesture_sel.get(IDC_GESTURE_WEST_SOUTH, 0),
                    float(min(abs(sdx_h), abs(pdy))),
                )

        # Decay accumulators when the other axis is active but didn't use that channel.
        if vertical_dom and not used_v:
            st_v["wheel_accum"] = float(st_v["wheel_accum"]) * WHEEL_ACCUM_DECAY
        elif horizontal_dom and not used_v:
            st_v["wheel_accum"] = float(st_v["wheel_accum"]) * WHEEL_ACCUM_DECAY
        if horizontal_dom and not used_h:
            st_hscr["wheel_accum"] = float(st_hscr["wheel_accum"]) * WHEEL_ACCUM_DECAY
        elif vertical_dom and not used_h:
            st_hscr["wheel_accum"] = float(st_hscr["wheel_accum"]) * WHEEL_ACCUM_DECAY
        if horizontal_dom and not used_zi:
            st_zi["wheel_accum"] = float(st_zi["wheel_accum"]) * WHEEL_ACCUM_DECAY
        elif vertical_dom and not used_zi:
            st_zi["wheel_accum"] = float(st_zi["wheel_accum"]) * WHEEL_ACCUM_DECAY
        if horizontal_dom and not used_zo:
            st_zo["wheel_accum"] = float(st_zo["wheel_accum"]) * WHEEL_ACCUM_DECAY
        elif vertical_dom and not used_zo:
            st_zo["wheel_accum"] = float(st_zo["wheel_accum"]) * WHEEL_ACCUM_DECAY
        if vertical_dom and not used_vol:
            st_vol["accum"] = float(st_vol["accum"]) * WHEEL_ACCUM_DECAY
        elif horizontal_dom and not used_vol:
            st_vol["accum"] = float(st_vol["accum"]) * WHEEL_ACCUM_DECAY
        if vertical_dom and not used_tab_r:
            st_tab["right_accum"] = float(st_tab["right_accum"]) * WHEEL_ACCUM_DECAY
        elif horizontal_dom and not used_tab_r:
            st_tab["right_accum"] = float(st_tab["right_accum"]) * WHEEL_ACCUM_DECAY
        if vertical_dom and not used_tab_l:
            st_tab["left_accum"] = float(st_tab["left_accum"]) * WHEEL_ACCUM_DECAY
        elif horizontal_dom and not used_tab_l:
            st_tab["left_accum"] = float(st_tab["left_accum"]) * WHEEL_ACCUM_DECAY
        if vertical_dom and not used_app:
            st_app["accum"] = float(st_app["accum"]) * WHEEL_ACCUM_DECAY
        elif horizontal_dom and not used_app:
            st_app["accum"] = float(st_app["accum"]) * WHEEL_ACCUM_DECAY
        if vertical_dom and not used_desktop:
            st_desktop["accum"] = float(st_desktop["accum"]) * WHEEL_ACCUM_DECAY
        elif horizontal_dom and not used_desktop:
            st_desktop["accum"] = float(st_desktop["accum"]) * WHEEL_ACCUM_DECAY

        st_v["wheel_accum"] = max(
            -WHEEL_ACCUM_CAP,
            min(WHEEL_ACCUM_CAP, float(st_v["wheel_accum"])),
        )
        st_hscr["wheel_accum"] = max(
            -WHEEL_ACCUM_CAP,
            min(WHEEL_ACCUM_CAP, float(st_hscr["wheel_accum"])),
        )
        st_vol["accum"] = max(
            -WHEEL_ACCUM_CAP,
            min(WHEEL_ACCUM_CAP, float(st_vol["accum"])),
        )
        st_tab["right_accum"] = max(
            0.0,
            min(WHEEL_ACCUM_CAP, float(st_tab["right_accum"])),
        )
        st_tab["left_accum"] = max(
            0.0,
            min(WHEEL_ACCUM_CAP, float(st_tab["left_accum"])),
        )
        st_app["accum"] = max(
            0.0,
            min(WHEEL_ACCUM_CAP, float(st_app["accum"])),
        )
        st_desktop["accum"] = max(
            0.0,
            min(WHEEL_ACCUM_CAP, float(st_desktop["accum"])),
        )

        gesture_feeding = (
            used_v
            or used_h
            or used_zi
            or used_zo
            or used_vol
            or used_tab_r
            or used_tab_l
            or used_app
            or used_desktop
        )

        chunk = max(1, int(WHEEL_SMOOTH_CHUNK))
        # Plain vertical wheel runs without Ctrl; drop it whenever Ctrl+wheel zoom
        # will inject this pass (avoids scroll-down ticks separate from zoom-out).
        if (
            float(st_zi["wheel_accum"]) >= chunk
            or float(st_zo["wheel_accum"]) >= chunk
        ):
            st_v["wheel_accum"] = 0.0

        injected_any = False

        acc_h = float(st_hscr["wheel_accum"])
        burst_h = 0
        while burst_h < WHEEL_SMOOTH_MAX_BURST and abs(acc_h) >= chunk:
            if acc_h < 0:
                inject_mouse_hwheel_delta(-chunk)
                acc_h += chunk
            else:
                inject_mouse_hwheel_delta(chunk)
                acc_h -= chunk
            burst_h += 1
            injected_any = True
        st_hscr["wheel_accum"] = acc_h

        tchunk = max(1, int(TAB_ACCUM_CHUNK))
        acc_tr = float(st_tab["right_accum"])
        burst_tab = 0
        while burst_tab < TAB_ACCUM_MAX_BURST and acc_tr >= tchunk:
            inject_tab_right()
            acc_tr -= tchunk
            burst_tab += 1
            injected_any = True
        st_tab["right_accum"] = acc_tr

        acc_tl = float(st_tab["left_accum"])
        burst_tab = 0
        while burst_tab < TAB_ACCUM_MAX_BURST and acc_tl >= tchunk:
            inject_tab_left()
            acc_tl -= tchunk
            burst_tab += 1
            injected_any = True
        st_tab["left_accum"] = acc_tl

        acc_app = float(st_app["accum"])
        burst_app = 0
        while burst_app < TAB_ACCUM_MAX_BURST and acc_app >= tchunk:
            inject_change_application()
            acc_app -= tchunk
            burst_app += 1
            injected_any = True
        st_app["accum"] = acc_app

        acc_sd = float(st_desktop["accum"])
        burst_sd = 0
        while burst_sd < TAB_ACCUM_MAX_BURST and acc_sd >= tchunk:
            inject_show_desktop()
            acc_sd -= tchunk
            burst_sd += 1
            injected_any = True
        st_desktop["accum"] = acc_sd

        acc_zi = float(st_zi["wheel_accum"])
        burst = 0
        while burst < WHEEL_SMOOTH_MAX_BURST and acc_zi >= chunk:
            inject_ctrl_wheel_zoom_in_burst(chunk)
            acc_zi -= chunk
            burst += 1
            injected_any = True
        st_zi["wheel_accum"] = acc_zi

        acc_zo = float(st_zo["wheel_accum"])
        burst = 0
        while burst < WHEEL_SMOOTH_MAX_BURST and acc_zo >= chunk:
            inject_ctrl_wheel_zoom_out_burst(chunk)
            acc_zo -= chunk
            burst += 1
            injected_any = True
        st_zo["wheel_accum"] = acc_zo

        vchunk = max(1, int(VOLUME_ACCUM_CHUNK))
        acc_vol = float(st_vol["accum"])
        burst_vol = 0
        while burst_vol < VOLUME_ACCUM_MAX_BURST and abs(acc_vol) >= vchunk:
            if acc_vol > 0:
                inject_volume_up()
                acc_vol -= vchunk
            else:
                inject_volume_down()
                acc_vol += vchunk
            burst_vol += 1
            injected_any = True
        st_vol["accum"] = acc_vol

        acc = float(st_v["wheel_accum"])
        burst = 0
        while burst < WHEEL_SMOOTH_MAX_BURST and abs(acc) >= chunk:
            if acc < 0:
                inject_mouse_wheel_delta(chunk)
                acc += chunk
            else:
                inject_mouse_wheel_delta(-chunk)
                acc -= chunk
            burst += 1
            injected_any = True
        st_v["wheel_accum"] = acc

        return injected_any or gesture_feeding

    def scroll_movement_lock_active() -> bool:
        return time.perf_counter() < float(scroll_move_lock["until"])

    def ensure_ptr_pair_for_scroll_snapshot() -> None:
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        cx, cy = int(pt.x), int(pt.y)
        if ptr_state["p_pri"] is None:
            ptr_state["p_pri"] = (cx, cy)
        if ptr_state["p_sec"] is None:
            ptr_state["p_sec"] = (ptr_state["p_pri"][0], ptr_state["p_pri"][1])

    def note_scroll_inject_freeze_pointers() -> None:
        """After a dual-mice gesture inject or while feeding: snapshot pointers and extend the move lock."""
        now = time.perf_counter()
        lk = scroll_move_lock
        if now >= float(lk["until"]):
            if dual_enabled[0]:
                ensure_ptr_pair_for_scroll_snapshot()
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            lk["anchor_pri"] = (int(pt.x), int(pt.y))
            if dual_enabled[0]:
                lk["anchor_sec"] = ptr_state["p_sec"]
            else:
                lk["anchor_sec"] = None
        lk["until"] = max(float(lk["until"]), now + SCROLL_MOVE_LOCK_S)

    def restore_scroll_freeze_pointers() -> None:
        """Snap primary (and 2nd cursor in split mode) back to pre-scroll anchors."""
        lk = scroll_move_lock
        ap = lk.get("anchor_pri")
        if ap is None:
            return
        ptr_state["p_pri"] = ap
        user32.SetCursorPos(int(ap[0]), int(ap[1]))
        asp = lk.get("anchor_sec")
        if asp is not None and dual_enabled[0]:
            ptr_state["p_sec"] = asp
        sync_secondary_overlay()

    def consume_scroll_move_lock_if_active(gesture_locks_pointer: bool) -> bool:
        """If a gesture is active/injected or lock is still active, snap pointers and return True."""
        if gesture_locks_pointer:
            note_scroll_inject_freeze_pointers()
        if scroll_movement_lock_active():
            restore_scroll_freeze_pointers()
            return True
        return False

    def clamp_secondary_index() -> None:
        n = len(device_order)
        if n < 2:
            return
        i = secondary_mouse_idx[0]
        if i < 0 or i >= n:
            secondary_mouse_idx[0] = min(1, n - 1)

    def get_secondary_dev() -> int | None:
        if len(device_order) < 2:
            return None
        clamp_secondary_index()
        return device_order[secondary_mouse_idx[0]]

    def _combo_add_string(cb_hwnd: int, text: str) -> None:
        ws = ctypes.c_wchar_p(text)
        user32.SendMessageW(
            cb_hwnd,
            CB_ADDSTRING,
            0,
            ctypes.cast(ws, ctypes.c_void_p).value or 0,
        )

    def refresh_secondary_mouse_combo(parent_hwnd: int) -> None:
        cb = user32.GetDlgItem(parent_hwnd, IDC_SECONDARY_MOUSE_COMBO)
        if not cb:
            return
        clamp_secondary_index()
        n = len(device_order)
        user32.SendMessageW(cb, CB_RESETCONTENT, 0, 0)
        if n == 0:
            _combo_add_string(cb, "Move a mouse to detect devices…")
            user32.EnableWindow(cb, False)
            user32.SendMessageW(cb, CB_SETCURSEL, 0, 0)
            return
        for dev in device_order:
            _combo_add_string(cb, raw_mouse_device_caption(dev))
        user32.EnableWindow(cb, n >= 2)
        if n < 2:
            user32.SendMessageW(cb, CB_SETCURSEL, 0, 0)
            return
        user32.SendMessageW(cb, CB_SETCURSEL, secondary_mouse_idx[0], 0)

    WNDPROC = ctypes.WINFUNCTYPE(
        wintypes.LRESULT,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    HOOKPROC_LL = ctypes.WINFUNCTYPE(
        wintypes.LRESULT,
        ctypes.c_int,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )

    def ghost_wnd_proc(hwnd, msg, wparam, lparam):
        # Visuals come from UpdateLayeredWindow, not GDI painting.
        if msg == WM_NCPAINT:
            return 0
        if msg == WM_ERASEBKGND:
            return 1
        if msg == WM_PAINT:
            ps = PAINTSTRUCT()
            user32.BeginPaint(hwnd, ctypes.byref(ps))
            user32.EndPaint(hwnd, ctypes.byref(ps))
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def split_snap_anim_cancel() -> None:
        sa = split_snap_anim
        th = int(sa.get("timer_hwnd") or 0)
        if th:
            user32.KillTimer(th, SPLIT_SNAP_TIMER_ID)
        sa.pop("anchor_x", None)
        sa.pop("anchor_y", None)
        sa["active"] = False
        sa["timer_hwnd"] = 0
        sa["off_x"] = 0.0
        sa["off_y"] = 0.0
        sa["vx"] = 0.0
        sa["vy"] = 0.0
        sa.pop("reverse", None)
        _split_fx_hide(overlay_state)

    def hide_ghost_visual() -> None:
        """Hide the 2nd-cursor layered window only; does not stop split/merge shockwave FX."""
        oh = overlay_state.get("hwnd")
        if oh and ghost_overlay_cache["shown"]:
            user32.ShowWindow(oh, SW_HIDE)
        ghost_overlay_cache["shown"] = False
        ghost_overlay_cache["x"] = None
        ghost_overlay_cache["y"] = None
        ghost_overlay_cache["side"] = None

    def ghost_overlay_hide() -> None:
        split_snap_anim_cancel()
        hide_ghost_visual()

    def init_split_cursor_positions_from_primary() -> None:
        """On entering split mode, start the 2nd cursor at the current primary cursor (Windows)."""
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        cx, cy = int(pt.x), int(pt.y)
        ptr_state["p_pri"] = (cx, cy)
        ptr_state["p_sec"] = (cx, cy)

    def sync_secondary_overlay() -> None:
        oh = overlay_state.get("hwnd")
        if not oh:
            return

        if not dual_enabled[0]:
            hide_ghost_visual()
            return

        if len(device_order) < 2 or ptr_state["p_sec"] is None:
            ghost_overlay_hide()
            return

        sx, sy = ptr_state["p_sec"]
        side = int(overlay_state.get("ghost_size", DEFAULT_GHOST_SIZE_PX))
        half = side // 2
        ox, oy = sx - half, sy - half

        prev_side = ghost_overlay_cache.get("side")
        size_changed = prev_side is None or int(prev_side) != side
        was_shown = bool(ghost_overlay_cache["shown"])

        def pin_ghost_topmost_zorder_only() -> None:
            """HWND_TOPMOST is relative; other topmost windows can slide above us unless we re-pin."""
            user32.SetWindowPos(
                oh,
                ctypes.c_void_p(HWND_TOPMOST),
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )

        if (
            was_shown
            and ghost_overlay_cache["x"] == ox
            and ghost_overlay_cache["y"] == oy
            and not size_changed
        ):
            pin_ghost_topmost_zorder_only()
            return

        # First show or size change: move/size + topmost. Position-only moves: ULW for
        # pixels, but still re-pin Z (UpdateLayeredWindow alone does not restore order).
        if not was_shown or size_changed:
            user32.SetWindowPos(
                oh,
                ctypes.c_void_p(HWND_TOPMOST),
                ox,
                oy,
                side,
                side,
                SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
        else:
            pin_ghost_topmost_zorder_only()

        _present_layered_ghost(overlay_state, ox, oy)

        # Split FX is driven only by WM_TIMER (see split_snap_anim_step) so Raw Input
        # is not contending with full FX+ghost work every ~16 ms.
        if not split_snap_anim["active"] or "anchor_x" not in split_snap_anim:
            _split_fx_hide(overlay_state)

        ghost_overlay_cache["shown"] = True
        ghost_overlay_cache["x"] = ox
        ghost_overlay_cache["y"] = oy
        ghost_overlay_cache["side"] = side

    def split_snap_anim_begin(timer_hwnd: int) -> None:
        if not ring_animations_enabled[0]:
            return
        if not timer_hwnd or not dual_enabled[0]:
            return
        if len(device_order) < 2:
            split_snap_anim_cancel()
            return
        split_snap_anim_cancel()
        sa = split_snap_anim
        ps = ptr_state["p_sec"]
        if ps is None:
            return
        sa["anchor_x"] = int(ps[0])
        sa["anchor_y"] = int(ps[1])
        sa["active"] = True
        sa["timer_hwnd"] = timer_hwnd
        now = time.perf_counter()
        sa["start_t"] = now
        sa["last_t"] = now
        sa["off_x"] = 0.0
        sa["off_y"] = 0.0
        sa["vx"] = float(SPLIT_SNAP_IMPULSE_VX)
        sa["vy"] = float(SPLIT_SNAP_IMPULSE_VY)
        sa["reverse"] = False
        _ensure_split_fx_layer(overlay_state)
        user32.SetTimer(timer_hwnd, SPLIT_SNAP_TIMER_ID, 16, None)
        _sync_split_fx_overlay_only(overlay_state, split_snap_anim)

    def merge_snap_anim_begin(timer_hwnd: int, cx: int, cy: int) -> None:
        """Shockwave contracting at merged cursor (split → merged); same timer as split snap."""
        if not ring_animations_enabled[0]:
            return
        if not timer_hwnd:
            return
        split_snap_anim_cancel()
        sa = split_snap_anim
        sa["anchor_x"] = int(cx)
        sa["anchor_y"] = int(cy)
        sa["active"] = True
        sa["reverse"] = True
        sa["timer_hwnd"] = timer_hwnd
        now = time.perf_counter()
        sa["start_t"] = now
        sa["last_t"] = now
        sa["off_x"] = 0.0
        sa["off_y"] = 0.0
        sa["vx"] = float(SPLIT_SNAP_IMPULSE_VX)
        sa["vy"] = float(SPLIT_SNAP_IMPULSE_VY)
        _ensure_split_fx_layer(overlay_state)
        user32.SetTimer(timer_hwnd, SPLIT_SNAP_TIMER_ID, 16, None)
        _sync_split_fx_overlay_only(overlay_state, split_snap_anim)

    def split_snap_anim_step() -> None:
        sa = split_snap_anim
        if not sa["active"]:
            return
        now = time.perf_counter()
        dt_raw = now - float(sa["last_t"])
        sa["last_t"] = now
        dt = min(max(dt_raw, 0.0), float(SPLIT_SNAP_DT_CAP_S))
        if dt <= 0.0:
            return
        x = float(sa["off_x"])
        y = float(sa["off_y"])
        vx = float(sa["vx"])
        vy = float(sa["vy"])
        k = float(SPLIT_SNAP_STIFFNESS)
        c = float(SPLIT_SNAP_DAMPING)
        ax = -k * x - c * vx
        ay = -k * y - c * vy
        vx += ax * dt
        vy += ay * dt
        x += vx * dt
        y += vy * dt
        sa["off_x"], sa["off_y"] = x, y
        sa["vx"], sa["vy"] = vx, vy
        elapsed = now - float(sa["start_t"])
        spd2 = vx * vx + vy * vy
        settled = (
            abs(x) < 0.45
            and abs(y) < 0.45
            and spd2 < 100.0
            and elapsed > 0.06
        )
        rip_done_s = (
            float(MERGE_SNAP_RIPPLE_DURATION_S)
            if sa.get("reverse")
            else float(SPLIT_SNAP_RIPPLE_DURATION_S)
        )
        ripple_done = elapsed >= rip_done_s + 0.02
        if settled or elapsed >= float(SPLIT_SNAP_MAX_S) or ripple_done:
            split_snap_anim_cancel()
        else:
            _sync_split_fx_overlay_only(overlay_state, split_snap_anim)

    def refresh_split_merge_checkbox(parent_hwnd: int) -> None:
        """Checked = split (2nd cursor); unchecked = merged (single cursor)."""
        btn = user32.GetDlgItem(parent_hwnd, IDC_TOGGLE_POINTERS)
        if btn:
            user32.SetWindowTextW(
                btn, ccos_input_label("split/merge", IDC_TOGGLE_POINTERS)
            )
            user32.SendMessageW(
                btn,
                BM_SETCHECK,
                BST_CHECKED if dual_enabled[0] else BST_UNCHECKED,
                0,
            )

    def refresh_duo_shift_checkbox(parent_hwnd: int) -> None:
        btn = user32.GetDlgItem(parent_hwnd, IDC_DUO_SHIFT_TOGGLE)
        if btn:
            user32.SendMessageW(
                btn,
                BM_SETCHECK,
                BST_CHECKED if duo_shift[0] else BST_UNCHECKED,
                0,
            )

    def refresh_ring_animations_checkbox(parent_hwnd: int) -> None:
        btn = user32.GetDlgItem(parent_hwnd, IDC_RING_ANIMATIONS_TOGGLE)
        if btn:
            user32.SendMessageW(
                btn,
                BM_SETCHECK,
                BST_CHECKED if ring_animations_enabled[0] else BST_UNCHECKED,
                0,
            )

    def save_live_profile_to_slot(slot: int) -> None:
        profile_store[slot]["input_sensitivity"] = copy.deepcopy(input_sensitivity)
        profile_store[slot]["gesture_sel"] = copy.deepcopy(gesture_sel)
        profile_store[slot]["ring_animations"] = bool(ring_animations_enabled[0])

    def load_profile_slot_into_live(slot: int) -> None:
        ins = copy.deepcopy(profile_store[slot]["input_sensitivity"])
        migrate_input_sensitivity_dict(ins)
        profile_store[slot]["input_sensitivity"] = ins
        ges = copy.deepcopy(profile_store[slot]["gesture_sel"])
        input_sensitivity.clear()
        input_sensitivity.update(ins)
        gesture_sel.clear()
        gesture_sel.update(ges)
        new_ra = bool(profile_store[slot].get("ring_animations", True))
        if ring_animations_enabled[0] and not new_ra:
            split_snap_anim_cancel()
        ring_animations_enabled[0] = new_ra

    def arm_profile_change_gesture_suppress() -> None:
        """Call when the active profile slot changes (not on first WM_CREATE load)."""
        _ms = clamp_profile_gesture_cooldown_ms(
            int(
                input_sensitivity.get(
                    "profile_gesture_cooldown_ms",
                    DEFAULT_PROFILE_GESTURE_COOLDOWN_MS,
                )
            )
        )
        if _ms > 0:
            profile_change_gesture_suppress_until[0] = (
                time.perf_counter() + _ms / 1000.0
            )
        else:
            profile_change_gesture_suppress_until[0] = 0.0
        dual_vertical_scroll["pri_dy"] = 0
        dual_vertical_scroll["sec_dy"] = 0
        dual_vertical_scroll["wheel_accum"] = 0.0
        dual_vertical_scroll["pri_t"] = 0.0
        dual_vertical_scroll["sec_t"] = 0.0
        dual_horizontal_scroll["wheel_accum"] = 0.0
        for _st in (
            dual_horizontal_same,
            dual_horizontal_zoom,
            dual_horizontal_zoom_out,
        ):
            _st["pri_dx"] = 0
            _st["sec_dx"] = 0
            _st["pri_t"] = 0.0
            _st["sec_t"] = 0.0
        dual_horizontal_zoom["wheel_accum"] = 0.0
        dual_horizontal_zoom_out["wheel_accum"] = 0.0
        dual_volume["accum"] = 0.0
        dual_tab["right_accum"] = 0.0
        dual_tab["left_accum"] = 0.0
        dual_change_app["accum"] = 0.0
        dual_show_desktop["accum"] = 0.0

    def finish_split_merge_profile_transition(
        parent_hwnd: int, new_split: bool
    ) -> None:
        """Save current profile, apply split flag, load matching profile slot, refresh UI."""
        old_idx = profile_idx_from_flags(dual_enabled[0], duo_shift[0])
        save_live_profile_to_slot(old_idx)
        dual_enabled[0] = new_split
        new_idx = profile_idx_from_flags(dual_enabled[0], duo_shift[0])
        current_profile_idx[0] = new_idx
        load_profile_slot_into_live(new_idx)
        arm_profile_change_gesture_suppress()
        refresh_sensitivity_edits(parent_hwnd)
        refresh_gesture_combos(parent_hwnd)
        refresh_split_merge_checkbox(parent_hwnd)
        refresh_duo_shift_checkbox(parent_hwnd)
        refresh_ring_animations_checkbox(parent_hwnd)
        set_profile_combo_sel(parent_hwnd, new_idx)
        if not dual_enabled[0]:
            pt_merge = POINT()
            user32.GetCursorPos(ctypes.byref(pt_merge))
            mx, my = int(pt_merge.x), int(pt_merge.y)
            ghost_overlay_hide()
            ptr_state["p_pri"] = None
            ptr_state["p_sec"] = None
            merge_snap_anim_begin(parent_hwnd, mx, my)
        else:
            init_split_cursor_positions_from_primary()
            split_snap_anim_begin(parent_hwnd)
            sync_secondary_overlay()

    def warp_first_execute(parent_hwnd: int | None) -> None:
        """Warp 1st: split → move to primary then merge; merged → jump to 4th invisible pin."""
        if dual_enabled[0]:
            if ptr_state["p_pri"] is None:
                return
            px, py = ptr_state["p_pri"]
            user32.SetCursorPos(int(px), int(py))
            ptr_state["p_sec"] = (int(px), int(py))
            if parent_hwnd:
                finish_split_merge_profile_transition(parent_hwnd, False)
        else:
            pin_pt = pinned_pri_warp_point[0]
            if pin_pt is None:
                return
            wx, wy = pin_pt
            ptr_state["p_pri"] = (wx, wy)
            user32.SetCursorPos(int(wx), int(wy))
            if parent_hwnd:
                merge_snap_anim_begin(parent_hwnd, int(wx), int(wy))

    def warp_second_execute(parent_hwnd: int | None) -> None:
        """Warp 2nd: split → move primary to 2nd cursor then merge; merged → jump to 3rd pin."""
        if dual_enabled[0]:
            if ptr_state["p_sec"] is None:
                return
            sx, sy = ptr_state["p_sec"]
            ptr_state["p_pri"] = (int(sx), int(sy))
            user32.SetCursorPos(int(sx), int(sy))
            if parent_hwnd:
                finish_split_merge_profile_transition(parent_hwnd, False)
        else:
            pin_pt = pinned_sec_warp_point[0]
            if pin_pt is None:
                return
            wx, wy = pin_pt
            ptr_state["p_pri"] = (wx, wy)
            user32.SetCursorPos(int(wx), int(wy))
            if parent_hwnd:
                merge_snap_anim_begin(parent_hwnd, int(wx), int(wy))

    def refresh_sensitivity_edits(parent_hwnd: int) -> None:
        pairs = (
            (IDC_SENS_MOVE_PRIMARY_EDIT, "move_primary_pct", DEFAULT_SENSITIVITY_PCT),
            (
                IDC_SENS_MOVE_SECONDARY_EDIT,
                "move_secondary_pct",
                DEFAULT_SENSITIVITY_PCT,
            ),
            (IDC_SENS_SCROLL_EDIT, "scroll_pct", DEFAULT_SCROLL_PCT),
            (
                IDC_SENS_SCROLL_H_EDIT,
                "scroll_horizontal_pct",
                DEFAULT_SCROLL_PCT,
            ),
            (IDC_SENS_ZOOM_EDIT, "zoom_pct", DEFAULT_SCROLL_PCT),
            (IDC_SENS_VOLUME_EDIT, "volume_pct", DEFAULT_VOLUME_PCT),
            (IDC_SENS_TAB_EDIT, "tab_pct", DEFAULT_TAB_PCT),
            (
                IDC_SENS_CHANGE_APP_EDIT,
                "change_app_pct",
                DEFAULT_CHANGE_APP_PCT,
            ),
            (
                IDC_SENS_GESTURE_AIM_EDIT,
                "gesture_aim_pct",
                DEFAULT_GESTURE_AIM_PCT,
            ),
            (
                IDC_SENS_PROFILE_GESTURE_COOLDOWN_EDIT,
                "profile_gesture_cooldown_ms",
                DEFAULT_PROFILE_GESTURE_COOLDOWN_MS,
            ),
        )
        for idc, key, dfl in pairs:
            ed = user32.GetDlgItem(parent_hwnd, idc)
            if ed:
                v = int(input_sensitivity.get(key, dfl))
                if key == "gesture_aim_pct":
                    v = clamp_gesture_aim_pct(v)
                elif key == "profile_gesture_cooldown_ms":
                    v = clamp_profile_gesture_cooldown_ms(v)
                user32.SetWindowTextW(ed, str(v))

    def refresh_gesture_combos(parent_hwnd: int) -> None:
        for _idc in ALL_GESTURE_IDCS:
            _gcb = user32.GetDlgItem(parent_hwnd, _idc)
            if not _gcb:
                continue
            _gsel = int(gesture_sel.get(_idc, 0))
            user32.SendMessageW(_gcb, CB_SETCURSEL, _gsel, 0)

    def set_profile_combo_sel(parent_hwnd: int, slot: int) -> None:
        pcb = user32.GetDlgItem(parent_hwnd, IDC_PROFILE_COMBO)
        if not pcb:
            return
        profile_combo_suppress[0] = True
        user32.SendMessageW(pcb, CB_SETCURSEL, slot, 0)
        profile_combo_suppress[0] = False

    def refresh_ghost_rgb_label(parent_hwnd: int) -> None:
        st = user32.GetDlgItem(parent_hwnd, IDC_GHOST_RGB_STATIC)
        if not st:
            return
        tr, tg, tb = overlay_state.get("ghost_tint", (220, 235, 255))
        user32.SetWindowTextW(st, f"RGB {tr}, {tg}, {tb}")

    def read_ghost_size_from_edit(parent_hwnd: int) -> int:
        ed = user32.GetDlgItem(parent_hwnd, IDC_GHOST_SIZE_EDIT)
        if not ed:
            return int(overlay_state.get("ghost_size", DEFAULT_GHOST_SIZE_PX))
        buf = ctypes.create_unicode_buffer(32)
        user32.GetWindowTextW(ed, buf, 32)
        try:
            v = int(buf.value.strip())
        except ValueError:
            v = int(overlay_state.get("ghost_size", DEFAULT_GHOST_SIZE_PX))
        return clamp(v, MIN_GHOST_SIZE_PX, MAX_GHOST_SIZE_PX)

    def normalize_sensitivity_edit(parent_hwnd: int, idc: int, key: str) -> None:
        ed = user32.GetDlgItem(parent_hwnd, idc)
        if not ed:
            return
        buf = ctypes.create_unicode_buffer(16)
        user32.GetWindowTextW(ed, buf, 16)
        try:
            v = int(buf.value.strip())
        except ValueError:
            v = int(input_sensitivity[key])
        if key == "gesture_aim_pct":
            v = clamp_gesture_aim_pct(v)
        elif key == "profile_gesture_cooldown_ms":
            v = clamp_profile_gesture_cooldown_ms(v)
        else:
            v = clamp(v, MIN_SENSITIVITY_PCT, MAX_SENSITIVITY_PCT)
        input_sensitivity[key] = v
        user32.SetWindowTextW(ed, str(v))

    def move_sensitivity_pct(_is_split: bool, device_is_secondary: bool) -> int:
        """Move % for this profile; primary vs 2nd device (split/merged use same row)."""
        k = "move_secondary_pct" if device_is_secondary else "move_primary_pct"
        return clamp(
            int(input_sensitivity.get(k, DEFAULT_SENSITIVITY_PCT)),
            MIN_SENSITIVITY_PCT,
            MAX_SENSITIVITY_PCT,
        )

    def pick_ghost_color(parent_hwnd: int) -> None:
        tr, tg, tb = overlay_state.get("ghost_tint", (220, 235, 255))
        orig = (int(tr), int(tg), int(tb))
        last_applied = [orig]

        cc = CHOOSECOLOR()
        cc.lStructSize = ctypes.sizeof(CHOOSECOLOR)
        cc.hwndOwner = parent_hwnd
        cc.hInstance = hinst
        cc.rgbResult = _rgb_to_colorref(tr, tg, tb)
        cc.lpCustColors = ctypes.cast(choosecolor_custom, ctypes.POINTER(wintypes.DWORD))
        cc.lCustData = None
        cc.lpTemplateName = None

        choosecolor_sync["cc"] = cc
        choosecolor_sync["last_rgb"] = last_applied

        def color_hook(_hdlg, msg, _wp, _lp):
            if msg == WM_INITDIALOG:
                return 0
            # Hook runs before the dialog updates cc.rgbResult; defer read to parent.
            if msg in (WM_COMMAND, WM_HSCROLL, WM_VSCROLL):
                user32.PostMessageW(parent_hwnd, WM_GHOST_COLOR_SYNC, 0, 0)
            return 0

        cb = LPCCHOOKPROC(color_hook)
        cc.lpfnHook = ctypes.cast(cb, ctypes.c_void_p)
        cc.Flags = CC_RGBINIT | CC_FULLOPEN | CC_ANYCOLOR | CC_ENABLEHOOK

        ok = bool(comdlg32.ChooseColorW(ctypes.byref(cc)))
        choosecolor_sync["cc"] = None
        choosecolor_sync["last_rgb"] = None

        if ok:
            r, g, b = _colorref_to_rgb(cc.rgbResult)
            t = (r, g, b)
            overlay_state["ghost_tint"] = t
            refresh_ghost_rgb_label(parent_hwnd)
            if t != last_applied[0]:
                apply_ghost_appearance(parent_hwnd)
        else:
            overlay_state["ghost_tint"] = orig
            refresh_ghost_rgb_label(parent_hwnd)
            apply_ghost_appearance(parent_hwnd)

    def apply_ghost_appearance(parent_hwnd: int) -> None:
        split_snap_anim_cancel()
        new_sz = read_ghost_size_from_edit(parent_hwnd)
        overlay_state["ghost_size"] = new_sz
        ed = user32.GetDlgItem(parent_hwnd, IDC_GHOST_SIZE_EDIT)
        if ed:
            user32.SetWindowTextW(ed, str(new_sz))
        oh = overlay_state.get("hwnd")
        _teardown_ghost_layer_assets(overlay_state)
        if oh:
            user32.SetWindowPos(
                oh,
                ctypes.c_void_p(HWND_TOPMOST),
                0,
                0,
                new_sz,
                new_sz,
                SWP_NOACTIVATE | SWP_NOMOVE,
            )
        if not _build_ghost_layer_assets(overlay_state):
            print("2nd cursor rebuild failed:", kernel32.GetLastError())
            return
        ghost_overlay_cache["shown"] = False
        ghost_overlay_cache["x"] = None
        ghost_overlay_cache["y"] = None
        ghost_overlay_cache["side"] = None
        sync_secondary_overlay()

    def pin_first_clicked(parent_hwnd: int) -> None:
        """4th invisible cursor: primary (split) or current merged cursor."""
        if dual_enabled[0]:
            if ptr_state["p_pri"] is None:
                return
            px, py = ptr_state["p_pri"]
        else:
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            px, py = int(pt.x), int(pt.y)
        pinned_pri_warp_point[0] = (int(px), int(py))
        merge_snap_anim_begin(parent_hwnd, int(px), int(py))

    def pin_second_clicked(parent_hwnd: int) -> None:
        """3rd invisible cursor: 2nd cursor (split) or current merged cursor."""
        if dual_enabled[0]:
            if ptr_state["p_sec"] is None:
                return
            sx, sy = ptr_state["p_sec"]
        else:
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            sx, sy = int(pt.x), int(pt.y)
        pinned_sec_warp_point[0] = (int(sx), int(sy))
        merge_snap_anim_begin(parent_hwnd, int(sx), int(sy))

    second_ghost_btn_drag: dict[str, object] = {
        "active": False,
        "button": None,
        "saved": None,
        "merged_click": False,
        "old_proc": {
            IDC_SECOND_LEFT_CLICK: 0,
            IDC_SECOND_RIGHT_CLICK: 0,
            IDC_SECOND_MIDDLE_CLICK: 0,
        },
    }
    ccos_fkey_physical_down: set[int] = set()
    ccos_hotkey_target: list[int] = [0]
    keyboard_ll_hook: list[ctypes.c_void_p | None] = [None]
    keyboard_ll_proc_keepalive: list[object] = [None]

    def dispatch_ccos_fkey(hwnd: int, vk: int, is_keyup: bool) -> None:
        idc_cc = CCOS_FKEY_TO_IDC.get(vk)
        if idc_cc is None:
            return
        if idc_cc in CCOS_FKEY_CHECKBOX_IDCS:
            if is_keyup:
                ccos_fkey_physical_down.discard(vk)
                return
            if vk in ccos_fkey_physical_down:
                return
            ccos_fkey_physical_down.add(vk)
            cbtn = user32.GetDlgItem(hwnd, idc_cc)
            if cbtn:
                user32.SendMessageW(cbtn, BM_CLICK, 0, 0)
            return
        if not is_keyup:
            if vk in ccos_fkey_physical_down:
                return
            ccos_fkey_physical_down.add(vk)
            btn_cc = user32.GetDlgItem(hwnd, idc_cc)
            if btn_cc:
                user32.SendMessageW(btn_cc, BM_SETSTATE, 1, 0)
                if idc_cc in CCOS_FKEY_SECOND_GHOST_BTN_IDCS:
                    user32.SendMessageW(
                        btn_cc,
                        WM_LBUTTONDOWN,
                        1,
                        wintypes.LPARAM((11 << 16) | 8),
                    )
            return
        if vk not in ccos_fkey_physical_down:
            return
        ccos_fkey_physical_down.discard(vk)
        btn_cc = user32.GetDlgItem(hwnd, idc_cc)
        if btn_cc:
            user32.SendMessageW(btn_cc, BM_SETSTATE, 0, 0)
            if idc_cc in CCOS_FKEY_SECOND_GHOST_BTN_IDCS:
                user32.SendMessageW(
                    btn_cc,
                    WM_LBUTTONUP,
                    0,
                    wintypes.LPARAM((11 << 16) | 8),
                )
        if idc_cc == IDC_PIN_FIRST:
            pin_first_clicked(hwnd)
        elif idc_cc == IDC_PIN_SECOND:
            pin_second_clicked(hwnd)
        elif idc_cc == IDC_MERGE_WARP_FIRST:
            warp_first_execute(hwnd)
        elif idc_cc == IDC_MERGE_WARP_SECOND:
            warp_second_execute(hwnd)

    def _ccos_ll_keyboard_hook(n_code: int, w_param: int, l_param: int) -> int:
        if n_code < 0:
            return int(
                user32.CallNextHookEx(
                    keyboard_ll_hook[0], n_code, w_param, l_param
                )
            )
        if n_code != HC_ACTION:
            return int(
                user32.CallNextHookEx(
                    keyboard_ll_hook[0], n_code, w_param, l_param
                )
            )
        kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        if kb.flags & LLKHF_INJECTED:
            return int(
                user32.CallNextHookEx(
                    keyboard_ll_hook[0], n_code, w_param, l_param
                )
            )
        vk = int(kb.vkCode) & 0xFF
        if VK_F15 <= vk <= VK_F24:
            target = int(ccos_hotkey_target[0])
            if target:
                is_up = bool(kb.flags & LLKHF_UP)
                user32.PostMessageW(
                    wintypes.HWND(target),
                    WM_CCOS_FKEY,
                    vk,
                    1 if is_up else 0,
                )
                return 1
        return int(
            user32.CallNextHookEx(keyboard_ll_hook[0], n_code, w_param, l_param)
        )

    keyboard_ll_proc_keepalive[0] = HOOKPROC_LL(_ccos_ll_keyboard_hook)

    def _inject_second_ghost_button_down(which: str) -> None:
        if which == "left":
            inject_mouse_left_down()
        elif which == "right":
            inject_mouse_right_down()
        else:
            inject_mouse_middle_down()

    def _inject_second_ghost_button_up(which: str) -> None:
        if which == "left":
            inject_mouse_left_up()
        elif which == "right":
            inject_mouse_right_up()
        else:
            inject_mouse_middle_up()

    def _finish_second_ghost_btn_drag() -> None:
        if not second_ghost_btn_drag["active"]:
            return
        btn = second_ghost_btn_drag.get("button")
        if isinstance(btn, str):
            _inject_second_ghost_button_up(btn)
        if second_ghost_btn_drag.get("merged_click"):
            second_ghost_btn_drag["active"] = False
            second_ghost_btn_drag["merged_click"] = False
            second_ghost_btn_drag["saved"] = None
            second_ghost_btn_drag["button"] = None
            return
        sav = second_ghost_btn_drag["saved"]
        if sav is not None:
            ax, ay = int(sav[0]), int(sav[1])
            ptr_state["p_pri"] = (ax, ay)
            user32.SetCursorPos(ax, ay)
        second_ghost_btn_drag["active"] = False
        second_ghost_btn_drag["saved"] = None
        second_ghost_btn_drag["button"] = None
        sync_secondary_overlay()

    def _second_ghost_btn_subclass_body(
        hw: int,
        msg: int,
        wparam: int,
        lparam: int,
        idc_btn: int,
        which: str,
    ) -> int:
        _op = second_ghost_btn_drag["old_proc"]
        old_p = ctypes.c_void_p(int(_op[idc_btn]))
        if msg == WM_LBUTTONDOWN:
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            saved_pri = (int(pt.x), int(pt.y))
            ret = user32.CallWindowProcW(old_p, hw, msg, wparam, lparam)
            if second_ghost_btn_drag["active"]:
                return ret
            if not dual_enabled[0]:
                _inject_second_ghost_button_down(which)
                user32.ReleaseCapture()
                second_ghost_btn_drag["active"] = True
                second_ghost_btn_drag["button"] = which
                second_ghost_btn_drag["merged_click"] = True
                second_ghost_btn_drag["saved"] = None
                return ret
            ok = ptr_state["p_sec"] is not None and len(device_order) >= 2
            if ok:
                sx, sy = ptr_state["p_sec"]
                ix, iy = int(sx), int(sy)
                user32.SetCursorPos(ix, iy)
                ptr_state["p_pri"] = (ix, iy)
                _inject_second_ghost_button_down(which)
                user32.ReleaseCapture()
                second_ghost_btn_drag["active"] = True
                second_ghost_btn_drag["button"] = which
                second_ghost_btn_drag["merged_click"] = False
                second_ghost_btn_drag["saved"] = saved_pri
                sync_secondary_overlay()
            return ret
        if msg == WM_LBUTTONUP:
            if second_ghost_btn_drag["active"]:
                _finish_second_ghost_btn_drag()
            return user32.CallWindowProcW(old_p, hw, msg, wparam, lparam)
        if msg == WM_CAPTURECHANGED:
            # Ghost drags release capture after inject; shell/apps take capture to move.
            return user32.CallWindowProcW(old_p, hw, msg, wparam, lparam)
        return user32.CallWindowProcW(old_p, hw, msg, wparam, lparam)

    def _second_ghost_left_sub(hw, msg, wp, lp):
        return _second_ghost_btn_subclass_body(hw, msg, wp, lp, IDC_SECOND_LEFT_CLICK, "left")

    def _second_ghost_right_sub(hw, msg, wp, lp):
        return _second_ghost_btn_subclass_body(hw, msg, wp, lp, IDC_SECOND_RIGHT_CLICK, "right")

    def _second_ghost_middle_sub(hw, msg, wp, lp):
        return _second_ghost_btn_subclass_body(hw, msg, wp, lp, IDC_SECOND_MIDDLE_CLICK, "middle")

    second_ghost_left_sub_proc = WNDPROC(_second_ghost_left_sub)
    second_ghost_right_sub_proc = WNDPROC(_second_ghost_right_sub)
    second_ghost_middle_sub_proc = WNDPROC(_second_ghost_middle_sub)

    tray_icon_alive: list[bool] = [False]

    def _tray_set_tip_text(nid: NOTIFYICONDATAW, text: str) -> None:
        buf = ctypes.create_unicode_buffer(text[:127], 128)
        ctypes.memmove(
            ctypes.addressof(nid.szTip),
            buf,
            128 * ctypes.sizeof(ctypes.c_wchar),
        )

    def tray_add(parent_hwnd: int) -> None:
        if tray_icon_alive[0]:
            return
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = parent_hwnd
        nid.uID = TRAY_ICON_ID
        nid.uCallbackMessage = WM_TRAY_CALLBACK
        hico = user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))
        if hico:
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            nid.hIcon = hico
        else:
            nid.uFlags = NIF_MESSAGE | NIF_TIP
        _tray_set_tip_text(nid, "CharaChorder Duo")
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            print("Shell_NotifyIconW(NIM_ADD) failed:", kernel32.GetLastError())
            return
        tray_icon_alive[0] = True

    def tray_remove(parent_hwnd: int) -> None:
        if not tray_icon_alive[0]:
            return
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = parent_hwnd
        nid.uID = TRAY_ICON_ID
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        tray_icon_alive[0] = False

    def tray_restore_window(parent_hwnd: int) -> None:
        user32.ShowWindow(parent_hwnd, SW_SHOW)
        user32.ShowWindow(parent_hwnd, SW_RESTORE)
        user32.SetForegroundWindow(parent_hwnd)

    def tray_show_context_menu(parent_hwnd: int) -> None:
        hmenu = user32.CreatePopupMenu()
        if not hmenu:
            return
        user32.AppendMenuW(
            hmenu,
            MF_STRING | MF_ENABLED,
            IDM_TRAY_OPEN,
            "Open CharaChorder Duo…",
        )
        user32.AppendMenuW(
            hmenu,
            MF_STRING | MF_ENABLED,
            IDM_TRAY_EXIT,
            "Exit",
        )
        user32.SetForegroundWindow(parent_hwnd)
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.TrackPopupMenu(
            hmenu,
            TPM_RIGHTBUTTON | TPM_BOTTOMALIGN,
            int(pt.x),
            int(pt.y),
            0,
            parent_hwnd,
            None,
        )
        user32.DestroyMenu(hmenu)

    def wnd_proc(hwnd, msg, wparam, lparam):
        if msg == WM_GHOST_COLOR_SYNC:
            cc_live = choosecolor_sync.get("cc")
            la_holder = choosecolor_sync.get("last_rgb")
            if cc_live is not None and la_holder is not None:
                r, g, b = _colorref_to_rgb(cc_live.rgbResult)
                t = (r, g, b)
                if t != la_holder[0]:
                    la_holder[0] = t
                    overlay_state["ghost_tint"] = t
                    refresh_ghost_rgb_label(hwnd)
                    apply_ghost_appearance(hwnd)
            return 0

        if msg == WM_TRAY_CALLBACK:
            ev = int(lparam) & 0xFFFF
            if ev == WM_LBUTTONDBLCLK:
                tray_restore_window(hwnd)
                return 0
            if ev == WM_RBUTTONUP:
                tray_show_context_menu(hwnd)
                return 0
            return 0

        if msg == WM_CLOSE:
            user32.ShowWindow(hwnd, SW_HIDE)
            return 0

        if msg == WM_SYSCOMMAND:
            if (int(wparam) & 0xFFF0) == SC_MINIMIZE:
                user32.ShowWindow(hwnd, SW_HIDE)
                return 0

        if msg == WM_TIMER and int(wparam) == SPLIT_SNAP_TIMER_ID:
            split_snap_anim_step()
            return 0

        if msg == WM_CTLCOLORSTATIC:
            if lparam and user32.GetDlgCtrlID(lparam) == IDC_PROFILE_DIVIDER:
                if red_divider_brush[0] is None:
                    red_divider_brush[0] = int(gdi32.CreateSolidBrush(0x0000FF))
                gdi32.SetBkColor(wparam, 0x0000FF)
                return wintypes.LRESULT(red_divider_brush[0])

        if msg == WM_CREATE:
            # Do not use RIDEV_NOLEGACY here: it suppresses legacy mouse messages and can
            # break WM_SETCURSOR handling so the arrow stays stuck as busy/wait.
            rid = RAWINPUTDEVICE(
                HID_USAGE_PAGE_GENERIC,
                HID_USAGE_GENERIC_MOUSE,
                RIDEV_INPUTSINK,
                hwnd,
            )
            if not user32.RegisterRawInputDevices(
                ctypes.byref(rid), 1, ctypes.sizeof(RAWINPUTDEVICE)
            ):
                print("RegisterRawInputDevices failed:", kernel32.GetLastError())
                user32.DestroyWindow(hwnd)
                return 0
            user32.CreateWindowExW(
                0,
                "BUTTON",
                "CCOS Inputs",
                WS_CHILD | WS_VISIBLE | BS_GROUPBOX,
                10,
                8,
                440,
                272,
                hwnd,
                None,
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "BUTTON",
                ccos_input_label("split/merge", IDC_TOGGLE_POINTERS),
                WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_AUTOCHECKBOX,
                18,
                26,
                200,
                22,
                hwnd,
                ctypes.c_void_p(IDC_TOGGLE_POINTERS),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "BUTTON",
                ccos_input_label("duo shift", IDC_DUO_SHIFT_TOGGLE),
                WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_AUTOCHECKBOX,
                230,
                26,
                212,
                22,
                hwnd,
                ctypes.c_void_p(IDC_DUO_SHIFT_TOGGLE),
                hinst,
                None,
            )
            _ph_toggle_rows = (
                (
                    (IDC_MERGE_WARP_FIRST, "merge/warp 1st"),
                    (IDC_MERGE_WARP_SECOND, "merge/warp 2nd"),
                ),
                (
                    (IDC_PIN_FIRST, "pin 1st"),
                    (IDC_PIN_SECOND, "pin 2nd"),
                ),
                (
                    (IDC_SECOND_LEFT_CLICK, "2nd Left Click"),
                    (IDC_SECOND_RIGHT_CLICK, "2nd Right Click"),
                ),
                (
                    (IDC_SECOND_MIDDLE_CLICK, "2nd Middle Click"),
                    (IDC_ULTIMATE_TOGGLE, "placeholder"),
                ),
            )
            _ph_y = 50
            for _ph_row in _ph_toggle_rows:
                user32.CreateWindowExW(
                    0,
                    "BUTTON",
                    ccos_input_label(_ph_row[0][1], _ph_row[0][0]),
                    WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
                    18,
                    _ph_y,
                    200,
                    22,
                    hwnd,
                    ctypes.c_void_p(_ph_row[0][0]),
                    hinst,
                    None,
                )
                user32.CreateWindowExW(
                    0,
                    "BUTTON",
                    ccos_input_label(_ph_row[1][1], _ph_row[1][0]),
                    WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
                    230,
                    _ph_y,
                    212,
                    22,
                    hwnd,
                    ctypes.c_void_p(_ph_row[1][0]),
                    hinst,
                    None,
                )
                _ph_y += 24
            for _idc_sg, _proc_sg in (
                (IDC_SECOND_LEFT_CLICK, second_ghost_left_sub_proc),
                (IDC_SECOND_RIGHT_CLICK, second_ghost_right_sub_proc),
                (IDC_SECOND_MIDDLE_CLICK, second_ghost_middle_sub_proc),
            ):
                _sg_btn = user32.GetDlgItem(hwnd, _idc_sg)
                if _sg_btn:
                    second_ghost_btn_drag["old_proc"][_idc_sg] = int(
                        user32.GetWindowLongPtrW(_sg_btn, GWLP_WNDPROC)
                    )
                    user32.SetWindowLongPtrW(
                        _sg_btn,
                        GWLP_WNDPROC,
                        ctypes.cast(_proc_sg, ctypes.c_void_p).value,
                    )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "2nd cursor:",
                WS_CHILD | WS_VISIBLE,
                18,
                148,
                424,
                16,
                hwnd,
                None,
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "COMBOBOX",
                "",
                WS_CHILD | WS_VISIBLE | WS_TABSTOP | CBS_DROPDOWNLIST,
                18,
                164,
                424,
                96,
                hwnd,
                ctypes.c_void_p(IDC_SECONDARY_MOUSE_COMBO),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "2nd cursor — size & color",
                WS_CHILD | WS_VISIBLE,
                18,
                192,
                424,
                16,
                hwnd,
                None,
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "Size px:",
                WS_CHILD | WS_VISIBLE,
                18,
                212,
                58,
                16,
                hwnd,
                None,
                hinst,
                None,
            )
            user32.CreateWindowExW(
                WS_EX_CLIENTEDGE,
                "EDIT",
                str(DEFAULT_GHOST_SIZE_PX),
                WS_CHILD
                | WS_VISIBLE
                | WS_TABSTOP
                | ES_AUTOHSCROLL
                | ES_NUMBER,
                78,
                208,
                44,
                22,
                hwnd,
                ctypes.c_void_p(IDC_GHOST_SIZE_EDIT),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "BUTTON",
                "Pick color…",
                WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
                128,
                206,
                96,
                24,
                hwnd,
                ctypes.c_void_p(IDC_GHOST_COLOR_BTN),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "RGB 220, 235, 255",
                WS_CHILD | WS_VISIBLE,
                230,
                212,
                210,
                16,
                hwnd,
                ctypes.c_void_p(IDC_GHOST_RGB_STATIC),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "",
                WS_CHILD | WS_VISIBLE,
                12,
                234,
                436,
                3,
                hwnd,
                ctypes.c_void_p(IDC_PROFILE_DIVIDER),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "Profile:",
                WS_CHILD | WS_VISIBLE,
                18,
                242,
                48,
                16,
                hwnd,
                None,
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "COMBOBOX",
                "",
                WS_CHILD | WS_VISIBLE | WS_TABSTOP | CBS_DROPDOWNLIST,
                70,
                238,
                372,
                96,
                hwnd,
                ctypes.c_void_p(IDC_PROFILE_COMBO),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "Sensitivity:",
                WS_CHILD | WS_VISIBLE,
                18,
                268,
                400,
                16,
                hwnd,
                None,
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "1st cursor:",
                WS_CHILD | WS_VISIBLE,
                18,
                286,
                192,
                16,
                hwnd,
                None,
                hinst,
                None,
            )
            user32.CreateWindowExW(
                WS_EX_CLIENTEDGE,
                "EDIT",
                str(DEFAULT_SENSITIVITY_PCT),
                WS_CHILD
                | WS_VISIBLE
                | WS_TABSTOP
                | ES_AUTOHSCROLL
                | ES_NUMBER,
                210,
                282,
                52,
                22,
                hwnd,
                ctypes.c_void_p(IDC_SENS_MOVE_PRIMARY_EDIT),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "2nd Cursor:",
                WS_CHILD | WS_VISIBLE,
                274,
                286,
                72,
                16,
                hwnd,
                None,
                hinst,
                None,
            )
            user32.CreateWindowExW(
                WS_EX_CLIENTEDGE,
                "EDIT",
                str(DEFAULT_SENSITIVITY_PCT),
                WS_CHILD
                | WS_VISIBLE
                | WS_TABSTOP
                | ES_AUTOHSCROLL
                | ES_NUMBER,
                352,
                282,
                52,
                22,
                hwnd,
                ctypes.c_void_p(IDC_SENS_MOVE_SECONDARY_EDIT),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "Vertical scroll strength (%):",
                WS_CHILD | WS_VISIBLE,
                18,
                308,
                400,
                16,
                hwnd,
                None,
                hinst,
                None,
            )
            user32.CreateWindowExW(
                WS_EX_CLIENTEDGE,
                "EDIT",
                str(DEFAULT_SCROLL_PCT),
                WS_CHILD
                | WS_VISIBLE
                | WS_TABSTOP
                | ES_AUTOHSCROLL
                | ES_NUMBER,
                218,
                304,
                52,
                22,
                hwnd,
                ctypes.c_void_p(IDC_SENS_SCROLL_EDIT),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "Horizontal scroll strength (%):",
                WS_CHILD | WS_VISIBLE,
                18,
                330,
                400,
                16,
                hwnd,
                None,
                hinst,
                None,
            )
            user32.CreateWindowExW(
                WS_EX_CLIENTEDGE,
                "EDIT",
                str(DEFAULT_SCROLL_PCT),
                WS_CHILD
                | WS_VISIBLE
                | WS_TABSTOP
                | ES_AUTOHSCROLL
                | ES_NUMBER,
                218,
                326,
                52,
                22,
                hwnd,
                ctypes.c_void_p(IDC_SENS_SCROLL_H_EDIT),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "Zoom strength (Ctrl+wheel, %):",
                WS_CHILD | WS_VISIBLE,
                18,
                352,
                400,
                16,
                hwnd,
                None,
                hinst,
                None,
            )
            user32.CreateWindowExW(
                WS_EX_CLIENTEDGE,
                "EDIT",
                str(DEFAULT_SCROLL_PCT),
                WS_CHILD
                | WS_VISIBLE
                | WS_TABSTOP
                | ES_AUTOHSCROLL
                | ES_NUMBER,
                218,
                348,
                52,
                22,
                hwnd,
                ctypes.c_void_p(IDC_SENS_ZOOM_EDIT),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "Volume strength (media keys, %):",
                WS_CHILD | WS_VISIBLE,
                18,
                374,
                400,
                16,
                hwnd,
                None,
                hinst,
                None,
            )
            user32.CreateWindowExW(
                WS_EX_CLIENTEDGE,
                "EDIT",
                str(DEFAULT_VOLUME_PCT),
                WS_CHILD
                | WS_VISIBLE
                | WS_TABSTOP
                | ES_AUTOHSCROLL
                | ES_NUMBER,
                218,
                370,
                52,
                22,
                hwnd,
                ctypes.c_void_p(IDC_SENS_VOLUME_EDIT),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "Tab strength (Ctrl+Tab / Ctrl+Shift+Tab, %):",
                WS_CHILD | WS_VISIBLE,
                18,
                396,
                400,
                16,
                hwnd,
                None,
                hinst,
                None,
            )
            user32.CreateWindowExW(
                WS_EX_CLIENTEDGE,
                "EDIT",
                str(DEFAULT_TAB_PCT),
                WS_CHILD
                | WS_VISIBLE
                | WS_TABSTOP
                | ES_AUTOHSCROLL
                | ES_NUMBER,
                218,
                392,
                52,
                22,
                hwnd,
                ctypes.c_void_p(IDC_SENS_TAB_EDIT),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "Change application (Win+Tab) strength (%):",
                WS_CHILD | WS_VISIBLE,
                18,
                418,
                400,
                16,
                hwnd,
                None,
                hinst,
                None,
            )
            user32.CreateWindowExW(
                WS_EX_CLIENTEDGE,
                "EDIT",
                str(DEFAULT_CHANGE_APP_PCT),
                WS_CHILD
                | WS_VISIBLE
                | WS_TABSTOP
                | ES_AUTOHSCROLL
                | ES_NUMBER,
                218,
                414,
                52,
                22,
                hwnd,
                ctypes.c_void_p(IDC_SENS_CHANGE_APP_EDIT),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "BUTTON",
                "ring animations",
                WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_AUTOCHECKBOX,
                18,
                438,
                424,
                22,
                hwnd,
                ctypes.c_void_p(IDC_RING_ANIMATIONS_TOGGLE),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "Gesture axis tightness (1–250000 %, 150 default; max ≈ off):",
                WS_CHILD | WS_VISIBLE,
                18,
                464,
                400,
                16,
                hwnd,
                None,
                hinst,
                None,
            )
            user32.CreateWindowExW(
                WS_EX_CLIENTEDGE,
                "EDIT",
                str(DEFAULT_GESTURE_AIM_PCT),
                WS_CHILD
                | WS_VISIBLE
                | WS_TABSTOP
                | ES_AUTOHSCROLL
                | ES_NUMBER,
                218,
                460,
                52,
                22,
                hwnd,
                ctypes.c_void_p(IDC_SENS_GESTURE_AIM_EDIT),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "After profile change, block gestures (ms, 0 = off, default 5):",
                WS_CHILD | WS_VISIBLE,
                18,
                490,
                400,
                16,
                hwnd,
                None,
                hinst,
                None,
            )
            user32.CreateWindowExW(
                WS_EX_CLIENTEDGE,
                "EDIT",
                str(DEFAULT_PROFILE_GESTURE_COOLDOWN_MS),
                WS_CHILD
                | WS_VISIBLE
                | WS_TABSTOP
                | ES_AUTOHSCROLL
                | ES_NUMBER,
                218,
                486,
                52,
                22,
                hwnd,
                ctypes.c_void_p(IDC_SENS_PROFILE_GESTURE_COOLDOWN_EDIT),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "Gestures:",
                WS_CHILD | WS_VISIBLE,
                18,
                520,
                424,
                14,
                hwnd,
                None,
                hinst,
                None,
            )
            _gesture_rows = (
                (
                    (IDC_GESTURE_SHEAR, "North-South"),
                    (IDC_GESTURE_REVERSE_SHEAR, "South-North"),
                ),
                (
                    (IDC_GESTURE_PINCH, "East-West"),
                    (IDC_GESTURE_PULL, "West-East"),
                ),
                (
                    (IDC_GESTURE_DOUBLE_UP, "North-North"),
                    (IDC_GESTURE_DOUBLE_DOWN, "South-South"),
                ),
                (
                    (IDC_GESTURE_DOUBLE_LEFT, "West-West"),
                    (IDC_GESTURE_DOUBLE_RIGHT, "East-East"),
                ),
                (
                    (IDC_GESTURE_NORTH_EAST, "North-East"),
                    (IDC_GESTURE_NORTH_WEST, "North-West"),
                ),
                (
                    (IDC_GESTURE_SOUTH_EAST, "South-East"),
                    (IDC_GESTURE_SOUTH_WEST, "South-West"),
                ),
                (
                    (IDC_GESTURE_EAST_NORTH, "East-North"),
                    (IDC_GESTURE_EAST_SOUTH, "East-South"),
                ),
                (
                    (IDC_GESTURE_WEST_NORTH, "West-North"),
                    (IDC_GESTURE_WEST_SOUTH, "West-South"),
                ),
            )
            _gx1 = 18
            _gx2 = 228
            _gcw = 200
            _gch = 36
            _gtitle = 13
            _gy = 538
            for _row in _gesture_rows:
                user32.CreateWindowExW(
                    0,
                    "STATIC",
                    _row[0][1],
                    WS_CHILD | WS_VISIBLE,
                    _gx1,
                    _gy,
                    _gcw,
                    _gtitle,
                    hwnd,
                    None,
                    hinst,
                    None,
                )
                user32.CreateWindowExW(
                    0,
                    "COMBOBOX",
                    "",
                    WS_CHILD | WS_VISIBLE | WS_TABSTOP | CBS_DROPDOWNLIST,
                    _gx1,
                    _gy + _gtitle,
                    _gcw,
                    _gch,
                    hwnd,
                    ctypes.c_void_p(_row[0][0]),
                    hinst,
                    None,
                )
                user32.CreateWindowExW(
                    0,
                    "STATIC",
                    _row[1][1],
                    WS_CHILD | WS_VISIBLE,
                    _gx2,
                    _gy,
                    _gcw,
                    _gtitle,
                    hwnd,
                    None,
                    hinst,
                    None,
                )
                user32.CreateWindowExW(
                    0,
                    "COMBOBOX",
                    "",
                    WS_CHILD | WS_VISIBLE | WS_TABSTOP | CBS_DROPDOWNLIST,
                    _gx2,
                    _gy + _gtitle,
                    _gcw,
                    _gch,
                    hwnd,
                    ctypes.c_void_p(_row[1][0]),
                    hinst,
                    None,
                )
                _gy += _gtitle + _gch + 2
            for _idc in ALL_GESTURE_IDCS:
                _gcb = user32.GetDlgItem(hwnd, _idc)
                if not _gcb:
                    continue
                _combo_add_string(_gcb, "(none)")
                _combo_add_string(_gcb, "Scroll north")
                _combo_add_string(_gcb, "Scroll south")
                _combo_add_string(_gcb, "Scroll west")
                _combo_add_string(_gcb, "Scroll east")
                _combo_add_string(_gcb, "zoom in")
                _combo_add_string(_gcb, "zoom out")
                _combo_add_string(_gcb, "Volume up")
                _combo_add_string(_gcb, "Volume down")
                _combo_add_string(_gcb, "Tab right")
                _combo_add_string(_gcb, "Tab left")
                _combo_add_string(_gcb, "Change application")
                _combo_add_string(_gcb, "Toggle split/merge")
                _combo_add_string(_gcb, "Warp 1st (merge at 1st)")
                _combo_add_string(_gcb, "Warp 2nd (merge at 2nd)")
                _combo_add_string(_gcb, "Show desktop")
            _pcb_prof = user32.GetDlgItem(hwnd, IDC_PROFILE_COMBO)
            if _pcb_prof:
                for _pn in PROFILE_LABELS:
                    _combo_add_string(_pcb_prof, _pn)
            current_profile_idx[0] = profile_idx_from_flags(
                dual_enabled[0], duo_shift[0]
            )
            load_profile_slot_into_live(current_profile_idx[0])
            refresh_sensitivity_edits(hwnd)
            refresh_gesture_combos(hwnd)
            refresh_secondary_mouse_combo(hwnd)
            refresh_ghost_rgb_label(hwnd)
            refresh_split_merge_checkbox(hwnd)
            refresh_duo_shift_checkbox(hwnd)
            refresh_ring_animations_checkbox(hwnd)
            set_profile_combo_sel(hwnd, current_profile_idx[0])
            sync_secondary_overlay()
            tray_add(hwnd)
            return 0

        if msg == WM_COMMAND:
            ctrl_id = wparam & 0xFFFF
            notify = (wparam >> 16) & 0xFFFF
            if notify == 0 and ctrl_id == IDM_TRAY_OPEN:
                tray_restore_window(hwnd)
                return 0
            if notify == 0 and ctrl_id == IDM_TRAY_EXIT:
                user32.DestroyWindow(hwnd)
                return 0
            if ctrl_id == IDC_TOGGLE_POINTERS and notify == BN_CLICKED:
                tbtn = user32.GetDlgItem(hwnd, IDC_TOGGLE_POINTERS)
                new_split = (
                    int(user32.SendMessageW(tbtn, BM_GETCHECK, 0, 0)) == BST_CHECKED
                )
                finish_split_merge_profile_transition(hwnd, new_split)
                return 0
            if ctrl_id == IDC_DUO_SHIFT_TOGGLE and notify == BN_CLICKED:
                tbtn = user32.GetDlgItem(hwnd, IDC_DUO_SHIFT_TOGGLE)
                new_duo = (
                    int(user32.SendMessageW(tbtn, BM_GETCHECK, 0, 0)) == BST_CHECKED
                )
                old_idx = profile_idx_from_flags(dual_enabled[0], duo_shift[0])
                save_live_profile_to_slot(old_idx)
                duo_shift[0] = new_duo
                new_idx = profile_idx_from_flags(dual_enabled[0], duo_shift[0])
                current_profile_idx[0] = new_idx
                load_profile_slot_into_live(new_idx)
                arm_profile_change_gesture_suppress()
                refresh_sensitivity_edits(hwnd)
                refresh_gesture_combos(hwnd)
                refresh_ring_animations_checkbox(hwnd)
                set_profile_combo_sel(hwnd, new_idx)
                return 0
            if ctrl_id == IDC_RING_ANIMATIONS_TOGGLE and notify == BN_CLICKED:
                tbtn = user32.GetDlgItem(hwnd, IDC_RING_ANIMATIONS_TOGGLE)
                ring_animations_enabled[0] = (
                    int(user32.SendMessageW(tbtn, BM_GETCHECK, 0, 0)) == BST_CHECKED
                )
                if not ring_animations_enabled[0]:
                    split_snap_anim_cancel()
                save_live_profile_to_slot(current_profile_idx[0])
                return 0
            if ctrl_id == IDC_PIN_FIRST and notify == BN_CLICKED:
                pin_first_clicked(hwnd)
                return 0
            if ctrl_id == IDC_PIN_SECOND and notify == BN_CLICKED:
                pin_second_clicked(hwnd)
                return 0
            if ctrl_id == IDC_MERGE_WARP_FIRST and notify == BN_CLICKED:
                warp_first_execute(hwnd)
                return 0
            if ctrl_id == IDC_MERGE_WARP_SECOND and notify == BN_CLICKED:
                warp_second_execute(hwnd)
                return 0
            _ph_i = PLACEHOLDER_TOGGLE_INDEX.get(ctrl_id)
            if _ph_i is not None and notify == BN_CLICKED:
                # BS_PUSHBUTTON: BN_CLICKED on mouse-up after a complete press.
                # Placeholder — no persistent state; hook per-button actions here later.
                return 0
            if ctrl_id == IDC_PROFILE_COMBO and notify == CBN_SELCHANGE:
                if profile_combo_suppress[0]:
                    return 0
                pcb = user32.GetDlgItem(hwnd, IDC_PROFILE_COMBO)
                if not pcb:
                    return 0
                sel = int(user32.SendMessageW(pcb, CB_GETCURSEL, 0, 0))
                if sel < 0 or sel > 3:
                    return 0
                save_live_profile_to_slot(current_profile_idx[0])
                was_split = dual_enabled[0]
                current_profile_idx[0] = sel
                dual_enabled[0] = (sel & 1) != 0
                duo_shift[0] = (sel & 2) != 0
                load_profile_slot_into_live(sel)
                arm_profile_change_gesture_suppress()
                refresh_sensitivity_edits(hwnd)
                refresh_gesture_combos(hwnd)
                refresh_split_merge_checkbox(hwnd)
                refresh_duo_shift_checkbox(hwnd)
                refresh_ring_animations_checkbox(hwnd)
                if not dual_enabled[0]:
                    pt_merge = POINT()
                    user32.GetCursorPos(ctypes.byref(pt_merge))
                    mx, my = int(pt_merge.x), int(pt_merge.y)
                    ghost_overlay_hide()
                    ptr_state["p_pri"] = None
                    ptr_state["p_sec"] = None
                    merge_snap_anim_begin(hwnd, mx, my)
                else:
                    if not was_split:
                        init_split_cursor_positions_from_primary()
                        split_snap_anim_begin(hwnd)
                    sync_secondary_overlay()
                return 0
            if ctrl_id == IDC_GHOST_COLOR_BTN and notify == BN_CLICKED:
                pick_ghost_color(hwnd)
                return 0
            if ctrl_id == IDC_GHOST_SIZE_EDIT and notify == EN_KILLFOCUS:
                new_sz = read_ghost_size_from_edit(hwnd)
                ed = user32.GetDlgItem(hwnd, IDC_GHOST_SIZE_EDIT)
                if ed:
                    user32.SetWindowTextW(ed, str(new_sz))
                cur = int(overlay_state.get("ghost_size", DEFAULT_GHOST_SIZE_PX))
                if new_sz != cur:
                    apply_ghost_appearance(hwnd)
                return 0
            if notify == EN_KILLFOCUS:
                if ctrl_id == IDC_SENS_MOVE_PRIMARY_EDIT:
                    normalize_sensitivity_edit(hwnd, ctrl_id, "move_primary_pct")
                    return 0
                if ctrl_id == IDC_SENS_MOVE_SECONDARY_EDIT:
                    normalize_sensitivity_edit(hwnd, ctrl_id, "move_secondary_pct")
                    return 0
                if ctrl_id == IDC_SENS_SCROLL_EDIT:
                    normalize_sensitivity_edit(hwnd, ctrl_id, "scroll_pct")
                    return 0
                if ctrl_id == IDC_SENS_SCROLL_H_EDIT:
                    normalize_sensitivity_edit(
                        hwnd, ctrl_id, "scroll_horizontal_pct"
                    )
                    return 0
                if ctrl_id == IDC_SENS_ZOOM_EDIT:
                    normalize_sensitivity_edit(hwnd, ctrl_id, "zoom_pct")
                    return 0
                if ctrl_id == IDC_SENS_VOLUME_EDIT:
                    normalize_sensitivity_edit(hwnd, ctrl_id, "volume_pct")
                    return 0
                if ctrl_id == IDC_SENS_TAB_EDIT:
                    normalize_sensitivity_edit(hwnd, ctrl_id, "tab_pct")
                    return 0
                if ctrl_id == IDC_SENS_CHANGE_APP_EDIT:
                    normalize_sensitivity_edit(
                        hwnd, ctrl_id, "change_app_pct"
                    )
                    return 0
                if ctrl_id == IDC_SENS_GESTURE_AIM_EDIT:
                    normalize_sensitivity_edit(
                        hwnd, ctrl_id, "gesture_aim_pct"
                    )
                    return 0
                if ctrl_id == IDC_SENS_PROFILE_GESTURE_COOLDOWN_EDIT:
                    normalize_sensitivity_edit(
                        hwnd,
                        ctrl_id,
                        "profile_gesture_cooldown_ms",
                    )
                    return 0
            if ctrl_id == IDC_SECONDARY_MOUSE_COMBO and notify == CBN_SELCHANGE:
                cb = user32.GetDlgItem(hwnd, IDC_SECONDARY_MOUSE_COMBO)
                if cb:
                    sel = int(user32.SendMessageW(cb, CB_GETCURSEL, 0, 0))
                    if 0 <= sel < len(device_order):
                        secondary_mouse_idx[0] = sel
                        refresh_secondary_mouse_combo(hwnd)
                        sync_secondary_overlay()
                return 0
            if ctrl_id in ALL_GESTURE_IDCS and notify == CBN_SELCHANGE:
                _gcb = user32.GetDlgItem(hwnd, ctrl_id)
                if _gcb:
                    _gsel = int(user32.SendMessageW(_gcb, CB_GETCURSEL, 0, 0))
                    if _gsel >= 0:
                        gesture_sel[ctrl_id] = _gsel
                return 0

        if msg == WM_CCOS_FKEY:
            dispatch_ccos_fkey(hwnd, int(wparam) & 0xFF, int(lparam) != 0)
            return 0

        if msg == WM_INPUT:
            size = wintypes.UINT(0)
            user32.GetRawInputData(
                lparam, RID_INPUT, None, ctypes.byref(size), ctypes.sizeof(RAWINPUTHEADER)
            )
            if size.value == 0:
                return 0

            buf = (ctypes.c_byte * size.value)()
            if (
                user32.GetRawInputData(
                    lparam,
                    RID_INPUT,
                    ctypes.byref(buf),
                    ctypes.byref(size),
                    ctypes.sizeof(RAWINPUTHEADER),
                )
                == 0xFFFFFFFF
            ):
                return 0

            raw = ctypes.cast(buf, PM_RAWINPUT).contents
            if raw.header.dwType != RIM_TYPEMOUSE:
                return 0

            mouse = raw.data.mouse
            dev_handle = raw.header.hDevice
            if not dev_handle:
                return 0
            dev_id = int(ctypes.cast(dev_handle, ctypes.c_void_p).value or 0)
            if not dev_id:
                return 0

            if mouse.usFlags & MOUSE_ATTRIBUTES_CHANGED:
                return 0

            n_before = len(device_order)
            if dev_id not in device_order:
                device_order.append(dev_id)
            if len(device_order) != n_before:
                refresh_secondary_mouse_combo(hwnd)
            sec = get_secondary_dev()

            left, top, vw, vh = get_virtual_screen_bounds()
            split = dual_enabled[0]

            def ensure_pair_initialized() -> None:
                pt = POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                cx, cy = int(pt.x), int(pt.y)
                if ptr_state["p_pri"] is None:
                    ptr_state["p_pri"] = (cx, cy)
                if ptr_state["p_sec"] is None:
                    ptr_state["p_sec"] = (ptr_state["p_pri"][0], ptr_state["p_pri"][1])

            def ensure_merged_cursor_initialized() -> None:
                if ptr_state["p_pri"] is None:
                    pt = POINT()
                    user32.GetCursorPos(ctypes.byref(pt))
                    ptr_state["p_pri"] = (int(pt.x), int(pt.y))

            if mouse.usFlags & MOUSE_MOVE_ABSOLUTE:
                if scroll_movement_lock_active():
                    restore_scroll_freeze_pointers()
                    return 0
                ax = mouse.lLastX
                ay = mouse.lLastY
                if mouse.usFlags & MOUSE_VIRTUAL_DESKTOP:
                    vx = left + (ax * vw) // 65535
                    vy = top + (ay * vh) // 65535
                else:
                    sw = user32.GetSystemMetrics(0)
                    sh = user32.GetSystemMetrics(1)
                    vx = (ax * sw) // 65535
                    vy = (ay * sh) // 65535
                vx = clamp(vx, left, left + vw - 1)
                vy = clamp(vy, top, top + vh - 1)

                if split:
                    if sec is None:
                        ptr_state["p_pri"] = (vx, vy)
                        user32.SetCursorPos(vx, vy)
                        sync_secondary_overlay()
                        return 0

                    ensure_pair_initialized()
                    if dev_id == sec:
                        ptr_state["p_sec"] = (vx, vy)
                        if second_ghost_btn_drag["active"]:
                            pt = POINT()
                            user32.GetCursorPos(ctypes.byref(pt))
                            cx, cy = int(pt.x), int(pt.y)
                            inject_mouse_move_relative(vx - cx, vy - cy)
                            ptr_state["p_pri"] = (vx, vy)
                        elif ptr_state["p_pri"] is not None:
                            px, py = ptr_state["p_pri"]
                            user32.SetCursorPos(px, py)
                    else:
                        if second_ghost_btn_drag["active"] and sec is not None:
                            return 0
                        ptr_state["p_pri"] = (vx, vy)
                        user32.SetCursorPos(vx, vy)
                    sync_secondary_overlay()
                    return 0

                ghost_drag_inject_moves = bool(second_ghost_btn_drag["active"])

                def _apply_merged_abs_target(nx: int, ny: int) -> None:
                    nx = clamp(nx, left, left + vw - 1)
                    ny = clamp(ny, top, top + vh - 1)
                    if ghost_drag_inject_moves:
                        pt = POINT()
                        user32.GetCursorPos(ctypes.byref(pt))
                        cx, cy = int(pt.x), int(pt.y)
                        inject_mouse_move_relative(nx - cx, ny - cy)
                    else:
                        user32.SetCursorPos(nx, ny)
                    ptr_state["p_pri"] = (nx, ny)

                if sec is None:
                    _apply_merged_abs_target(vx, vy)
                    return 0
                if dev_id == sec:
                    pc = move_sensitivity_pct(False, True)
                    pt = POINT()
                    user32.GetCursorPos(ctypes.byref(pt))
                    cx, cy = int(pt.x), int(pt.y)
                    nx = int(round(cx + (vx - cx) * pc / 100.0))
                    ny = int(round(cy + (vy - cy) * pc / 100.0))
                    _apply_merged_abs_target(nx, ny)
                else:
                    _apply_merged_abs_target(vx, vy)
                return 0

            dx, dy = mouse.lLastX, mouse.lLastY
            if dx == 0 and dy == 0:
                return 0

            gesture_locks_pointer = False
            if sec is not None:
                gesture_locks_pointer = process_merged_gestures(
                    hwnd, dev_id, sec, dx, dy
                )
            if consume_scroll_move_lock_if_active(gesture_locks_pointer):
                return 0

            if split:
                if sec is None:
                    pc = move_sensitivity_pct(True, False)
                    dx, dy = scale_mouse_delta_xy(dx, dy, pc)
                    if ptr_state["p_pri"] is None:
                        pt = POINT()
                        user32.GetCursorPos(ctypes.byref(pt))
                        ptr_state["p_pri"] = (int(pt.x), int(pt.y))
                    px, py = ptr_state["p_pri"]
                    nx, ny = px + dx, py + dy
                    nx = clamp(nx, left, left + vw - 1)
                    ny = clamp(ny, top, top + vh - 1)
                    ptr_state["p_pri"] = (nx, ny)
                    user32.SetCursorPos(nx, ny)
                    sync_secondary_overlay()
                    return 0

                ensure_pair_initialized()
                pc = move_sensitivity_pct(True, dev_id == sec)
                dx, dy = scale_mouse_delta_xy(dx, dy, pc)
                if dev_id == sec:
                    sx, sy = ptr_state["p_sec"]
                    nx, ny = sx + dx, sy + dy
                    nx = clamp(nx, left, left + vw - 1)
                    ny = clamp(ny, top, top + vh - 1)
                    ptr_state["p_sec"] = (nx, ny)
                    if second_ghost_btn_drag["active"]:
                        pt = POINT()
                        user32.GetCursorPos(ctypes.byref(pt))
                        cx, cy = int(pt.x), int(pt.y)
                        inject_mouse_move_relative(nx - cx, ny - cy)
                        ptr_state["p_pri"] = (nx, ny)
                    elif ptr_state["p_pri"] is not None:
                        px, py = ptr_state["p_pri"]
                        user32.SetCursorPos(px, py)
                else:
                    if second_ghost_btn_drag["active"] and sec is not None:
                        return 0
                    px, py = ptr_state["p_pri"]
                    nx, ny = px + dx, py + dy
                    nx = clamp(nx, left, left + vw - 1)
                    ny = clamp(ny, top, top + vh - 1)
                    ptr_state["p_pri"] = (nx, ny)
                    user32.SetCursorPos(nx, ny)
                sync_secondary_overlay()
                return 0

            ghost_drag_inject_moves = bool(second_ghost_btn_drag["active"])

            def _apply_merged_rel_delta(adx: int, ady: int) -> None:
                ensure_merged_cursor_initialized()
                px, py = ptr_state["p_pri"]
                nx, ny = px + adx, py + ady
                nx = clamp(nx, left, left + vw - 1)
                ny = clamp(ny, top, top + vh - 1)
                adx, ady = nx - px, ny - py
                if ghost_drag_inject_moves:
                    inject_mouse_move_relative(adx, ady)
                else:
                    user32.SetCursorPos(nx, ny)
                ptr_state["p_pri"] = (nx, ny)

            if sec is None:
                pc = move_sensitivity_pct(False, False)
                dx, dy = scale_mouse_delta_xy(dx, dy, pc)
                _apply_merged_rel_delta(dx, dy)
                return 0

            pc = move_sensitivity_pct(False, dev_id == sec)
            dx, dy = scale_mouse_delta_xy(dx, dy, pc)
            _apply_merged_rel_delta(dx, dy)
            return 0

        if msg == WM_DESTROY:
            tray_remove(hwnd)
            hh = keyboard_ll_hook[0]
            if hh:
                user32.UnhookWindowsHookEx(hh)
                keyboard_ll_hook[0] = None
            ccos_hotkey_target[0] = 0
            _finish_second_ghost_btn_drag()
            split_snap_anim_cancel()
            _teardown_split_fx_layer(overlay_state)
            rb = red_divider_brush[0]
            if rb:
                gdi32.DeleteObject(rb)
                red_divider_brush[0] = None
            _teardown_ghost_layer_assets(overlay_state)
            oh = overlay_state.get("hwnd")
            if oh:
                register_tab_focus_skip_hwnd(0)
                user32.DestroyWindow(oh)
                overlay_state["hwnd"] = None
            user32.PostQuitMessage(0)
            return 0

        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    ghost_wc = WNDCLASSW()
    ghost_wc.style = 0  # no CS_HREDRAW/CS_VREDRAW — avoids extra full repaints
    ghost_wc.lpfnWndProc = ctypes.cast(WNDPROC(ghost_wnd_proc), ctypes.c_void_p)
    ghost_wc.cbClsExtra = 0
    ghost_wc.cbWndExtra = 0
    ghost_wc.hInstance = hinst
    ghost_wc.hIcon = None
    ghost_wc.hCursor = None
    ghost_wc.hbrBackground = gdi32.GetStockObject(NULL_BRUSH_STOCK)
    ghost_wc.lpszMenuName = None
    ghost_wc.lpszClassName = "CharaChorderDuoGhostClass"
    if not user32.RegisterClassW(ctypes.byref(ghost_wc)):
        print("RegisterClassW (2nd cursor) failed:", kernel32.GetLastError())
        return 1

    ex_style = (
        WS_EX_LAYERED
        | WS_EX_TOPMOST
        | WS_EX_TOOLWINDOW
        | WS_EX_NOACTIVATE
        | WS_EX_TRANSPARENT
    )
    _gs0 = int(overlay_state.get("ghost_size", DEFAULT_GHOST_SIZE_PX))
    ghost_hwnd = user32.CreateWindowExW(
        ex_style,
        ghost_wc.lpszClassName,
        None,
        WS_POPUP,
        0,
        0,
        _gs0,
        _gs0,
        None,
        None,
        hinst,
        None,
    )
    if not ghost_hwnd:
        print("CreateWindowExW (2nd cursor) failed:", kernel32.GetLastError())
        return 1
    _configure_ghost_popup_no_frame(int(ghost_hwnd))
    overlay_state["hwnd"] = ghost_hwnd
    register_tab_focus_skip_hwnd(int(ghost_hwnd))
    if not _build_ghost_layer_assets(overlay_state):
        print("2nd cursor bitmap init failed:", kernel32.GetLastError())
        register_tab_focus_skip_hwnd(0)
        user32.DestroyWindow(ghost_hwnd)
        return 1
    user32.ShowWindow(ghost_hwnd, SW_HIDE)

    wc = WNDCLASSW()
    wc.style = CS_HREDRAW | CS_VREDRAW
    wc.lpfnWndProc = ctypes.cast(WNDPROC(wnd_proc), ctypes.c_void_p)
    wc.cbClsExtra = 0
    wc.cbWndExtra = 0
    wc.hInstance = hinst
    wc.hIcon = None
    wc.hCursor = user32.LoadCursorW(None, IDC_ARROW)
    wc.hbrBackground = gdi32.GetStockObject(5)
    wc.lpszMenuName = None
    wc.lpszClassName = "CharaChorderDuoClass"

    atom = user32.RegisterClassW(ctypes.byref(wc))
    if not atom:
        print("RegisterClassW failed:", kernel32.GetLastError())
        user32.DestroyWindow(ghost_hwnd)
        return 1

    hwnd = user32.CreateWindowExW(
        0,
        wc.lpszClassName,
        "CharaChorder Duo",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE,
        100,
        100,
        460,
        1050,
        None,
        None,
        hinst,
        None,
    )
    if not hwnd:
        print("CreateWindowExW failed:", kernel32.GetLastError())
        user32.DestroyWindow(ghost_hwnd)
        return 1

    config_hwnd = int(hwnd)
    ccos_hotkey_target[0] = config_hwnd
    _hh = user32.SetWindowsHookExW(
        WH_KEYBOARD_LL,
        ctypes.cast(keyboard_ll_proc_keepalive[0], ctypes.c_void_p),
        kernel32.GetModuleHandleW(None),
        0,
    )
    if not _hh:
        print("SetWindowsHookExW(WH_KEYBOARD_LL) failed:", kernel32.GetLastError())
    else:
        keyboard_ll_hook[0] = _hh

    msg = MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
