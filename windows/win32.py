"""Typed Windows API bindings used by the notification-area client."""
import ctypes as C
from ctypes import wintypes as W

U, S, K, G, O = (C.WinDLL(name, use_last_error=True) for name in
                 ("user32", "shell32", "kernel32", "gdi32", "ole32"))
LRESULT = C.c_ssize_t
WNDPROC = C.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)


class WNDCLASS(C.Structure):
    _fields_ = [("style", W.UINT), ("proc", WNDPROC), ("extra", C.c_int),
        ("windowExtra", C.c_int), ("instance", W.HINSTANCE), ("icon", W.HICON),
        ("cursor", W.HANDLE), ("background", W.HBRUSH), ("menu", W.LPCWSTR), ("name", W.LPCWSTR)]


class GUID(C.Structure):
    _fields_ = [("a", W.DWORD), ("b", W.WORD), ("c", W.WORD), ("d", C.c_ubyte * 8)]


class NOTIFYICONDATA(C.Structure):
    _fields_ = [("size", W.DWORD), ("window", W.HWND), ("id", W.UINT),
        ("flags", W.UINT), ("message", W.UINT), ("icon", W.HICON),
        ("tip", W.WCHAR * 128), ("state", W.DWORD), ("mask", W.DWORD),
        ("info", W.WCHAR * 256), ("version", W.UINT), ("title", W.WCHAR * 64),
        ("infoFlags", W.DWORD), ("guid", GUID), ("balloonIcon", W.HICON)]


class ICONINFO(C.Structure):
    _fields_ = [("icon", W.BOOL), ("x", W.DWORD), ("y", W.DWORD),
                ("mask", W.HBITMAP), ("color", W.HBITMAP)]


class BROWSEINFO(C.Structure):
    _fields_ = [("owner", W.HWND), ("root", C.c_void_p), ("display", W.LPWSTR),
        ("title", W.LPCWSTR), ("flags", W.UINT), ("callback", C.c_void_p),
        ("parameter", W.LPARAM), ("image", C.c_int)]


def api(dll, name, result, *arguments):
    function = getattr(dll, name)
    function.restype, function.argtypes = result, list(arguments)
    return function


get_module = api(K, "GetModuleHandleW", W.HMODULE, W.LPCWSTR)
register_class = api(U, "RegisterClassW", W.ATOM, C.POINTER(WNDCLASS))
create_window = api(U, "CreateWindowExW", W.HWND, W.DWORD, W.LPCWSTR, W.LPCWSTR,
    W.DWORD, C.c_int, C.c_int, C.c_int, C.c_int, W.HWND, W.HMENU, W.HINSTANCE, C.c_void_p)
