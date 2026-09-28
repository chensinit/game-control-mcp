"""Local MCP tools for selecting and controlling a Windows game window."""

from __future__ import annotations

import ctypes
import base64
import io
import sys
import time
from dataclasses import dataclass
from typing import Any

import mss
import pywinctl
from PIL import Image as PILImage
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, ImageContent, TextContent


SERVER = MCPServer(
    "game-control",
    instructions=(
        "Control one user-selected Windows game window. Call list_windows and select_window first. "
        "Call get_screenshot before clicking or swiping: click, move_mouse, tap, and swipe coordinates "
        "are pixels in the most recently returned, resized screenshot, with (0, 0) at its top-left. "
        "click, press_key, press_keys, and type_text support input_mode='window_message' to post "
        "messages directly to the selected window without moving the cursor or activating it; "
        "games using Raw Input may ignore these messages."
    ),
)

MAX_IMAGE_EDGE = 1280
JPEG_QUALITY = 80


@dataclass
class SelectedWindow:
    hwnd: int
    pid: int


@dataclass
class ScreenshotBasis:
    hwnd: int
    pid: int
    width: int
    height: int
    client_width: int
    client_height: int


class GameController:
    def __init__(self) -> None:
        self.selected: SelectedWindow | None = None
        self.screenshot_basis: ScreenshotBasis | None = None

    @staticmethod
    def require_windows() -> None:
        if sys.platform != "win32":
            raise RuntimeError("This MCP server currently supports Windows only.")

    def windows(self) -> list[Any]:
        self.require_windows()
        result = []
        for window in pywinctl.getAllWindows():
            try:
                hwnd = int(window.getHandle())
                pid = int(window.getPID())
                title = str(window.title or "").strip()
                if hwnd and pid and title and window.isAlive:
                    result.append((window, hwnd, pid, title))
            except Exception:
                continue
        return result

    def selected_window(self) -> tuple[Any, int, int, str]:
        self.require_windows()
        if self.selected is None:
            raise RuntimeError("No game window selected. Call list_windows, then select_window.")
        for window, hwnd, pid, title in self.windows():
            if hwnd == self.selected.hwnd and pid == self.selected.pid:
                return window, hwnd, pid, title
        self.selected = None
        self.screenshot_basis = None
        raise RuntimeError("The selected window is no longer available. Select a window again.")

    def focus(self, window: Any) -> None:
        if not window.isActive:
            window.activate()
            time.sleep(0.12)

    @staticmethod
    def client_rect(window: Any) -> tuple[int, int, int, int]:
        rect = window.getClientFrame()
        left, top = int(rect.left), int(rect.top)
        width, height = int(rect.right - rect.left), int(rect.bottom - rect.top)
        if width <= 0 or height <= 0:
            raise RuntimeError("The selected window has no visible client area.")
        return left, top, width, height

    def capture(self) -> tuple[bytes, int, int]:
        window, hwnd, pid, _ = self.selected_window()
        self.focus(window)
        left, top, client_width, client_height = self.client_rect(window)
        with mss.mss() as capture:
            frame = capture.grab(
                {"left": left, "top": top, "width": client_width, "height": client_height}
            )
        image = PILImage.frombytes("RGB", frame.size, frame.rgb)
        image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), PILImage.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        self.screenshot_basis = ScreenshotBasis(
            hwnd, pid, image.width, image.height, client_width, client_height
        )
        return output.getvalue(), image.width, image.height

    def client_point(self, x: int, y: int) -> tuple[int, int]:
        _, hwnd, pid, _ = self.selected_window()
        basis = self.screenshot_basis
        if basis is None or (basis.hwnd, basis.pid) != (hwnd, pid):
            raise RuntimeError("Call get_screenshot immediately before using image pixel coordinates.")
        if not 0 <= x < basis.width or not 0 <= y < basis.height:
            raise ValueError(
                f"Coordinates must be inside the latest screenshot: x=0..{basis.width - 1}, "
                f"y=0..{basis.height - 1}."
            )
        window, _, _, _ = self.selected_window()
        _, _, current_width, current_height = self.client_rect(window)
        # The game may resize between screenshot and input. Keep coordinates tied to the image.
        px = round(x * current_width / basis.width)
        py = round(y * current_height / basis.height)
        return min(px, current_width - 1), min(py, current_height - 1)

    def screen_point(self, x: int, y: int) -> tuple[int, int]:
        window, _, _, _ = self.selected_window()
        self.focus(window)
        client_x, client_y = self.client_point(x, y)
        left, top, _, _ = self.client_rect(window)
        return left + client_x, top + client_y


