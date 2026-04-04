"""
Dual Mouse Hands — Windows only.

Pick one mouse as the ghost pointer; every other mouse moves the real Windows
cursor. When both mice move up or down together (within ~60 ms) in merged
(single-pointer) mode, a wheel tick is sent if Double up / Double down are
set to Scroll (default); pointer movement is frozen briefly while scrolling.
Separate percentage edits set primary/ghost move speed when split (ghost
visible) and when merged. Dual-scroll wheel size has its own control. The
ghost is drawn with
UpdateLayeredWindow; that overlay is click-through and does not target UI.

Windows may still deliver wheel / extra-button messages from the second device
to whatever is under the primary cursor — only movement is fully separated.

The main window has one dropdown for the ghost mouse. Ghost size (16–160 px)
applies when you leave
the size field; the color dialog applies tint on OK (no separate apply step).

Run: python dual_mouse_hands.py
Exit: close the window or Ctrl+C in the terminal.
"""

from __future__ import annotations

import ctypes
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
WM_APP = 0x8000
# Posted from ChooseColor hook so we read rgbResult after the dialog updates it.
WM_GHOST_COLOR_SYNC = WM_APP + 120

BN_CLICKED = 0

WS_CHILD = 0x40000000
WS_TABSTOP = 0x00010000
BS_PUSHBUTTON = 0x00000000

IDC_TOGGLE_POINTERS = 1001
IDC_GHOST_SIZE_EDIT = 1002
IDC_GHOST_COLOR_BTN = 1003
IDC_GHOST_RGB_STATIC = 1004
IDC_SECONDARY_MOUSE_COMBO = 1005
# Placeholder gesture dropdowns (no behavior yet)
IDC_GESTURE_SHEAR = 1010
IDC_GESTURE_REVERSE_SHEAR = 1011
IDC_GESTURE_PINCH = 1012
IDC_GESTURE_PULL = 1013
IDC_GESTURE_DOUBLE_UP = 1014
IDC_GESTURE_DOUBLE_DOWN = 1015
IDC_GESTURE_DOUBLE_LEFT = 1016
IDC_GESTURE_DOUBLE_RIGHT = 1017
IDC_SENS_SPLIT_PRIMARY_EDIT = 1020
IDC_SENS_SPLIT_SECONDARY_EDIT = 1021
IDC_SENS_MERGED_PRIMARY_EDIT = 1022
IDC_SENS_MERGED_SECONDARY_EDIT = 1023
IDC_SENS_SCROLL_EDIT = 1024

DEFAULT_SENSITIVITY_PCT = 100
DEFAULT_MERGED_SECONDARY_PCT = 50
DEFAULT_SCROLL_PCT = 300
MIN_SENSITIVITY_PCT = 1
MAX_SENSITIVITY_PCT = 500

# Gesture combo item index: 1 = "Scroll" (double up / double down)
GESTURE_SCROLL_ITEM = 1

MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120

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
SW_SHOWNOACTIVATE = 4

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOREDRAW = 0x0008
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040

GWL_STYLE = -16

WS_BORDER = 0x00800000
WS_DLGFRAME = 0x00400000
WS_THICKFRAME = 0x00040000

HWND_TOPMOST = -1

NULL_BRUSH_STOCK = 5

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
GHOST_CURSOR_PNG = "ghost_cursor.png"