default_proc = api(U, "DefWindowProcW", LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
get_message = api(U, "GetMessageW", C.c_int, C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT)
translate = api(U, "TranslateMessage", W.BOOL, C.POINTER(W.MSG))
dispatch = api(U, "DispatchMessageW", LRESULT, C.POINTER(W.MSG))
post = api(U, "PostMessageW", W.BOOL, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
destroy_window = api(U, "DestroyWindow", W.BOOL, W.HWND)
post_quit = api(U, "PostQuitMessage", None, C.c_int)
set_timer = api(U, "SetTimer", C.c_size_t, W.HWND, C.c_size_t, W.UINT, C.c_void_p)
notify = api(S, "Shell_NotifyIconW", W.BOOL, W.DWORD, C.POINTER(NOTIFYICONDATA))
load_icon = api(U, "LoadIconW", W.HICON, W.HINSTANCE, C.c_void_p)
destroy_icon = api(U, "DestroyIcon", W.BOOL, W.HICON)
popup = api(U, "CreatePopupMenu", W.HMENU)
append = api(U, "AppendMenuW", W.BOOL, W.HMENU, W.UINT, C.c_size_t, W.LPCWSTR)
destroy_menu = api(U, "DestroyMenu", W.BOOL, W.HMENU)
get_cursor = api(U, "GetCursorPos", W.BOOL, C.POINTER(W.POINT))
foreground = api(U, "SetForegroundWindow", W.BOOL, W.HWND)
track = api(U, "TrackPopupMenu", W.UINT, W.HMENU, W.UINT, C.c_int, C.c_int, C.c_int, W.HWND, C.c_void_p)
message_box = api(U, "MessageBoxW", C.c_int, W.HWND, W.LPCWSTR, W.LPCWSTR, W.UINT)
taskbar_created = api(U, "RegisterWindowMessageW", W.UINT, W.LPCWSTR)("TaskbarCreated")
create_mutex = api(K, "CreateMutexW", W.HANDLE, C.c_void_p, W.BOOL, W.LPCWSTR)
close_handle = api(K, "CloseHandle", W.BOOL, W.HANDLE)
get_dc = api(U, "GetDC", W.HDC, W.HWND)
release_dc = api(U, "ReleaseDC", C.c_int, W.HWND, W.HDC)
compatible_dc = api(G, "CreateCompatibleDC", W.HDC, W.HDC)
bitmap = api(G, "CreateCompatibleBitmap", W.HBITMAP, W.HDC, C.c_int, C.c_int)
select_object = api(G, "SelectObject", W.HANDLE, W.HDC, W.HANDLE)
delete_object = api(G, "DeleteObject", W.BOOL, W.HANDLE)
delete_dc = api(G, "DeleteDC", W.BOOL, W.HDC)
background_color = api(G, "SetBkColor", W.DWORD, W.HDC, W.DWORD)
text_color = api(G, "SetTextColor", W.DWORD, W.HDC, W.DWORD)
text_out = api(G, "ExtTextOutW", W.BOOL, W.HDC, C.c_int, C.c_int, W.UINT,
               C.POINTER(W.RECT), W.LPCWSTR, W.UINT, C.c_void_p)
create_font = api(G, "CreateFontW", W.HANDLE, *([C.c_int] * 5 + [W.DWORD] * 8 + [W.LPCWSTR]))
create_bitmap = api(G, "CreateBitmap", W.HBITMAP, C.c_int, C.c_int, W.UINT, W.UINT, C.c_void_p)
create_icon = api(U, "CreateIconIndirect", W.HICON, C.POINTER(ICONINFO))
browse = api(S, "SHBrowseForFolderW", C.c_void_p, C.POINTER(BROWSEINFO))
path_from_id = api(S, "SHGetPathFromIDListW", W.BOOL, C.c_void_p, W.LPWSTR)
free_id = api(O, "CoTaskMemFree", None, C.c_void_p)


class PAINTSTRUCT(C.Structure):
    _fields_ = [("dc", W.HDC), ("erase", W.BOOL), ("rect", W.RECT),
                ("restore", W.BOOL), ("update", W.BOOL), ("reserved", W.BYTE * 32)]


class DRAWITEMSTRUCT(C.Structure):
    _fields_ = [("type", W.UINT), ("id", W.UINT), ("item", W.UINT),
                ("action", W.UINT), ("state", W.UINT), ("window", W.HWND),
                ("dc", W.HDC), ("rect", W.RECT), ("data", C.c_size_t)]


class MONITORINFO(C.Structure):
    _fields_ = [("size", W.DWORD), ("monitor", W.RECT), ("work", W.RECT), ("flags", W.DWORD)]


class NOTIFYICONIDENTIFIER(C.Structure):
    _fields_ = [("size", W.DWORD), ("window", W.HWND), ("id", W.UINT), ("guid", GUID)]


class HIGHCONTRAST(C.Structure):
    _fields_ = [("size", W.UINT), ("flags", W.DWORD), ("scheme", W.LPWSTR)]


load_image = api(U, "LoadImageW", W.HANDLE, W.HINSTANCE, W.LPCWSTR, W.UINT, C.c_int, C.c_int, W.UINT)
icon_rect = api(S, "Shell_NotifyIconGetRect", C.c_long, C.POINTER(NOTIFYICONIDENTIFIER), C.POINTER(W.RECT))
monitor_from_rect = api(U, "MonitorFromRect", W.HANDLE, C.POINTER(W.RECT), W.DWORD)
monitor_info = api(U, "GetMonitorInfoW", W.BOOL, W.HANDLE, C.POINTER(MONITORINFO))
begin_paint = api(U, "BeginPaint", W.HDC, W.HWND, C.POINTER(PAINTSTRUCT))
end_paint = api(U, "EndPaint", W.BOOL, W.HWND, C.POINTER(PAINTSTRUCT))
invalidate = api(U, "InvalidateRect", W.BOOL, W.HWND, C.POINTER(W.RECT), W.BOOL)
update_window = api(U, "UpdateWindow", W.BOOL, W.HWND)
show_window = api(U, "ShowWindow", W.BOOL, W.HWND, C.c_int)
is_visible = api(U, "IsWindowVisible", W.BOOL, W.HWND)
enable_window = api(U, "EnableWindow", W.BOOL, W.HWND, W.BOOL)
is_enabled = api(U, "IsWindowEnabled", W.BOOL, W.HWND)
set_position = api(U, "SetWindowPos", W.BOOL, W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT)
set_text = api(U, "SetWindowTextW", W.BOOL, W.HWND, W.LPCWSTR)
send = api(U, "SendMessageW", LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
send_text = api(U, "SendMessageW", LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPCWSTR)
dialog_message = api(U, "IsDialogMessageW", W.BOOL, W.HWND, C.POINTER(W.MSG))
get_focus = api(U, "GetFocus", W.HWND)
set_focus = api(U, "SetFocus", W.HWND, W.HWND)
get_ancestor = api(U, "GetAncestor", W.HWND, W.HWND, W.UINT)
system_parameters = api(U, "SystemParametersInfoW", W.BOOL, W.UINT, W.UINT, C.c_void_p, W.UINT)
system_color = api(U, "GetSysColor", W.DWORD, C.c_int)
fill_rect = api(U, "FillRect", C.c_int, W.HDC, C.POINTER(W.RECT), W.HBRUSH)
draw_text = api(U, "DrawTextW", C.c_int, W.HDC, W.LPCWSTR, C.c_int, C.POINTER(W.RECT), W.UINT)
draw_icon = api(U, "DrawIconEx", W.BOOL, W.HDC, C.c_int, C.c_int, W.HICON,
                C.c_int, C.c_int, W.UINT, W.HBRUSH, W.UINT)
draw_focus = api(U, "DrawFocusRect", W.BOOL, W.HDC, C.POINTER(W.RECT))
solid_brush = api(G, "CreateSolidBrush", W.HBRUSH, W.DWORD)
create_pen = api(G, "CreatePen", W.HANDLE, C.c_int, C.c_int, W.DWORD)
stock_object = api(G, "GetStockObject", W.HANDLE, C.c_int)
round_rect = api(G, "RoundRect", W.BOOL, W.HDC, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int)
ellipse = api(G, "Ellipse", W.BOOL, W.HDC, C.c_int, C.c_int, C.c_int, C.c_int)
move_to = api(G, "MoveToEx", W.BOOL, W.HDC, C.c_int, C.c_int, C.POINTER(W.POINT))
line_to = api(G, "LineTo", W.BOOL, W.HDC, C.c_int, C.c_int)
background_mode = api(G, "SetBkMode", C.c_int, W.HDC, C.c_int)


def enable_dpi_awareness():
    # pythonw has its own manifest. Set before creating any windows; an already-set
    # context is allowed. Windows 10 versions before 1703 use the older API.
    try:
        api(U, "SetProcessDpiAwarenessContext", W.BOOL, C.c_void_p)(C.c_void_p(-4))
    except AttributeError:
        api(U, "SetProcessDPIAware", W.BOOL)()


def dpi_for_monitor(monitor):
    try:
        shcore = C.WinDLL("shcore", use_last_error=True)
        x, y = W.UINT(), W.UINT()
        result = api(shcore, "GetDpiForMonitor", C.c_long, W.HANDLE, C.c_int,
                     C.POINTER(W.UINT), C.POINTER(W.UINT))(monitor, 0, C.byref(x), C.byref(y))
        if result == 0 and x.value:
            return x.value
    except (AttributeError, OSError):
        pass  # OS capability fallback, never a swallowed parser error.
    return 96