controller = GameController()


def post_window_message(hwnd: int, message: int, wparam: int, lparam: int) -> None:
    post_message = ctypes.windll.user32.PostMessageW
    post_message.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    post_message.restype = ctypes.c_int
    if not post_message(hwnd, message, wparam, lparam):
        raise OSError("Windows rejected a message posted to the selected window.")


@SERVER.tool()
def list_windows() -> list[dict[str, Any]]:
    """List visible Windows app windows. Use the returned window_id with select_window."""
    return [
        {
            "window_id": f"{hwnd}:{pid}",
            "title": title,
            "process_id": pid,
            "width": int(window.size[0]),
            "height": int(window.size[1]),
        }
        for window, hwnd, pid, title in controller.windows()
    ]


@SERVER.tool()
def select_window(window_id: str) -> dict[str, Any]:
    """Select a target window using its window_id from list_windows."""
    try:
        hwnd_text, pid_text = window_id.split(":", maxsplit=1)
        wanted = (int(hwnd_text), int(pid_text))
    except (ValueError, AttributeError) as exc:
        raise ValueError("window_id must be copied from list_windows, for example '12345:678'.") from exc
    for window, hwnd, pid, title in controller.windows():
        if (hwnd, pid) == wanted:
            controller.selected = SelectedWindow(hwnd, pid)
            controller.screenshot_basis = None
            return {"selected": True, "window_id": window_id, "title": title}
    raise ValueError("That window_id is no longer available. Call list_windows again.")


@SERVER.tool()
def get_screenshot() -> CallToolResult:
    """Capture the selected window as a JPEG, resized to at most 1280 px on its longest edge.

    Mouse coordinates are integer pixels in this returned image. The original aspect ratio is kept.
    Take a new screenshot after any window resize before sending more coordinates.
    """
    data, width, height = controller.capture()
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=(
                    f"Screenshot size: {width}x{height} pixels. Use these image pixel coordinates "
                    "for click and move_mouse; (0, 0) is the top-left. Coordinates refer to this "
                    "most recent screenshot."
                ),
            ),
            ImageContent(
                type="image",
                data=base64.b64encode(data).decode("ascii"),
                mime_type="image/jpeg",
            ),
        ]
    )


@SERVER.tool()
def click(
    x: int,
    y: int,
    button: str = "left",
    input_mode: str = "system",
) -> dict[str, Any]:
    """Click at latest screenshot coordinates using system input or a message sent to the window.

    input_mode='system' moves the real cursor and clicks. input_mode='window_message' posts
    mouse button messages to the selected window without moving the cursor or activating it.
    """
    if button not in {"left", "right", "middle"}:
        raise ValueError("button must be 'left', 'right', or 'middle'.")
    if input_mode not in {"system", "window_message"}:
        raise ValueError("input_mode must be 'system' or 'window_message'.")
    if input_mode == "window_message":
        window, hwnd, _, _ = controller.selected_window()
        client_x, client_y = controller.client_point(x, y)
        lparam = ((client_y & 0xFFFF) << 16) | (client_x & 0xFFFF)
        down_message, up_message, down_state = {
            "left": (0x0201, 0x0202, 0x0001),
            "right": (0x0204, 0x0205, 0x0002),
            "middle": (0x0207, 0x0208, 0x0010),
        }[button]
        post_window_message(hwnd, 0x0200, 0, lparam)  # WM_MOUSEMOVE
        post_window_message(hwnd, down_message, down_state, lparam)
        post_window_message(hwnd, up_message, 0, lparam)
        return {
            "clicked": True,
            "x": x,
            "y": y,
            "button": button,
            "input_mode": input_mode,
            "window_title": str(window.title or "").strip(),
        }

    screen_x, screen_y = controller.screen_point(x, y)
    user32 = ctypes.windll.user32
    user32.SetCursorPos(screen_x, screen_y)
    flags = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}
    down, up = flags[button]
    user32.mouse_event(down, 0, 0, 0, 0)
    user32.mouse_event(up, 0, 0, 0, 0)
    return {"clicked": True, "x": x, "y": y, "button": button, "input_mode": input_mode}


@SERVER.tool()
def move_mouse(x: int, y: int) -> dict[str, Any]:
    """Move the pointer to image-pixel coordinates from the latest screenshot."""
    screen_x, screen_y = controller.screen_point(x, y)
    ctypes.windll.user32.SetCursorPos(screen_x, screen_y)
    return {"moved": True, "x": x, "y": y}


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
    ]