# Defaults for secondary ghost (overridden from UI / overlay_state).
DEFAULT_GHOST_SIZE_PX = 28
MIN_GHOST_SIZE_PX = 16
MAX_GHOST_SIZE_PX = 160
# Minimum high-res scratch side (must fit draw_large + margin).
GHOST_TEMP_MIN_PX = 192
# Overall opacity of the ghost pointer (0–255).
GHOST_ALPHA = 200

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
    """Send one wheel tick (delta typically ±WHEEL_DELTA)."""
    dw_data = ctypes.c_uint32(int(delta) & 0xFFFFFFFF).value
    user32.mouse_event(
        MOUSEEVENTF_WHEEL,
        0,
        0,
        dw_data,
        ctypes.c_ulonglong(0),
    )


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
    """Remove thin popup/DWM frame around the layered ghost (no visible window border)."""
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
        print("ghost_cursor.png found but Pillow is missing; run: pip install Pillow")
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
    png_path = os.path.join(script_dir, GHOST_CURSOR_PNG)
    if os.path.isfile(png_path) and _try_load_ghost_png_to_dib(png_path, bits, w):
        # Asset is already mirrored; no DrawIcon path.
        _prepare_ghost_cursor_pixels(bits, w, h, tr, tg, tb, GHOST_ALPHA)
        overlay_state["ghost_memdc"] = memdc
        overlay_state["ghost_hbmp"] = hbmp
        overlay_state["ghost_oldbmp"] = old
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

    # Append-only mouse discovery order; secondary_mouse_idx picks the ghost.
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
    dual_enabled = [False]
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
    gesture_sel: dict[int, int] = {}
    input_sensitivity: dict[str, int] = {
        "split_primary_pct": DEFAULT_SENSITIVITY_PCT,
        "split_secondary_pct": DEFAULT_SENSITIVITY_PCT,
        "merged_primary_pct": DEFAULT_SENSITIVITY_PCT,
        "merged_secondary_pct": DEFAULT_MERGED_SECONDARY_PCT,
        "scroll_pct": DEFAULT_SCROLL_PCT,
    }

    DUAL_SCROLL_SYNC_S = 0.078
    DUAL_SCROLL_MIN_DY = 2
    # Finer than WHEEL_DELTA (120) per inject + accumulator = smoother scroll than one big step.
    WHEEL_SMOOTH_CHUNK = 24
    WHEEL_SMOOTH_MAX_BURST = 12
    WHEEL_ACCUM_GAIN = 0.52
    WHEEL_ACCUM_DECAY = 0.68
    WHEEL_ACCUM_CAP = 520.0
    # After each injected wheel tick, freeze pointer positions for this long; each new
    # inject extends the window so rapid dual-scroll keeps movement suppressed.
    SCROLL_MOVE_LOCK_S = 0.10

    scroll_move_lock: dict[str, float | tuple[int, int] | None] = {
        "until": 0.0,
        "anchor_pri": None,
        "anchor_sec": None,
    }

    def maybe_dual_vertical_wheel(dev_id: int, sec_dev: int | None, dx: int, dy: int) -> bool:
        """If both mice move vertically together within a short window, inject wheel input."""
        if sec_dev is None:
            return False
        # Dual-scroll gestures apply only with a single shared cursor (merged mode).
        if dual_enabled[0]:
            return False
        if abs(dy) < max(DUAL_SCROLL_MIN_DY, abs(dx)):
            return False
        du_on = gesture_sel.get(IDC_GESTURE_DOUBLE_UP, 0) == GESTURE_SCROLL_ITEM
        dd_on = gesture_sel.get(IDC_GESTURE_DOUBLE_DOWN, 0) == GESTURE_SCROLL_ITEM
        if not du_on and not dd_on:
            return False
        now = time.perf_counter()
        st = dual_vertical_scroll
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
        synced = (
            pdy != 0
            and sdy != 0
            and pdy * sdy > 0
            and now - pt <= DUAL_SCROLL_SYNC_S
            and now - stt <= DUAL_SCROLL_SYNC_S
            and abs(pdy) >= DUAL_SCROLL_MIN_DY
            and abs(sdy) >= DUAL_SCROLL_MIN_DY
        )
        spct_f = float(
            clamp(
                int(input_sensitivity.get("scroll_pct", DEFAULT_SCROLL_PCT)),
                MIN_SENSITIVITY_PCT,
                MAX_SENSITIVITY_PCT,
            )
        )
        acc = float(st["wheel_accum"])
        if synced:
            mag = float(min(abs(pdy), abs(sdy)))
            # Negative raw dy → scroll-up (positive wheel delta); positive → scroll-down.
            direction = -1.0 if pdy < 0 else 1.0
            if direction < 0 and not du_on:
                pass
            elif direction > 0 and not dd_on:
                pass
            else:
                acc += direction * mag * (spct_f / 100.0) * WHEEL_ACCUM_GAIN
        else:
            acc *= WHEEL_ACCUM_DECAY
        acc = max(-WHEEL_ACCUM_CAP, min(WHEEL_ACCUM_CAP, acc))
        st["wheel_accum"] = acc

        injected_any = False
        burst = 0
        chunk = max(1, int(WHEEL_SMOOTH_CHUNK))
        while burst < WHEEL_SMOOTH_MAX_BURST and abs(acc) >= chunk:
            if acc < 0:
                if not du_on:
                    break
                inject_mouse_wheel_delta(chunk)
                acc += chunk
            else:
                if not dd_on:
                    break
                inject_mouse_wheel_delta(-chunk)
                acc -= chunk
            burst += 1
            injected_any = True
        st["wheel_accum"] = acc
        return injected_any

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
        """Call when a dual-scroll wheel inject just ran; snapshot pointers and extend lock."""
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
        """Snap primary (and ghost in split mode) back to pre-scroll anchors."""
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

    def consume_scroll_move_lock_if_active(injected_wheel: bool) -> bool:
        """If we just injected or lock is still active, cancel pointer drift and return True."""
        if injected_wheel:
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

    def ghost_overlay_hide() -> None:
        oh = overlay_state.get("hwnd")
        if oh and ghost_overlay_cache["shown"]:
            user32.ShowWindow(oh, SW_HIDE)
        ghost_overlay_cache["shown"] = False
        ghost_overlay_cache["x"] = None
        ghost_overlay_cache["y"] = None
        ghost_overlay_cache["side"] = None

    def sync_secondary_overlay() -> None:
        oh = overlay_state.get("hwnd")
        if not oh:
            return

        if not dual_enabled[0]:
            ghost_overlay_hide()
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

        ghost_overlay_cache["shown"] = True
        ghost_overlay_cache["x"] = ox
        ghost_overlay_cache["y"] = oy
        ghost_overlay_cache["side"] = side

    def refresh_toggle_label(parent_hwnd: int) -> None:
        btn = user32.GetDlgItem(parent_hwnd, IDC_TOGGLE_POINTERS)
        if not btn:
            return
        if dual_enabled[0]:
            user32.SetWindowTextW(btn, "Single pointer (merge)")
        else:
            user32.SetWindowTextW(btn, "Primary + ghost second mouse")

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
        v = clamp(v, MIN_SENSITIVITY_PCT, MAX_SENSITIVITY_PCT)
        input_sensitivity[key] = v
        user32.SetWindowTextW(ed, str(v))

    def move_sensitivity_pct(is_split: bool, device_is_secondary: bool) -> int:
        """Effective move % for the current pointer mode and physical device role."""
        if is_split:
            k = "split_secondary_pct" if device_is_secondary else "split_primary_pct"
            default = DEFAULT_SENSITIVITY_PCT
        else:
            k = "merged_secondary_pct" if device_is_secondary else "merged_primary_pct"
            default = (
                DEFAULT_MERGED_SECONDARY_PCT
                if device_is_secondary
                else DEFAULT_SENSITIVITY_PCT
            )
        return clamp(
            int(input_sensitivity.get(k, default)),
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
            print("Ghost cursor rebuild failed:", kernel32.GetLastError())
            return
        ghost_overlay_cache["shown"] = False
        ghost_overlay_cache["x"] = None
        ghost_overlay_cache["y"] = None
        ghost_overlay_cache["side"] = None
        sync_secondary_overlay()

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
                "Single pointer (merge)",
                WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
                18,
                48,
                424,
                30,
                hwnd,
                ctypes.c_void_p(IDC_TOGGLE_POINTERS),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "Secondary Cursor:",
                WS_CHILD | WS_VISIBLE,
                18,
                82,
                424,
                28,
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
                112,
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
                "Move sensitivity (% — 100 = normal):",
                WS_CHILD | WS_VISIBLE,
                18,
                214,
                400,
                18,
                hwnd,
                None,
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "Split (ghost visible) — primary:",
                WS_CHILD | WS_VISIBLE,
                18,
                236,
                200,
                18,
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
                218,
                232,
                52,
                24,
                hwnd,
                ctypes.c_void_p(IDC_SENS_SPLIT_PRIMARY_EDIT),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "ghost:",
                WS_CHILD | WS_VISIBLE,
                282,
                236,
                44,
                18,
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
                328,
                232,
                52,
                24,
                hwnd,
                ctypes.c_void_p(IDC_SENS_SPLIT_SECONDARY_EDIT),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "Merged (one cursor) — primary:",
                WS_CHILD | WS_VISIBLE,
                18,
                264,
                210,
                18,
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
                218,
                260,
                52,
                24,
                hwnd,
                ctypes.c_void_p(IDC_SENS_MERGED_PRIMARY_EDIT),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "2nd mouse:",
                WS_CHILD | WS_VISIBLE,
                282,
                264,
                70,
                18,
                hwnd,
                None,
                hinst,
                None,
            )
            user32.CreateWindowExW(
                WS_EX_CLIENTEDGE,
                "EDIT",
                str(DEFAULT_MERGED_SECONDARY_PCT),
                WS_CHILD
                | WS_VISIBLE
                | WS_TABSTOP
                | ES_AUTOHSCROLL
                | ES_NUMBER,
                352,
                260,
                52,
                24,
                hwnd,
                ctypes.c_void_p(IDC_SENS_MERGED_SECONDARY_EDIT),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "Dual scroll (both mice up/down):",
                WS_CHILD | WS_VISIBLE,
                18,
                292,
                220,
                18,
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
                288,
                52,
                24,
                hwnd,
                ctypes.c_void_p(IDC_SENS_SCROLL_EDIT),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "Ghost pointer look (size & color)",
                WS_CHILD | WS_VISIBLE,
                18,
                320,
                424,
                18,
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
                350,
                70,
                18,
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
                88,
                346,
                52,
                24,
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
                150,
                344,
                110,
                28,
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
                270,
                350,
                160,
                18,
                hwnd,
                ctypes.c_void_p(IDC_GHOST_RGB_STATIC),
                hinst,
                None,
            )
            user32.CreateWindowExW(
                0,
                "STATIC",
                "Gesture placeholders (not connected yet):",
                WS_CHILD | WS_VISIBLE,
                18,
                380,
                424,
                18,
                hwnd,
                None,
                hinst,
                None,
            )
            _gesture_rows = (
                (
                    (IDC_GESTURE_SHEAR, "Shear"),
                    (IDC_GESTURE_REVERSE_SHEAR, "Reverse shear"),
                ),
                (
                    (IDC_GESTURE_PINCH, "Pinch"),
                    (IDC_GESTURE_PULL, "Pull"),
                ),
                (
                    (IDC_GESTURE_DOUBLE_UP, "Double up"),
                    (IDC_GESTURE_DOUBLE_DOWN, "Double down"),
                ),
                (
                    (IDC_GESTURE_DOUBLE_LEFT, "Double left"),
                    (IDC_GESTURE_DOUBLE_RIGHT, "Double right"),
                ),
            )
            _gx1 = 18
            _gx2 = 242
            _gcw = 200
            _gch = 64
            _gy = 400
            for _row in _gesture_rows:
                user32.CreateWindowExW(
                    0,
                    "STATIC",
                    _row[0][1],
                    WS_CHILD | WS_VISIBLE,
                    _gx1,
                    _gy,
                    _gcw,
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
                    _gx1,
                    _gy + 16,
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
                    _gx2,
                    _gy + 16,
                    _gcw,
                    _gch,
                    hwnd,
                    ctypes.c_void_p(_row[1][0]),
                    hinst,
                    None,
                )
                _gy += 16 + _gch + 8
            for _idc in range(IDC_GESTURE_SHEAR, IDC_GESTURE_DOUBLE_RIGHT + 1):
                _gcb = user32.GetDlgItem(hwnd, _idc)
                if not _gcb:
                    continue
                if _idc in (IDC_GESTURE_DOUBLE_UP, IDC_GESTURE_DOUBLE_DOWN):
                    _combo_add_string(_gcb, "(none)")
                    _combo_add_string(_gcb, "Scroll")
                    user32.SendMessageW(_gcb, CB_SETCURSEL, GESTURE_SCROLL_ITEM, 0)
                    gesture_sel[_idc] = GESTURE_SCROLL_ITEM
                else:
                    _combo_add_string(_gcb, "(not wired)")
                    user32.SendMessageW(_gcb, CB_SETCURSEL, 0, 0)
                    gesture_sel[_idc] = 0
            refresh_secondary_mouse_combo(hwnd)
            refresh_ghost_rgb_label(hwnd)
            refresh_toggle_label(hwnd)
            sync_secondary_overlay()
            return 0

        if msg == WM_COMMAND:
            ctrl_id = wparam & 0xFFFF
            notify = (wparam >> 16) & 0xFFFF
            if ctrl_id == IDC_TOGGLE_POINTERS and notify == BN_CLICKED:
                dual_enabled[0] = not dual_enabled[0]
                if not dual_enabled[0]:
                    ghost_overlay_hide()
                    ptr_state["p_pri"] = None
                    ptr_state["p_sec"] = None
                else:
                    sync_secondary_overlay()
                refresh_toggle_label(hwnd)
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
                if ctrl_id == IDC_SENS_SPLIT_PRIMARY_EDIT:
                    normalize_sensitivity_edit(hwnd, ctrl_id, "split_primary_pct")
                    return 0
                if ctrl_id == IDC_SENS_SPLIT_SECONDARY_EDIT:
                    normalize_sensitivity_edit(hwnd, ctrl_id, "split_secondary_pct")
                    return 0
                if ctrl_id == IDC_SENS_MERGED_PRIMARY_EDIT:
                    normalize_sensitivity_edit(hwnd, ctrl_id, "merged_primary_pct")
                    return 0
                if ctrl_id == IDC_SENS_MERGED_SECONDARY_EDIT:
                    normalize_sensitivity_edit(hwnd, ctrl_id, "merged_secondary_pct")
                    return 0
                if ctrl_id == IDC_SENS_SCROLL_EDIT:
                    normalize_sensitivity_edit(hwnd, ctrl_id, "scroll_pct")
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
            if (
                IDC_GESTURE_SHEAR <= ctrl_id <= IDC_GESTURE_DOUBLE_RIGHT
                and notify == CBN_SELCHANGE
            ):
                _gcb = user32.GetDlgItem(hwnd, ctrl_id)
                if _gcb:
                    _gsel = int(user32.SendMessageW(_gcb, CB_GETCURSEL, 0, 0))
                    if _gsel >= 0:
                        gesture_sel[ctrl_id] = _gsel
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
                        if ptr_state["p_pri"] is not None:
                            px, py = ptr_state["p_pri"]
                            user32.SetCursorPos(px, py)
                    else:
                        ptr_state["p_pri"] = (vx, vy)
                        user32.SetCursorPos(vx, vy)
                    sync_secondary_overlay()
                    return 0

                if sec is None:
                    ptr_state["p_pri"] = (vx, vy)
                    user32.SetCursorPos(vx, vy)
                    return 0
                if dev_id == sec:
                    pc = move_sensitivity_pct(False, True)
                    pt = POINT()
                    user32.GetCursorPos(ctypes.byref(pt))
                    cx, cy = int(pt.x), int(pt.y)
                    nx = int(round(cx + (vx - cx) * pc / 100.0))
                    ny = int(round(cy + (vy - cy) * pc / 100.0))
                    nx = clamp(nx, left, left + vw - 1)
                    ny = clamp(ny, top, top + vh - 1)
                    ptr_state["p_pri"] = (nx, ny)
                    user32.SetCursorPos(nx, ny)
                else:
                    ptr_state["p_pri"] = (vx, vy)
                    user32.SetCursorPos(vx, vy)
                return 0

            dx, dy = mouse.lLastX, mouse.lLastY
            if dx == 0 and dy == 0:
                return 0

            injected_wheel = False
            if sec is not None:
                injected_wheel = maybe_dual_vertical_wheel(dev_id, sec, dx, dy)
            if consume_scroll_move_lock_if_active(injected_wheel):
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
                    if ptr_state["p_pri"] is not None:
                        px, py = ptr_state["p_pri"]
                        user32.SetCursorPos(px, py)
                else:
                    px, py = ptr_state["p_pri"]
                    nx, ny = px + dx, py + dy
                    nx = clamp(nx, left, left + vw - 1)
                    ny = clamp(ny, top, top + vh - 1)
                    ptr_state["p_pri"] = (nx, ny)
                    user32.SetCursorPos(nx, ny)
                sync_secondary_overlay()
                return 0

            if sec is None:
                pc = move_sensitivity_pct(False, False)
                dx, dy = scale_mouse_delta_xy(dx, dy, pc)
                ensure_merged_cursor_initialized()
                px, py = ptr_state["p_pri"]
                nx, ny = px + dx, py + dy
                nx = clamp(nx, left, left + vw - 1)
                ny = clamp(ny, top, top + vh - 1)
                ptr_state["p_pri"] = (nx, ny)
                user32.SetCursorPos(nx, ny)
                return 0

            ensure_merged_cursor_initialized()
            pc = move_sensitivity_pct(False, dev_id == sec)
            dx, dy = scale_mouse_delta_xy(dx, dy, pc)
            px, py = ptr_state["p_pri"]
            nx, ny = px + dx, py + dy
            nx = clamp(nx, left, left + vw - 1)
            ny = clamp(ny, top, top + vh - 1)
            ptr_state["p_pri"] = (nx, ny)
            user32.SetCursorPos(nx, ny)
            return 0

        if msg == WM_DESTROY:
            _teardown_ghost_layer_assets(overlay_state)
            oh = overlay_state.get("hwnd")
            if oh:
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
    ghost_wc.lpszClassName = "DualMouseHandsGhostClass"
    if not user32.RegisterClassW(ctypes.byref(ghost_wc)):
        print("RegisterClassW (ghost) failed:", kernel32.GetLastError())
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
        print("CreateWindowExW (ghost) failed:", kernel32.GetLastError())
        return 1
    _configure_ghost_popup_no_frame(int(ghost_hwnd))
    overlay_state["hwnd"] = ghost_hwnd
    if not _build_ghost_layer_assets(overlay_state):
        print("Ghost cursor bitmap init failed:", kernel32.GetLastError())
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
    wc.lpszClassName = "DualMouseHandsClass"

    atom = user32.RegisterClassW(ctypes.byref(wc))
    if not atom:
        print("RegisterClassW failed:", kernel32.GetLastError())
        user32.DestroyWindow(ghost_hwnd)
        return 1

    hwnd = user32.CreateWindowExW(
        0,
        wc.lpszClassName,
        "Dual Mouse Hands — 1st mouse = real pointer; 2nd = ghost (no hit)",
        WS_OVERLAPPEDWINDOW | WS_VISIBLE,
        100,
        100,
        460,
        800,
        None,
        None,
        hinst,
        None,
    )
    if not hwnd:
        print("CreateWindowExW failed:", kernel32.GetLastError())
        user32.DestroyWindow(ghost_hwnd)
        return 1

    msg = MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
