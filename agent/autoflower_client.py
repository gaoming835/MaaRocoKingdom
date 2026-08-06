from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import platform
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


BUSY_STATES = {"RUNNING", "PAUSED", "STOPPING"}
TERMINAL_STATES = {"COMPLETED", "FAILED", "IDLE", "READY"}
CURSOR_TOLERANCE = 4
MAX_MOVE_STEP = 100
MAX_MOVE_ATTEMPTS = 40
MIN_MOVE_DURATION_MS = 10
MAX_MOVE_DURATION_MS = 60_000
MAX_MOUSE_DELTA = 32_767
VALID_MOUSE_BUTTONS = {"LEFT", "MIDDLE", "RIGHT"}
VALID_KEY_PATTERN = re.compile(
    r"(?:[A-Z0-9]|F(?:[1-9]|1[0-2])|"
    r"UP|DOWN|LEFT|RIGHT|HOME|END|PAGE_UP|PAGE_DOWN|"
    r"SPACE|TAB|ENTER|ESC|ESCAPE|BACKSPACE|DELETE|INSERT)"
)


class _WindowsPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _WindowsRect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class AutoFlowerError(RuntimeError):
    pass


@dataclass(frozen=True)
class AutoFlowerConfig:
    base_url: str
    pin: str
    window_title: str = "洛克王国：世界"
    recognition_width: int = 1280
    recognition_height: int = 720
    poll_timeout: float = 15.0
    poll_interval: float = 0.2
    move_duration_ms: int = 100
    script_move_step: int = 10
    script_move_duration_ms: int = 10

    @classmethod
    def load(cls, path: Path) -> "AutoFlowerConfig":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise AutoFlowerError(f"AutoFlower 配置文件不存在：{path}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise AutoFlowerError("AutoFlower 配置文件无法读取或 JSON 格式错误") from exc

        # 环境变量便于临时覆盖，同时避免把凭据写入仓库。
        base_url = os.environ.get("AUTOFLOWER_BASE_URL", data.get("base_url", ""))
        pin = os.environ.get("AUTOFLOWER_PIN", data.get("pin", ""))

        config = cls(
            base_url=str(base_url).rstrip("/"),
            pin=str(pin),
            window_title=str(data.get("window_title", "洛克王国：世界")).strip(),
            recognition_width=int(data.get("recognition_width", 1280)),
            recognition_height=int(data.get("recognition_height", 720)),
            poll_timeout=float(data.get("poll_timeout", 15)),
            poll_interval=float(data.get("poll_interval", 0.2)),
            move_duration_ms=int(data.get("move_duration_ms", 100)),
            script_move_step=int(data.get("script_move_step", 10)),
            script_move_duration_ms=int(
                data.get("script_move_duration_ms", 10)
            ),
        )
        config.validate()
        return config

    def validate(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme != "http" or not parsed.hostname:
            raise AutoFlowerError("请在本地配置中填写手机显示的 AutoFlower HTTP 地址")
        if not re.fullmatch(r"\d{6}", self.pin):
            raise AutoFlowerError("请在本地配置中填写手机显示的六位 PIN")
        if not self.window_title:
            raise AutoFlowerError("请在本地配置中填写游戏窗口标题")
        if min(self.recognition_width, self.recognition_height) <= 0:
            raise AutoFlowerError("识别分辨率必须为正整数")
        if self.poll_timeout <= 0:
            raise AutoFlowerError("状态轮询超时必须大于 0")
        if self.poll_interval <= 0:
            raise AutoFlowerError("状态轮询间隔必须大于 0")
        if not MIN_MOVE_DURATION_MS <= self.move_duration_ms <= MAX_MOVE_DURATION_MS:
            raise AutoFlowerError("鼠标移动时长必须在 10～60000 毫秒之间")
        if not 1 <= self.script_move_step <= MAX_MOUSE_DELTA:
            raise AutoFlowerError("脚本鼠标移动步长必须在 1～32767 像素之间")
        if not (
            MIN_MOVE_DURATION_MS
            <= self.script_move_duration_ms
            <= MAX_MOVE_DURATION_MS
        ):
            raise AutoFlowerError("脚本鼠标移动时长必须在 10～60000 毫秒之间")


class AutoFlowerClient:
    def __init__(self, config: AutoFlowerConfig) -> None:
        self.config = config
        self._token: str | None = None
        self._held_mouse_buttons: set[str] = set()

    @property
    def held_mouse_buttons(self) -> frozenset[str]:
        return frozenset(self._held_mouse_buttons)

    def is_game_window_foreground(self) -> bool:
        if platform.system() != "Windows":
            return False
        try:
            window = self._find_window()
        except AutoFlowerError:
            return False
        return int(ctypes.windll.user32.GetForegroundWindow()) == window

    def focus_game_window(self) -> None:
        window = self._find_window()
        self._focus_window(window)

    def game_client_rect(self) -> tuple[int, int, int, int]:
        """Return the game client area as screen x/y/width/height."""
        _, client_rect = self._get_window_client_rect()
        return client_rect

    def press_key(self, key: str) -> None:
        normalized = key.strip().upper()
        if not VALID_KEY_PATTERN.fullmatch(normalized):
            raise AutoFlowerError(f"不支持的键盘按键：{key!r}")
        self._prepare_input()
        self._send_command(f"{{KEY {normalized}}}")

    def move_relative(
        self,
        delta_x: int,
        delta_y: int,
        *,
        duration_ms: int | None = None,
        bionic: bool = False,
    ) -> None:
        delta_x = int(delta_x)
        delta_y = int(delta_y)
        if not -MAX_MOUSE_DELTA <= delta_x <= MAX_MOUSE_DELTA:
            raise AutoFlowerError("鼠标水平相对位移必须在 -32767～32767 之间")
        if not -MAX_MOUSE_DELTA <= delta_y <= MAX_MOUSE_DELTA:
            raise AutoFlowerError("鼠标垂直相对位移必须在 -32767～32767 之间")
        if delta_x == 0 and delta_y == 0:
            return

        duration = (
            self.config.move_duration_ms
            if duration_ms is None
            else int(duration_ms)
        )
        if not MIN_MOVE_DURATION_MS <= duration <= MAX_MOVE_DURATION_MS:
            raise AutoFlowerError("鼠标移动时长必须在 10～60000 毫秒之间")

        self._prepare_input()
        command = "MOVE_BIONIC" if bionic else "MOVE"
        self._send_command(f"{{{command} {delta_x} {delta_y} {duration}}}")

    def mouse_down(self, button: str = "LEFT") -> None:
        normalized = self._normalize_mouse_button(button)
        if normalized in self._held_mouse_buttons:
            return
        self._prepare_input()
        self._send_command(f"{{MOUSE_DOWN {normalized}}}")
        self._held_mouse_buttons.add(normalized)

    def mouse_up(self, button: str = "LEFT") -> None:
        normalized = self._normalize_mouse_button(button)
        if normalized not in self._held_mouse_buttons:
            return
        self._prepare_input()
        self._send_command(f"{{MOUSE_UP {normalized}}}")
        self._held_mouse_buttons.discard(normalized)

    def release_all_inputs(self) -> None:
        """尽力释放由本客户端保持的鼠标键；失败时请求远端停止并清空 HID。"""
        first_error: AutoFlowerError | None = None
        for button in tuple(self._held_mouse_buttons):
            try:
                self.mouse_up(button)
            except AutoFlowerError as exc:
                first_error = first_error or exc

        if self._held_mouse_buttons:
            try:
                self.stop_remote_task()
            except AutoFlowerError as exc:
                first_error = first_error or exc

        if first_error is not None and self._held_mouse_buttons:
            raise AutoFlowerError("AutoFlower 无法确认已释放保持中的鼠标按键") from first_error

    def stop_remote_task(self) -> None:
        if not self._token:
            self._authenticate()

        status = self._get_status()
        state = status.get("taskState")
        if state not in BUSY_STATES:
            return

        accepted = self._request(
            "POST",
            "/api/task/stop",
            expected_status=202,
        )
        if accepted.get("ok") is not True:
            raise AutoFlowerError("AutoFlower 未接受停止请求")
        self._wait_until_not_busy()
        self._held_mouse_buttons.clear()

    def click_box(self, box: tuple[int, int, int, int]) -> None:
        if not self._token:
            self._authenticate()
        status = self._get_status()
        self._ensure_available(status)

        window, client_rect = self._get_window_client_rect()
        self._focus_window(window)
        target = self._target_point(box, client_rect)
        self._move_cursor_to(target)
        self._send_command("{CLICK LEFT}")

    def click_boxes(self, boxes: list[tuple[int, int, int, int]]) -> None:
        """定位首格后，把后续相对移动与全部点击合并为一个 AFS 脚本。"""
        if not boxes:
            return

        if not self._token:
            self._authenticate()
        status = self._get_status()
        self._ensure_available(status)

        window, client_rect = self._get_window_client_rect()
        self._focus_window(window)
        targets = [self._target_point(box, client_rect) for box in boxes]
        self._move_cursor_to(targets[0])
        self._send_script(self._build_click_script(targets))

    def _build_click_script(self, targets: list[tuple[int, int]]) -> str:
        if not targets:
            raise AutoFlowerError("批量点击目标不能为空")

        commands = ["{CLICK LEFT}"]
        for previous, target in zip(targets, targets[1:]):
            delta_x = target[0] - previous[0]
            delta_y = target[1] - previous[1]
            commands.extend(self._build_script_moves(delta_x, delta_y))
            commands.append("{CLICK LEFT}")
        return "\n".join(commands)

    def _build_script_moves(self, delta_x: int, delta_y: int) -> list[str]:
        commands: list[str] = []
        remaining_x = delta_x
        remaining_y = delta_y
        max_step = self.config.script_move_step

        while remaining_x or remaining_y:
            step_x = min(max(remaining_x, -max_step), max_step)
            step_y = min(max(remaining_y, -max_step), max_step)
            commands.append(
                f"{{MOVE {step_x} {step_y} "
                f"{self.config.script_move_duration_ms}}}"
            )
            remaining_x -= step_x
            remaining_y -= step_y
        return commands

    def _authenticate(self) -> None:
        response = self._request(
            "POST",
            "/api/auth",
            {"pin": self.config.pin},
            expected_status=200,
            authenticated=False,
        )
        token = response.get("token")
        if response.get("ok") is not True or not isinstance(token, str) or not token:
            raise AutoFlowerError("AutoFlower 鉴权失败")
        self._token = token

    def _prepare_input(self) -> None:
        if not self._token:
            self._authenticate()
        self._ensure_available(self._get_status())
        self.focus_game_window()

    @staticmethod
    def _normalize_mouse_button(button: str) -> str:
        normalized = button.strip().upper()
        if normalized not in VALID_MOUSE_BUTTONS:
            raise AutoFlowerError(f"不支持的鼠标按键：{button!r}")
        return normalized

    def _get_status(self) -> dict[str, Any]:
        response = self._request("GET", "/api/status", expected_status=200)
        status = response.get("status")
        if response.get("ok") is not True or not isinstance(status, dict):
            raise AutoFlowerError("AutoFlower 返回了无效状态")
        return status

    @staticmethod
    def _ensure_available(status: dict[str, Any]) -> None:
        if status.get("connectionState") != "CONNECTED":
            raise AutoFlowerError("AutoFlower Bluetooth HID 尚未连接")
        task_state = status.get("taskState")
        if task_state in BUSY_STATES:
            raise AutoFlowerError(f"AutoFlower 当前有任务正在处理：{task_state}")

    def _target_point(
        self,
        box: tuple[int, int, int, int],
        client_rect: tuple[int, int, int, int],
    ) -> tuple[int, int]:
        if len(box) != 4:
            raise AutoFlowerError("识别框格式无效")

        x, y, width, height = (int(value) for value in box)
        if width <= 0 or height <= 0:
            raise AutoFlowerError("识别框尺寸无效")

        center_x = x + width / 2
        center_y = y + height / 2
        client_x, client_y, client_width, client_height = client_rect
        target_x = int(
            client_x
            + center_x * client_width / self.config.recognition_width
            + 0.5
        )
        target_y = int(
            client_y
            + center_y * client_height / self.config.recognition_height
            + 0.5
        )
        return target_x, target_y

    def _get_window_client_rect(self) -> tuple[int, tuple[int, int, int, int]]:
        if platform.system() != "Windows":
            raise AutoFlowerError("游戏窗口坐标映射当前仅支持 Windows")

        user32 = ctypes.windll.user32
        window = self._find_window()

        if user32.IsIconic(window):
            # FramePool 控制器退出时偶尔会让高权限游戏保持最小化。
            # 恢复动作同样走 AutoFlower HID，不使用 Win32 输入回退。
            self._send_command("{COMBO ALT+TAB}")
            time.sleep(0.3)
            if user32.IsIconic(window):
                raise AutoFlowerError("游戏窗口已最小化，AutoFlower 无法恢复")

        rect = _WindowsRect()
        origin = _WindowsPoint()
        if not user32.GetClientRect(window, ctypes.byref(rect)):
            raise AutoFlowerError("无法读取游戏窗口客户区")
        if not user32.ClientToScreen(window, ctypes.byref(origin)):
            raise AutoFlowerError("无法换算游戏窗口坐标")

        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width <= 0 or height <= 0:
            raise AutoFlowerError("游戏窗口客户区尺寸无效")
        return window, (int(origin.x), int(origin.y), width, height)

    def _find_window(self) -> int:
        if platform.system() != "Windows":
            raise AutoFlowerError("游戏窗口定位当前仅支持 Windows")

        user32 = ctypes.windll.user32
        found: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def enum_window(window: int, _: int) -> bool:
            length = user32.GetWindowTextLengthW(window)
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(window, title, length + 1)
            if title.value.strip() == self.config.window_title:
                found.append(int(window))
            return True

        user32.EnumWindows(enum_window, 0)
        if not found:
            raise AutoFlowerError(f"未找到游戏窗口：{self.config.window_title}")

        # Launchers and stale hidden windows can retain the same title. Pick
        # the visible client window, preferring the current foreground window
        # and then the largest visible candidate so HID coordinate mapping
        # uses the actual game surface seen by FramePool.
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_WindowsRect)]
        user32.GetWindowRect.restype = wintypes.BOOL
        foreground = int(user32.GetForegroundWindow())
        if foreground in found and user32.IsWindowVisible(foreground):
            return foreground

        candidates: list[tuple[int, int]] = []
        for window in found:
            if not user32.IsWindowVisible(window):
                continue
            rect = _WindowsRect()
            if user32.GetWindowRect(window, ctypes.byref(rect)):
                area = max(0, int(rect.right - rect.left)) * max(
                    0,
                    int(rect.bottom - rect.top),
                )
                candidates.append((area, window))
        if candidates:
            return max(candidates)[1]
        return found[0]

    @staticmethod
    def _focus_window(window: int) -> None:
        user32 = ctypes.windll.user32
        if int(user32.GetForegroundWindow()) == window:
            return
        user32.SetForegroundWindow(window)
        time.sleep(0.2)
        if int(user32.GetForegroundWindow()) != window:
            raise AutoFlowerError("无法将游戏窗口置于前台，已取消点击")

    def _move_cursor_to(self, target: tuple[int, int]) -> None:
        for _ in range(MAX_MOVE_ATTEMPTS):
            current_x, current_y = self._get_cursor_position()
            error_x = target[0] - current_x
            error_y = target[1] - current_y

            if (
                abs(error_x) <= CURSOR_TOLERANCE
                and abs(error_y) <= CURSOR_TOLERANCE
            ):
                return

            delta_x = min(max(error_x, -MAX_MOVE_STEP), MAX_MOVE_STEP)
            delta_y = min(max(error_y, -MAX_MOVE_STEP), MAX_MOVE_STEP)
            self._send_command(
                f"{{MOVE {delta_x} {delta_y} {self.config.move_duration_ms}}}"
            )

        current = self._get_cursor_position()
        raise AutoFlowerError(
            f"AutoFlower 鼠标定位失败，当前位置为 {current[0]},{current[1]}"
        )

    def _send_command(self, command: str) -> None:
        self._ensure_available(self._get_status())
        accepted = self._request(
            "POST",
            "/api/command",
            {"command": command},
            expected_status=202,
        )
        if accepted.get("ok") is not True:
            raise AutoFlowerError("AutoFlower 未接受鼠标命令")
        self._wait_for_completion()

    def _send_script(self, script: str) -> None:
        validation = self._request(
            "POST",
            "/api/script/validate",
            {"script": script},
            expected_status=200,
        )
        if validation.get("ok") is not True:
            raise AutoFlowerError("AutoFlower 鼠标脚本校验失败")

        self._ensure_available(self._get_status())
        accepted = self._request(
            "POST",
            "/api/task/start",
            {"script": script},
            expected_status=202,
        )
        if accepted.get("ok") is not True:
            raise AutoFlowerError("AutoFlower 未接受鼠标脚本")
        self._wait_for_completion()

    @staticmethod
    def _get_cursor_position() -> tuple[int, int]:
        if platform.system() != "Windows":
            raise AutoFlowerError("AutoFlower 鼠标闭环定位当前仅支持 Windows")

        point = _WindowsPoint()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            raise AutoFlowerError("无法读取 Windows 鼠标位置")
        return int(point.x), int(point.y)

    def _wait_for_completion(self) -> None:
        deadline = time.monotonic() + self.config.poll_timeout
        saw_busy = False

        while time.monotonic() < deadline:
            time.sleep(self.config.poll_interval)
            status = self._get_status()
            state = status.get("taskState")

            if state in BUSY_STATES:
                saw_busy = True
                continue
            if state == "FAILED":
                raise AutoFlowerError("AutoFlower 点击任务执行失败")
            if state == "COMPLETED":
                return
            if state in {"IDLE", "READY"}:
                executed = status.get("progressExecuted")
                total = status.get("progressTotal")
                if saw_busy or (
                    isinstance(executed, int)
                    and isinstance(total, int)
                    and total > 0
                    and executed >= total
                ):
                    return
            elif state not in TERMINAL_STATES:
                raise AutoFlowerError(f"AutoFlower 返回未知任务状态：{state}")

        # /api/task/start 非幂等，超时后不能自动重发。
        raise AutoFlowerError("等待 AutoFlower 点击任务完成超时，请先查询手机端状态")

    def _wait_until_not_busy(self) -> None:
        deadline = time.monotonic() + self.config.poll_timeout
        while time.monotonic() < deadline:
            time.sleep(self.config.poll_interval)
            state = self._get_status().get("taskState")
            if state not in BUSY_STATES:
                if state == "FAILED":
                    raise AutoFlowerError("AutoFlower 停止远程任务失败")
                return
        raise AutoFlowerError("等待 AutoFlower 停止远程任务超时")

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        expected_status: int,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if authenticated:
            if not self._token:
                raise AutoFlowerError("AutoFlower 会话尚未鉴权")
            headers["Authorization"] = f"Bearer {self._token}"

        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        request = urllib.request.Request(
            f"{self.config.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )

        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                status_code = response.status
                response_body = response.read()
        except urllib.error.HTTPError as exc:
            response_body = exc.read()
            error = self._decode_response(response_body)
            code = error.get("code", "HTTP_ERROR")
            raise AutoFlowerError(f"AutoFlower 请求失败：HTTP {exc.code} / {code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AutoFlowerError("无法连接 AutoFlower，未自动重发请求") from exc

        response_data = self._decode_response(response_body)
        if status_code != expected_status:
            code = response_data.get("code", "UNEXPECTED_STATUS")
            raise AutoFlowerError(
                f"AutoFlower 返回意外状态：HTTP {status_code} / {code}"
            )
        return response_data

    @staticmethod
    def _decode_response(response_body: bytes) -> dict[str, Any]:
        try:
            response = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AutoFlowerError("AutoFlower 返回的 JSON 无效") from exc
        if not isinstance(response, dict):
            raise AutoFlowerError("AutoFlower 返回的数据结构无效")
        return response