class POINTER_INFO(ctypes.Structure):
    _fields_ = [
        ("pointerType", ctypes.c_ulong), ("pointerId", ctypes.c_uint),
        ("frameId", ctypes.c_uint), ("pointerFlags", ctypes.c_ulong),
        ("sourceDevice", ctypes.c_void_p), ("hwndTarget", ctypes.c_void_p),
        ("ptPixelLocation", POINT), ("ptHimetricLocation", POINT),
        ("ptPixelLocationRaw", POINT), ("ptHimetricLocationRaw", POINT),
        ("dwTime", ctypes.c_ulong), ("historyCount", ctypes.c_uint),
        ("InputData", ctypes.c_int), ("dwKeyStates", ctypes.c_ulong),
        ("PerformanceCount", ctypes.c_ulonglong),
        ("ButtonChangeType", ctypes.c_int),
    ]


class POINTER_TOUCH_INFO(ctypes.Structure):
    _fields_ = [
        ("pointerInfo", POINTER_INFO), ("touchFlags", ctypes.c_ulong),
        ("touchMask", ctypes.c_ulong), ("rcContact", RECT),
        ("rcContactRaw", RECT), ("orientation", ctypes.c_uint),
        ("pressure", ctypes.c_uint),
    ]


_touch_injection_initialized = False


def inject_touch(x: int, y: int) -> None:
    global _touch_injection_initialized
    user32 = ctypes.windll.user32
    if not _touch_injection_initialized:
        if not user32.InitializeTouchInjection(1, 1):
            raise OSError("Windows could not initialize touch input injection.")
        _touch_injection_initialized = True

    def touch(flags: int) -> POINTER_TOUCH_INFO:
        contact = RECT(x - 2, y - 2, x + 2, y + 2)
        info = POINTER_TOUCH_INFO()
        info.pointerInfo.pointerType = 2  # PT_TOUCH
        info.pointerInfo.pointerId = 1
        info.pointerInfo.pointerFlags = flags
        info.pointerInfo.ptPixelLocation = POINT(x, y)
        info.pointerInfo.ptPixelLocationRaw = POINT(x, y)
        info.touchMask = 0x1 | 0x2 | 0x4  # contact area, orientation, pressure
        info.rcContact = contact
        info.rcContactRaw = contact
        info.orientation = 90
        info.pressure = 32000
        return info

    down = touch(0x00010000 | 0x00000004 | 0x00000002)  # DOWN | INCONTACT | INRANGE
    up = touch(0x00040000)  # UP
    if not user32.InjectTouchInput(1, ctypes.byref(down)):
        raise OSError("Windows rejected touch down input.")
    time.sleep(0.04)
    if not user32.InjectTouchInput(1, ctypes.byref(up)):
        raise OSError("Windows rejected touch up input.")


def inject_touch_swipe(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    duration_ms: int,
    steps: int,
) -> None:
    global _touch_injection_initialized
    user32 = ctypes.windll.user32
    if not _touch_injection_initialized:
        if not user32.InitializeTouchInjection(1, 1):
            raise OSError("Windows could not initialize touch input injection.")
        _touch_injection_initialized = True

    def touch(x: int, y: int, flags: int) -> POINTER_TOUCH_INFO:
        contact = RECT(x - 2, y - 2, x + 2, y + 2)
        info = POINTER_TOUCH_INFO()
        info.pointerInfo.pointerType = 2  # PT_TOUCH
        info.pointerInfo.pointerId = 1
        info.pointerInfo.pointerFlags = flags
        info.pointerInfo.ptPixelLocation = POINT(x, y)
        info.pointerInfo.ptPixelLocationRaw = POINT(x, y)
        info.touchMask = 0x1 | 0x2 | 0x4  # contact area, orientation, pressure
        info.rcContact = contact
        info.rcContactRaw = contact
        info.orientation = 90
        info.pressure = 32000
        return info

    down_flags = 0x00010000 | 0x00000004 | 0x00000002  # DOWN | INCONTACT | INRANGE
    move_flags = 0x00020000 | 0x00000004 | 0x00000002  # UPDATE | INCONTACT | INRANGE
    up_flags = 0x00040000  # UP
    down = touch(start_x, start_y, down_flags)
    if not user32.InjectTouchInput(1, ctypes.byref(down)):
        raise OSError("Windows rejected touch down input for swipe.")

    try:
        delay = duration_ms / steps / 1000
        for step in range(1, steps + 1):
            ratio = step / steps
            x = round(start_x + (end_x - start_x) * ratio)
            y = round(start_y + (end_y - start_y) * ratio)
            point = touch(x, y, move_flags)
            if not user32.InjectTouchInput(1, ctypes.byref(point)):
                raise OSError("Windows rejected touch movement during swipe.")
            time.sleep(delay)
    finally:
        up = touch(end_x, end_y, up_flags)
        if not user32.InjectTouchInput(1, ctypes.byref(up)):
            raise OSError("Windows rejected touch up input after swipe.")


@SERVER.tool()
def tap(x: int, y: int) -> dict[str, Any]:
    """Send a native Windows touch tap at pixel coordinates from the latest screenshot."""
    screen_x, screen_y = controller.screen_point(x, y)
    inject_touch(screen_x, screen_y)
    return {"tapped": True, "x": x, "y": y}


@SERVER.tool()
def swipe(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    duration_ms: int = 350,
    steps: int = 12,
) -> dict[str, Any]:
    """Perform a native touch swipe between image-pixel coordinates from the latest screenshot.

    The finger stays down while moving from start to end. Duration must be 50-5000 ms
    and steps 2-100.
    """
    if not 50 <= duration_ms <= 5_000:
        raise ValueError("duration_ms must be between 50 and 5000.")
    if not 2 <= steps <= 100:
        raise ValueError("steps must be between 2 and 100.")
    start = controller.screen_point(start_x, start_y)
    end = controller.screen_point(end_x, end_y)
    inject_touch_swipe(*start, *end, duration_ms=duration_ms, steps=steps)
    return {
        "swiped": True,
        "start_x": start_x,
        "start_y": start_y,
        "end_x": end_x,
        "end_y": end_y,
        "duration_ms": duration_ms,
        "steps": steps,
    }


KEYS = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "shift": 0x10,
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "pause": 0x13,
    "caps_lock": 0x14, "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "page_up": 0x21, "page_down": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "insert": 0x2D, "delete": 0x2E,
}
KEYS.update({f"f{i}": 0x6F + i for i in range(1, 13)})


def key_code(key: str) -> tuple[int, int]:
    lowered = key.lower()
    if lowered in KEYS:
        return KEYS[lowered], 0
    if len(key) == 1:
        value = ctypes.windll.user32.VkKeyScanW(ord(key))
        if value == -1:
            raise ValueError(f"Unsupported key: {key!r}")
        return value & 0xFF, (value >> 8) & 0xFF
    raise ValueError(f"Unsupported key: {key!r}")


def send_key(key: str, pressed: bool) -> None:
    vk, modifiers = key_code(key)
    user32 = ctypes.windll.user32
    # VkKeyScanW reports shift/control/alt requirements in bits 0/1/2.
    modifier_keys = (0x10, 0x11, 0x12)
    if pressed:
        for bit, modifier_vk in enumerate(modifier_keys):
            if modifiers & (1 << bit):
                user32.keybd_event(modifier_vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 0 if pressed else 0x0002, 0)
    if not pressed:
        for bit, modifier_vk in reversed(tuple(enumerate(modifier_keys))):
            if modifiers & (1 << bit):
                user32.keybd_event(modifier_vk, 0, 0x0002, 0)


def post_virtual_key(hwnd: int, vk: int, pressed: bool) -> None:
    scan_code = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    lparam = 1 | ((scan_code & 0xFF) << 16)
    if vk in {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}:
        lparam |= 0x01000000  # Extended key
    if pressed:
        message = 0x0100  # WM_KEYDOWN
    else:
        message = 0x0101  # WM_KEYUP
        lparam |= 0xC0000000  # Previous key state and transition state
    post_window_message(hwnd, message, vk, ctypes.c_ssize_t(lparam).value)


def post_key(hwnd: int, key: str, pressed: bool) -> None:
    vk, modifiers = key_code(key)
    modifier_keys = (0x10, 0x11, 0x12)  # shift, ctrl, alt
    if pressed:
        for bit, modifier_vk in enumerate(modifier_keys):
            if modifiers & (1 << bit):
                post_virtual_key(hwnd, modifier_vk, True)
        post_virtual_key(hwnd, vk, True)
    else:
        post_virtual_key(hwnd, vk, False)
        for bit, modifier_vk in reversed(tuple(enumerate(modifier_keys))):
            if modifiers & (1 << bit):
                post_virtual_key(hwnd, modifier_vk, False)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", ctypes.c_ulong), ("union", INPUTUNION)]


def send_unicode_character(character: str) -> None:
    user32 = ctypes.windll.user32
    for flags in (0x0004, 0x0004 | 0x0002):  # KEYEVENTF_UNICODE, then KEYUP
        event = INPUT(type=1, ki=KEYBDINPUT(wVk=0, wScan=ord(character), dwFlags=flags))
        if user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) != 1:
            raise OSError("Windows rejected Unicode keyboard input.")


@SERVER.tool()
def press_key(
    key: str,
    duration_ms: int = 80,
    input_mode: str = "system",
) -> dict[str, Any]:
    """Press a key using system input or by posting keyboard messages to the selected window.

    input_mode='system' focuses the window and sends normal system keyboard input.
    input_mode='window_message' posts WM_KEYDOWN/WM_KEYUP without activating the window.
    """
    if input_mode not in {"system", "window_message"}:
        raise ValueError("input_mode must be 'system' or 'window_message'.")
    if not 10 <= duration_ms <= 10_000:
        raise ValueError("duration_ms must be between 10 and 10000.")
    key_code(key)  # Validate before posting a partial input sequence.
    window, hwnd, _, _ = controller.selected_window()
    if input_mode == "system":
        controller.focus(window)
        down = lambda: send_key(key, True)
        up = lambda: send_key(key, False)
    else:
        down = lambda: post_key(hwnd, key, True)
        up = lambda: post_key(hwnd, key, False)
    down()
    try:
        time.sleep(duration_ms / 1000)
    finally:
        up()
    return {"pressed": key, "duration_ms": duration_ms, "input_mode": input_mode}


@SERVER.tool()
def press_keys(
    keys: list[str],
    interval_ms: int = 300,
    duration_ms: int = 80,
    input_mode: str = "system",
) -> dict[str, Any]:
    """Press a sequence of keys in order, with 300 ms between press starts by default.

    Example: keys=["w", "w", "space"]. Each key is released after duration_ms.
    Coordinates are not involved. Limits: 1-100 keys, interval_ms 0-10000,
    duration_ms 10-10000. input_mode may be 'system' or 'window_message'.
    """
    if input_mode not in {"system", "window_message"}:
        raise ValueError("input_mode must be 'system' or 'window_message'.")
    if not 1 <= len(keys) <= 100:
        raise ValueError("keys must contain between 1 and 100 key names.")
    if not 0 <= interval_ms <= 10_000:
        raise ValueError("interval_ms must be between 0 and 10000.")
    if not 10 <= duration_ms <= 10_000:
        raise ValueError("duration_ms must be between 10 and 10000.")
    # Validate all names before sending any input, so a typo cannot partially run a sequence.
    for key in keys:
        key_code(key)
    window, hwnd, _, _ = controller.selected_window()
    if input_mode == "system":
        controller.focus(window)
        send_down = lambda key: send_key(key, True)
        send_up = lambda key: send_key(key, False)
    else:
        send_down = lambda key: post_key(hwnd, key, True)
        send_up = lambda key: post_key(hwnd, key, False)

    starts: list[float] = []
    for key in keys:
        now = time.monotonic()
        if starts:
            remaining = interval_ms / 1000 - (now - starts[-1])
            if remaining > 0:
                time.sleep(remaining)
        starts.append(time.monotonic())
        send_down(key)
        try:
            time.sleep(duration_ms / 1000)
        finally:
            send_up(key)
    return {
        "pressed": keys,
        "interval_ms": interval_ms,
        "duration_ms": duration_ms,
        "count": len(keys),
        "input_mode": input_mode,
    }


@SERVER.tool()
def type_text(text: str, input_mode: str = "system") -> dict[str, Any]:
    """Type text using system Unicode input or WM_CHAR messages to the selected window."""
    if input_mode not in {"system", "window_message"}:
        raise ValueError("input_mode must be 'system' or 'window_message'.")
    window, hwnd, _, _ = controller.selected_window()
    if input_mode == "system":
        controller.focus(window)
        for character in text:
            send_unicode_character(character)
    else:
        utf16 = text.encode("utf-16-le", errors="surrogatepass")
        for index in range(0, len(utf16), 2):
            code_unit = int.from_bytes(utf16[index:index + 2], "little")
            post_window_message(hwnd, 0x0102, code_unit, 1)  # WM_CHAR
    return {"typed_characters": len(text), "input_mode": input_mode}


@SERVER.tool()
def wait(seconds: float = 1.0) -> dict[str, Any]:
    """Wait for a short in-game animation or transition to finish (maximum 30 seconds)."""
    if not 0 <= seconds <= 30:
        raise ValueError("seconds must be between 0 and 30.")
    time.sleep(seconds)
    return {"waited_seconds": seconds}


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("game-control-mcp currently requires Windows.")
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    SERVER.run(transport="stdio")


if __name__ == "__main__":
    main()
