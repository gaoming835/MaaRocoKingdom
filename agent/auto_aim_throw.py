from __future__ import annotations

import ctypes
from collections import Counter
import json
import math
import os
import platform
import re
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction
from maa.pipeline import JNeuralNetworkDetect, JRecognitionType

from autoflower_client import AutoFlowerClient, AutoFlowerConfig, AutoFlowerError


VK_ESCAPE = 0x1B
SCAN_PATTERN = (35, 35, -35, -35, -35, -35, 35, 35)
MISSING_MODEL_MESSAGE = (
    "未找到精灵检测模型：请将 YOLO11n 640 ONNX 放到 "
    "model/detect/sprite.onnx"
)


class StopRequested(RuntimeError):
    pass


@dataclass(frozen=True)
class AimConfig:
    autoflower_config_path: str = "autoflower.local.json"
    detector_threshold: float = 0.4
    # Zero disables size filtering; every direct YOLO detection at the
    # confidence threshold is eligible for target selection.
    detector_min_height_pixels: int = 0
    detector_hysteresis: float = 0.2
    aim_center_x: int = 640
    aim_center_y: int = 360
    # The projectile follows an arc.  Aim at the upper body rather than the
    # geometric center; this is configurable because the required lead varies
    # with game sensitivity and target distance.
    aim_anchor_y: float = 0.25
    # Additional screen-space offset for the projectile arc. Negative values
    # put the reticle above the detection box.
    aim_vertical_offset_pixels: int = 0
    # Smaller boxes are farther away. Raise the projectile aim point for
    # those targets because the throw follows a visible arc.
    aim_distance_reference_height_pixels: int = 180
    aim_distance_raise_gain: float = 0.0
    aim_distance_raise_max_pixels: int = 0
    aim_gain_x: float = 0.50
    aim_gain_y: float = 0.50
    aim_deadzone_x: int = 18
    aim_deadzone_y: int = 24
    aim_max_step: int = 120
    aim_max_iterations: int = 24
    aim_stable_frames: int = 2
    aim_move_duration_ms: int = 20
    aim_settle_seconds: float = 0.04
    # DirectML/FramePool can occasionally yield several empty inference
    # frames while the camera settles. Keep the lock briefly before failing
    # closed so a target already being centered is not aborted by a transient
    # detector gap. A sustained miss still releases the button safely.
    aim_target_miss_tolerance_frames: int = 5
    target_lock_confirm_frames: int = 2
    aim_lead_seconds: float = 0.2
    aim_max_lead_pixels: int = 50
    # Two adjacent no-target scan steps cover roughly 90 degrees for the
    # current game sensitivity. The pattern still returns to zero net
    # displacement after a full cycle.
    scan_step: int = 35
    scan_move_duration_ms: int = 80
    scan_settle_seconds: float = 0.25
    throw_cooldown_seconds: float = 1.0
    # Kept for backwards-compatible config loading; runtime no longer limits
    # the number of throws in one task run.
    max_throw_attempts_per_activation: int = 0
    activation_timeout_seconds: float = 60.0
    foreground_grace_seconds: float = 60.0
    hotkey_poll_seconds: float = 0.05
    hotkey_debounce_seconds: float = 0.2
    ball_select_settle_seconds: float = 0.3
    ball_roi: tuple[int, int, int, int] = (1160, 470, 120, 180)
    # Keep the ball icon out of the OCR ROI.  The old wide crop allowed OCR
    # to interpret the icon artwork as a leading digit (60/260 readings).
    ball_count_roi: tuple[int, int, int, int] = (1229, 565, 36, 20)
    ball_active_min_pixels: int = 120
    debug_samples: bool = True
    debug_sample_limit: int = 200

    @classmethod
    def load(cls, path: Path) -> "AimConfig":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise AutoFlowerError(f"自动投球配置文件不存在：{path}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise AutoFlowerError("自动投球配置文件无法读取或 JSON 格式错误") from exc

        ball_roi = tuple(int(value) for value in data.get("ball_roi", cls.ball_roi))
        ball_count_roi = tuple(
            int(value) for value in data.get("ball_count_roi", cls.ball_count_roi)
        )
        config = cls(
            autoflower_config_path=str(
                data.get("autoflower_config_path", cls.autoflower_config_path)
            ),
            detector_threshold=float(
                data.get("detector_threshold", cls.detector_threshold)
            ),
            detector_min_height_pixels=int(
                data.get(
                    "detector_min_height_pixels",
                    cls.detector_min_height_pixels,
                )
            ),
            detector_hysteresis=float(
                data.get("detector_hysteresis", cls.detector_hysteresis)
            ),
            aim_center_x=int(data.get("aim_center_x", cls.aim_center_x)),
            aim_center_y=int(data.get("aim_center_y", cls.aim_center_y)),
            aim_anchor_y=float(data.get("aim_anchor_y", cls.aim_anchor_y)),
            aim_vertical_offset_pixels=int(
                data.get(
                    "aim_vertical_offset_pixels",
                    cls.aim_vertical_offset_pixels,
                )
            ),
            aim_distance_reference_height_pixels=int(
                data.get(
                    "aim_distance_reference_height_pixels",
                    cls.aim_distance_reference_height_pixels,
                )
            ),
            aim_distance_raise_gain=float(
                data.get(
                    "aim_distance_raise_gain",
                    cls.aim_distance_raise_gain,
                )
            ),
            aim_distance_raise_max_pixels=int(
                data.get(
                    "aim_distance_raise_max_pixels",
                    cls.aim_distance_raise_max_pixels,
                )
            ),
            aim_gain_x=float(data.get("aim_gain_x", cls.aim_gain_x)),
            aim_gain_y=float(data.get("aim_gain_y", cls.aim_gain_y)),
            aim_deadzone_x=int(data.get("aim_deadzone_x", cls.aim_deadzone_x)),
            aim_deadzone_y=int(data.get("aim_deadzone_y", cls.aim_deadzone_y)),
            aim_max_step=int(data.get("aim_max_step", cls.aim_max_step)),
            aim_max_iterations=int(
                data.get("aim_max_iterations", cls.aim_max_iterations)
            ),
            aim_stable_frames=int(
                data.get("aim_stable_frames", cls.aim_stable_frames)
            ),
            aim_move_duration_ms=int(
                data.get("aim_move_duration_ms", cls.aim_move_duration_ms)
            ),
            aim_settle_seconds=float(
                data.get("aim_settle_seconds", cls.aim_settle_seconds)
            ),
            aim_target_miss_tolerance_frames=int(
                data.get(
                    "aim_target_miss_tolerance_frames",
                    cls.aim_target_miss_tolerance_frames,
                )
            ),
            target_lock_confirm_frames=int(
                data.get(
                    "target_lock_confirm_frames",
                    cls.target_lock_confirm_frames,
                )
            ),
            aim_lead_seconds=float(
                data.get("aim_lead_seconds", cls.aim_lead_seconds)
            ),
            aim_max_lead_pixels=int(
                data.get("aim_max_lead_pixels", cls.aim_max_lead_pixels)
            ),
            scan_step=int(data.get("scan_step", cls.scan_step)),
            scan_move_duration_ms=int(
                data.get("scan_move_duration_ms", cls.scan_move_duration_ms)
            ),
            scan_settle_seconds=float(
                data.get("scan_settle_seconds", cls.scan_settle_seconds)
            ),
            throw_cooldown_seconds=float(
                data.get("throw_cooldown_seconds", cls.throw_cooldown_seconds)
            ),
            max_throw_attempts_per_activation=int(
                data.get(
                    "max_throw_attempts_per_activation",
                    cls.max_throw_attempts_per_activation,
                )
            ),
            activation_timeout_seconds=float(
                data.get(
                    "activation_timeout_seconds",
                    cls.activation_timeout_seconds,
                )
            ),
            foreground_grace_seconds=float(
                data.get(
                    "foreground_grace_seconds",
                    cls.foreground_grace_seconds,
                )
            ),
            hotkey_poll_seconds=float(
                data.get("hotkey_poll_seconds", cls.hotkey_poll_seconds)
            ),
            hotkey_debounce_seconds=float(
                data.get("hotkey_debounce_seconds", cls.hotkey_debounce_seconds)
            ),
            ball_select_settle_seconds=float(
                data.get(
                    "ball_select_settle_seconds",
                    cls.ball_select_settle_seconds,
                )
            ),
            ball_roi=ball_roi,
            ball_count_roi=ball_count_roi,
            ball_active_min_pixels=int(
                data.get("ball_active_min_pixels", cls.ball_active_min_pixels)
            ),
            debug_samples=bool(data.get("debug_samples", cls.debug_samples)),
            debug_sample_limit=int(
                data.get("debug_sample_limit", cls.debug_sample_limit)
            ),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if len(self.ball_roi) != 4 or min(self.ball_roi) < 0:
            raise AutoFlowerError("ball_roi 必须是四个非负整数")
        if self.ball_roi[2] <= 0 or self.ball_roi[3] <= 0:
            raise AutoFlowerError("ball_roi 宽高必须大于 0")
        if len(self.ball_count_roi) != 4 or min(self.ball_count_roi) < 0:
            raise AutoFlowerError("ball_count_roi 必须是四个非负整数")
        if self.ball_count_roi[2] <= 0 or self.ball_count_roi[3] <= 0:
            raise AutoFlowerError("ball_count_roi 宽高必须大于 0")
        if not 0 < self.detector_threshold <= 1:
            raise AutoFlowerError("detector_threshold 必须在 0～1 之间")
        if not 0 <= self.detector_min_height_pixels <= 720:
            raise AutoFlowerError(
                "detector_min_height_pixels 必须在 0～720 之间"
            )
        if not 0 <= self.detector_hysteresis <= 1:
            raise AutoFlowerError("detector_hysteresis 必须在 0～1 之间")
        if self.max_throw_attempts_per_activation < 0:
            raise AutoFlowerError(
                "max_throw_attempts_per_activation 不能为负数"
            )
        if self.activation_timeout_seconds < 0:
            raise AutoFlowerError("activation_timeout_seconds 不能为负数")
        if self.foreground_grace_seconds < 0:
            raise AutoFlowerError("foreground_grace_seconds 不能为负数")
        if not 0 <= self.aim_anchor_y <= 1:
            raise AutoFlowerError("aim_anchor_y 必须在 0～1 之间")
        if not -720 <= self.aim_vertical_offset_pixels <= 720:
            raise AutoFlowerError(
                "aim_vertical_offset_pixels 必须在 -720～720 之间"
            )
        if self.aim_distance_reference_height_pixels <= 0:
            raise AutoFlowerError(
                "aim_distance_reference_height_pixels must be greater than 0"
            )
        if not 0 <= self.aim_distance_raise_gain <= 2:
            raise AutoFlowerError(
                "aim_distance_raise_gain must be between 0 and 2"
            )
        if not 0 <= self.aim_distance_raise_max_pixels <= 720:
            raise AutoFlowerError(
                "aim_distance_raise_max_pixels must be between 0 and 720"
            )
        if min(self.aim_center_x, self.aim_center_y) < 0:
            raise AutoFlowerError("瞄准中心坐标不能为负数")
        if min(self.aim_gain_x, self.aim_gain_y) <= 0:
            raise AutoFlowerError("瞄准增益必须大于 0")
        if min(self.aim_deadzone_x, self.aim_deadzone_y) < 0:
            raise AutoFlowerError("瞄准死区不能为负数")
        if min(
            self.aim_max_step,
            self.aim_max_iterations,
            self.aim_stable_frames,
            self.scan_step,
            self.ball_active_min_pixels,
            self.debug_sample_limit,
        ) <= 0:
            raise AutoFlowerError("自动投球的计数和步长参数必须大于 0")
        if min(
            self.aim_settle_seconds,
            self.scan_settle_seconds,
            self.throw_cooldown_seconds,
            self.hotkey_poll_seconds,
            self.hotkey_debounce_seconds,
            self.ball_select_settle_seconds,
        ) < 0:
            raise AutoFlowerError("自动投球的时间参数不能为负数")
        if not 10 <= self.aim_move_duration_ms <= 60_000:
            raise AutoFlowerError("aim_move_duration_ms 必须在 10～60000 之间")
        if not 10 <= self.scan_move_duration_ms <= 60_000:
            raise AutoFlowerError("scan_move_duration_ms 必须在 10～60000 之间")
        if self.aim_lead_seconds < 0:
            raise AutoFlowerError("aim_lead_seconds 不能为负数")
        if self.aim_max_lead_pixels < 0:
            raise AutoFlowerError("aim_max_lead_pixels 不能为负数")
        if not 0 <= self.aim_target_miss_tolerance_frames <= 10:
            raise AutoFlowerError(
                "aim_target_miss_tolerance_frames 必须在 0～10 之间"
            )
        if not 1 <= self.target_lock_confirm_frames <= 5:
            raise AutoFlowerError(
                "target_lock_confirm_frames 必须在 1～5 之间"
            )


@dataclass(frozen=True)
class Target:
    box: tuple[int, int, int, int]
    score: float

    def aim_point(
        self,
        anchor_y: float = 0.25,
        vertical_offset_pixels: int = 0,
        distance_reference_height_pixels: int = 0,
        distance_raise_gain: float = 0.0,
        distance_raise_max_pixels: int = 0,
    ) -> tuple[float, float]:
        x, y, width, height = self.box
        raise_pixels = 0.0
        if distance_reference_height_pixels > 0 and distance_raise_gain > 0:
            raise_pixels = min(
                float(distance_raise_max_pixels),
                max(
                    0.0,
                    (distance_reference_height_pixels - height)
                    * distance_raise_gain,
                ),
            )
        return (
            x + width / 2,
            y + height * anchor_y + vertical_offset_pixels - raise_pixels,
        )


def annotate_target_boxes(
    image: Any,
    targets: Iterable[Target],
    selected: Target | None,
) -> Any:
    """Return a copy of ``image`` with direct-YOLO boxes drawn on it.

    Detection boxes use the fixed 1280×720 recognition coordinate system,
    while a FramePool screenshot may have a different physical size. The
    helper scales and clips each box and intentionally has no dependency on
    OpenCV or a display window, so it is safe to use in diagnostic paths.
    """
    if image is None:
        return image
    try:
        source = numpy.asarray(image)
    except (TypeError, ValueError):
        return image
    if source.ndim < 3 or source.shape[2] < 3:
        return numpy.array(source, copy=True)

    annotated = numpy.array(source, copy=True)
    image_height, image_width = annotated.shape[:2]
    if image_height <= 0 or image_width <= 0:
        return annotated

    scale_x = image_width / 1280.0
    scale_y = image_height / 720.0
    for candidate in targets:
        try:
            x, y, box_width, box_height = (
                int(value) for value in candidate.box
            )
        except (AttributeError, TypeError, ValueError):
            continue
        if box_width <= 0 or box_height <= 0:
            continue

        left = max(0, min(image_width - 1, round(x * scale_x)))
        top = max(0, min(image_height - 1, round(y * scale_y)))
        right = max(
            left + 1,
            min(image_width, round((x + box_width) * scale_x)),
        )
        bottom = max(
            top + 1,
            min(image_height, round((y + box_height) * scale_y)),
        )
        if right <= left or bottom <= top:
            continue

        # Maa screenshots are BGR arrays. Green marks the final target;
        # yellow marks the other direct-YOLO candidates in that same frame.
        color = (0, 255, 0) if candidate is selected else (0, 220, 255)
        thickness = min(4, right - left, bottom - top)
        if thickness <= 0:
            continue
        color_array = numpy.asarray(color, dtype=annotated.dtype)
        annotated[top : top + thickness, left:right, :3] = color_array
        annotated[bottom - thickness : bottom, left:right, :3] = color_array
        annotated[top:bottom, left : left + thickness, :3] = color_array
        annotated[top:bottom, right - thickness : right, :3] = color_array
    return annotated


class KeyEdgeDetector:
    def __init__(
        self,
        reader: Callable[[int], bool],
        clock: Callable[[], float] = time.monotonic,
        debounce_seconds: float = 0.2,
    ) -> None:
        self._reader = reader
        self._clock = clock
        self._debounce_seconds = debounce_seconds
        self._previous: dict[int, bool] = {}
        self._last_edge: dict[int, float] = {}

    def rising(self, virtual_key: int) -> bool:
        down = bool(self._reader(virtual_key))
        previous = self._previous.get(virtual_key, False)
        self._previous[virtual_key] = down
        if not down or previous:
            return False

        now = self._clock()
        last = self._last_edge.get(virtual_key, -math.inf)
        if now - last < self._debounce_seconds:
            return False
        self._last_edge[virtual_key] = now
        return True


def build_scan_pattern(step: int) -> tuple[int, ...]:
    return (
        step,
        step,
        -step,
        -step,
        -step,
        -step,
        step,
        step,
    )


def detector_model_exists() -> bool:
    project_root = Path(__file__).resolve().parent.parent
    candidates = (
        project_root / "assets" / "resource" / "model" / "detect" / "sprite.onnx",
        project_root / "resource" / "model" / "detect" / "sprite.onnx",
    )
    return any(path.is_file() for path in candidates)


def require_directml(context: Context) -> None:
    """强制本任务使用 DirectML；失败时不允许回退到 CPU。

    Custom actions run in Maa's AgentServer process.  Its resource handle is a
    remote proxy and cannot change inference options, so the host process must
    configure DirectML before binding the agent.  A failed option call in the
    AgentServer is therefore accepted only in that process; local resources
    still fail closed.
    """
    if os.environ.get("MAA_AGENT_SERVER_PROCESS") == "1":
        print("[自动投球] DirectML GPU 由宿主 Maa 资源预配置")
        return

    try:
        resource = context.tasker.resource
        enabled = resource is not None and resource.use_directml()
    except Exception as exc:
        raise AutoFlowerError("无法启用 DirectML GPU 推理，任务拒绝启动") from exc
    if not enabled:
        raise AutoFlowerError("无法启用 DirectML GPU 推理，任务拒绝启动")
    print("[自动投球] 推理后端：DirectML GPU")


def choose_target(
    targets: Iterable[Target],
    *,
    aim_center: tuple[int, int] = (640, 360),
    anchor_y: float = 0.25,
    vertical_offset_pixels: int = 0,
    distance_reference_height_pixels: int = 0,
    distance_raise_gain: float = 0.0,
    distance_raise_max_pixels: int = 0,
    previous: Target | None = None,
    hysteresis: float = 0.2,
) -> Target | None:
    candidates = list(targets)
    if not candidates:
        return None

    center_x, center_y = aim_center

    def point(target: Target) -> tuple[float, float]:
        return target.aim_point(
            anchor_y,
            vertical_offset_pixels,
            distance_reference_height_pixels,
            distance_raise_gain,
            distance_raise_max_pixels,
        )

    def aim_distance(target: Target) -> float:
        target_x, target_y = point(target)
        return math.hypot(target_x - center_x, target_y - center_y)

    nearest = min(candidates, key=aim_distance)
    if previous is None:
        return nearest

    previous_x, previous_y = point(previous)
    tracked = min(
        candidates,
        key=lambda target: math.hypot(point(target)[0] - previous_x, point(target)[1] - previous_y),
    )
    tracked_x, tracked_y = point(tracked)
    track_distance = math.hypot(tracked_x - previous_x, tracked_y - previous_y)
    # Keep the lock on the spatially continuous box.  A second sprite can be
    # closer to the crosshair after a camera move; switching to it mid-aim
    # makes the camera oscillate and prevents convergence.  The generous
    # limit covers one HID step plus normal target motion, while a larger
    # jump is treated as a miss and handled by the bounded grace window.
    track_limit = max(360.0, math.hypot(tracked.box[2], tracked.box[3]) * 3.5)
    # During closed-loop aiming, never jump from a locked target to a
    # distant low-confidence box after a transient empty frame. Treat that
    # frame as a miss so the caller's bounded miss tolerance can recover or
    # release safely.
    if track_distance > track_limit:
        if tracked.score < previous.score * (1 - hysteresis):
            return None
        return nearest
    if track_distance <= track_limit:
        return tracked
    return None


def calculate_aim_move(
    target_point: tuple[float, float],
    config: AimConfig,
) -> tuple[int, int, bool]:
    error_x = target_point[0] - config.aim_center_x
    error_y = target_point[1] - config.aim_center_y
    inside = (
        abs(error_x) <= config.aim_deadzone_x
        and abs(error_y) <= config.aim_deadzone_y
    )
    if inside:
        return 0, 0, True

    # AutoFlower's relative HID move rotates the camera opposite to the
    # target's screen error, bringing the detected point toward the fixed
    # crosshair.
    move_x = round(error_x * config.aim_gain_x)
    move_y = round(error_y * config.aim_gain_y)
    move_x = min(max(move_x, -config.aim_max_step), config.aim_max_step)
    move_y = min(max(move_y, -config.aim_max_step), config.aim_max_step)
    if move_x == 0 and abs(error_x) > config.aim_deadzone_x:
        move_x = 1 if error_x > 0 else -1
    if move_y == 0 and abs(error_y) > config.aim_deadzone_y:
        move_y = 1 if error_y > 0 else -1
    return move_x, move_y, False


def ball_icon_is_active(image: Any, config: AimConfig) -> bool:
    if image is None or not hasattr(image, "shape") or len(image.shape) < 3:
        return False

    x, y, width, height = config.ball_roi
    image_height, image_width = image.shape[:2]
    right = min(x + width, image_width)
    bottom = min(y + height, image_height)
    if x >= right or y >= bottom:
        return False

    crop = image[y:bottom, x:right, :3]
    channel_0 = crop[:, :, 0].astype("int16")
    green = crop[:, :, 1].astype("int16")
    channel_2 = crop[:, :, 2].astype("int16")
    purple = (
        (channel_0 >= 90)
        & (channel_2 >= 110)
        & (channel_0 + channel_2 >= green * 2 + 50)
    )
    return int(purple.sum()) >= config.ball_active_min_pixels


@dataclass(frozen=True)
class BallCount:
    value: int
    capped: bool = False


def parse_ball_count_text(texts: Iterable[str]) -> BallCount | None:
    """Parse the numeric ball counter returned by Maa OCR.

    A suffix such as ``+`` means the UI is capped (for example ``999+``),
    which cannot prove that one ball was consumed while it remains capped.
    """
    for text in texts:
        normalized = str(text).replace("，", ",").replace("+", "+")
        match = re.search(r"(\d{1,6})(\s*\+)?", normalized)
        if match:
            return BallCount(int(match.group(1)), bool(match.group(2)))
    return None


def ball_count_decreased(before: BallCount, after: BallCount) -> bool:
    if before.capped and after.capped:
        return False
    # A single throw should consume one item. Allow a small OCR digit error,
    # but reject a huge apparent drop (for example 263 -> 162) as a read of
    # unrelated UI text rather than a capture result.
    drop = before.value - after.value
    return 0 < drop <= 8


def ball_count_consensus(readings: Iterable[BallCount]) -> BallCount | None:
    """Return the most repeated OCR value from a short stable sample.

    The counter is rendered in a small outlined font. A single OCR frame can
    therefore produce a spurious leading digit. Taking a short consensus is
    safer than treating the first parse as truth, while preserving the
    capped-state bit used for ``999+``.
    """
    values = list(readings)
    if not values:
        return None
    counts = Counter((item.value, item.capped) for item in values)
    best_count = max(counts.values())
    for item in reversed(values):
        if counts[(item.value, item.capped)] == best_count:
            return item
    return None


class AutoAimRuntime:
    def __init__(
        self,
        context: Context,
        client: AutoFlowerClient,
        config: AimConfig,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        key_reader: Callable[[int], bool] | None = None,
    ) -> None:
        self.context = context
        self.client = client
        self.config = config
        self.sleep = sleep
        self.clock = clock
        self.active = False
        self.scan_index = 0
        self._throw_attempts = 0
        self._last_throw_failure_reason: str | None = None
        self._activation_started_at: float | None = None
        self._no_target_started_at: float | None = None
        self._task_started_at: float | None = None
        self._background_exit_logged = False
        self._foreground_error_logged = False
        self._stop_requested = False
        self._detector_enabled = detector_model_exists()
        self._debug_sequence = 0
        self._last_detected_targets: list[Target] = []
        self._keys = KeyEdgeDetector(
            key_reader or self._read_virtual_key,
            clock,
            config.hotkey_debounce_seconds,
        )

    def run(self) -> bool:
        if not self._detector_enabled:
            raise AutoFlowerError(MISSING_MODEL_MESSAGE)
        if platform.system() != "Windows":
            raise AutoFlowerError("自动瞄准投球当前仅支持 Windows")

        # The task is armed as soon as Maa starts it. Esc is the only
        # keyboard control.
        self._task_started_at = self.clock()
        self._start_activation()
        print("[自动投球] 已启动并开始运行（YOLO 640），Esc 急停")
        try:
            while not self.context.tasker.stopping and not self._stop_requested:
                event = self._poll_control_event()
                if event == "stop":
                    self._stop_requested = True
                    break

                try:
                    foreground = self.client.is_game_window_foreground()
                except Exception as exc:
                    # A transient Win32/API failure must not escape Maa's
                    # ctypes callback and terminate the whole custom action.
                    # Treat it as background until the next poll; the normal
                    # grace-period/cleanup path still applies.
                    if not self._foreground_error_logged:
                        self._foreground_error_logged = True
                        print(
                            f"[自动投球] 前台窗口检测暂时失败，将重试：{exc}"
                        )
                    self.sleep(self.config.hotkey_poll_seconds)
                    continue
                if not foreground:
                    if self._background_grace_expired(foreground=False):
                        self._stop_requested = True
                        break
                    self.sleep(self.config.hotkey_poll_seconds)
                    continue
                if not self.active:
                    self.sleep(self.config.hotkey_poll_seconds)
                    continue

                try:
                    if self._pause_if_activation_timed_out():
                        continue

                    image = self._capture()
                    target = self._observe_target(image)
                    if target is None:
                        self._scan_once()
                        continue

                    # Require a short consecutive lock before sending any
                    # input. This prevents a just-captured/transitioning
                    # sprite from starting the next throw and then vanishing
                    # on the first held-button frame.
                    lock_image = image
                    for _ in range(self.config.target_lock_confirm_frames - 1):
                        self._wait_interruptible(self.config.aim_settle_seconds)
                        lock_image = self._capture()
                        target = self._observe_target(lock_image, previous=target)
                        if target is None:
                            break
                    if target is None:
                        continue

                    self._save_target_selection_sample(
                        lock_image,
                        self._last_detected_targets,
                        target,
                    )
                    self._ensure_ball_selected(lock_image)
                    thrown = self._attempt_throw(target)
                except StopRequested:
                    self._stop_requested = True
                except AutoFlowerError as exc:
                    self._pause_activation()
                    print(f"[自动投球] 已暂停：{exc}")

            return True
        finally:
            self._release_inputs_safely()
            print("[自动投球] 已结束")

    def _start_activation(self) -> None:
        self.active = True
        self._throw_attempts = 0
        self._activation_started_at = self.clock()
        self._no_target_started_at = None

    def _pause_activation(self) -> None:
        self.active = False
        self._activation_started_at = None
        self._no_target_started_at = None

    def _pause_if_activation_timed_out(self) -> bool:
        timeout = self.config.activation_timeout_seconds
        if (
            timeout <= 0
            or self._no_target_started_at is None
            or self.clock() - self._no_target_started_at < timeout
        ):
            return False

        while self.scan_index != 0:
            self._scan_once()
        self._pause_activation()
        print(
            f"[自动投球] {timeout:g} 秒内未发现合格目标，扫描已回正并自动暂停"
        )
        return True

    def _attempt_throw(self, target: Target) -> bool:
        if not self.active:
            return False

        self._throw_attempts += 1
        print(f"[自动投球] 投球尝试 {self._throw_attempts}/不限")

        self._last_throw_failure_reason = None
        before_count = self._read_ball_count_retry()
        thrown = self._throw_at(target)
        if thrown and before_count is not None:
            after_count = self._wait_for_ball_count_change(before_count)
            if after_count is not None and ball_count_decreased(before_count, after_count):
                print(
                    f"[自动投球] 捕捉成功，球数 {before_count.value} -> {after_count.value}"
                )
                print("[自动投球] 投球完成")
                return True
            self._last_throw_failure_reason = "miss"
            if after_count is None:
                print("[自动投球] 投球完成但未能读取投球后球数，返回搜索")
            else:
                print(
                    f"[自动投球] 未命中，球数未减少（仍为 {after_count.value}），返回搜索"
                )
            self._wait_interruptible(self.config.throw_cooldown_seconds)
            return False
        if not thrown:
            if self._last_throw_failure_reason == "aim_timeout":
                print("[自动投球] 瞄准迭代超时，已释放左键并返回搜索")
            elif self._last_throw_failure_reason == "target_lost":
                print("[自动投球] 目标在瞄准中连续丢失，已释放左键并返回搜索")
            # A failed attempt has already released all inputs. Keep the
            # activation alive so the no-target scanner can turn to the next
            # sector and find another sprite; the global no-target timeout
            # still stops the loop after a prolonged empty scene.
            self._wait_interruptible(self.config.throw_cooldown_seconds)
        if thrown and before_count is None:
            print("[自动投球] 投球完成（球数未确认）")
        return thrown

    def _background_grace_expired(self, *, foreground: bool | None = None) -> bool:
        if foreground is None:
            foreground = self.client.is_game_window_foreground()
        if foreground or self._task_started_at is None:
            return False
        grace = self.config.foreground_grace_seconds
        if self.clock() - self._task_started_at < grace:
            return False
        if not self._background_exit_logged:
            self._background_exit_logged = True
            print(
                f"[自动投球] 游戏窗口启动后超过 {grace:g} 秒仍未在前台，自动退出"
            )
        return True

    def _ensure_ball_selected(self, image: Any) -> None:
        if ball_icon_is_active(image, self.config):
            return

        self.client.press_key("E")
        self._wait_interruptible(self.config.ball_select_settle_seconds)
        verification = self._capture()
        if not ball_icon_is_active(verification, self.config):
            self._save_debug_sample(verification, "ball_not_selected")
            raise AutoFlowerError("按 E 后仍未识别到已选中的球")

    def _read_ball_count(self, image: Any) -> BallCount | None:
        try:
            detail = self.context.run_recognition(
                "AutoAimThrow.BallCount",
                self._prepare_ball_count_image(image),
            )
        except Exception:
            return None
        if detail is None:
            return None
        texts: list[str] = []
        for result in getattr(detail, "all_results", ()):
            text = getattr(result, "text", None)
            if text:
                texts.append(str(text))
        count = parse_ball_count_text(texts)
        if count is not None:
            print(
                f"[自动投球] 球数 OCR: {count.value}{'+' if count.capped else ''}"
            )
        elif texts:
            print(f"[自动投球] 球数 OCR 未解析文本: {texts}")
        return count

    def _prepare_ball_count_image(self, image: Any) -> Any:
        """Upscale and isolate the small HUD counter before Maa OCR.

        The counter is rendered at roughly 12px high and overlaps the lower
        edge of the ball icon. OCR on the full HUD ROI frequently returns a
        glyph from the icon as a leading digit. A fixed, high-contrast crop is
        enlarged four times and placed in a white canvas consumed by the
        dedicated pipeline node. This does not alter the screenshot used by
        YOLO detection.
        """
        if image is None or not hasattr(image, "shape") or len(image.shape) < 3:
            return image
        x, y, width, height = self.config.ball_count_roi
        image_height, image_width = image.shape[:2]
        right = min(x + width, image_width)
        bottom = min(y + height, image_height)
        if x >= right or y >= bottom:
            return image
        crop = image[y:bottom, x:right, :3]
        if crop.size == 0:
            return image
        gray = crop.astype("float32").mean(axis=2)
        binary = numpy.where(gray[..., None] < 190, 0, 255).astype("uint8")
        enlarged = numpy.repeat(numpy.repeat(binary, 4, axis=0), 4, axis=1)
        canvas = numpy.full((100, 180, 3), 255, dtype="uint8")
        copy_height = min(canvas.shape[0], enlarged.shape[0])
        copy_width = min(canvas.shape[1], enlarged.shape[1])
        canvas[:copy_height, :copy_width] = enlarged[:copy_height, :copy_width]
        return canvas

    def _read_ball_count_retry(self, attempts: int = 4) -> BallCount | None:
        readings: list[BallCount] = []
        for index in range(max(1, attempts)):
            self._check_interrupt()
            count = self._read_ball_count(self._capture())
            if count is not None:
                readings.append(count)
            if index + 1 < attempts:
                self._wait_interruptible(0.10)
        return ball_count_consensus(readings)

    def _wait_for_ball_count_change(self, before: BallCount) -> BallCount | None:
        last: BallCount | None = None
        repeated: BallCount | None = None
        repeated_frames = 0
        deadline = self.clock() + max(
            0.5,
            min(2.0, self.config.throw_cooldown_seconds + 0.5),
        )
        while self.clock() < deadline:
            self._check_interrupt()
            last = self._read_ball_count(self._capture())
            if last is not None:
                if repeated == last:
                    repeated_frames += 1
                else:
                    repeated = last
                    repeated_frames = 1
                if repeated_frames >= 2 and ball_count_decreased(before, last):
                    return last
            self._wait_interruptible(0.2)
        return last

    def _throw_at(self, initial_target: Target) -> bool:
        previous = initial_target
        history: list[tuple[float, tuple[float, float]]] = []
        stable_frames = 0
        missed_frames = 0
        released = False

        self.client.mouse_down("LEFT")
        try:
            self._wait_interruptible(self.config.aim_settle_seconds)
            for _ in range(self.config.aim_max_iterations):
                self._check_interrupt()
                image = self._capture()
                target = self._observe_target(image, previous=previous)
                if target is None:
                    missed_frames += 1
                    tolerance = self.config.aim_target_miss_tolerance_frames
                    if missed_frames <= tolerance:
                        # FramePool/YOLO can transiently return an empty frame
                        # immediately after a camera move. Keep the last lock
                        # briefly, but never move or release based on a stale
                        # target; a sustained miss still fails closed below.
                        if missed_frames == 1:
                            print(
                                "[自动投球] 瞄准中检测暂时丢帧，等待下一帧确认"
                            )
                        self._wait_interruptible(self.config.aim_settle_seconds)
                        continue
                    self._save_debug_sample(image, "target_lost")
                    self._last_throw_failure_reason = "target_lost"
                    print(
                        f"[自动投球] 瞄准中连续 {missed_frames} 帧未检测到目标，安全释放"
                    )
                    return False

                missed_frames = 0

                now = self.clock()
                raw_point = target.aim_point(
                    self.config.aim_anchor_y,
                    self.config.aim_vertical_offset_pixels,
                    self.config.aim_distance_reference_height_pixels,
                    self.config.aim_distance_raise_gain,
                    self.config.aim_distance_raise_max_pixels,
                )
                history.append((now, raw_point))
                history = history[-3:]
                lead_x, lead_y = self._estimate_lead(history)
                target_point = (raw_point[0] + lead_x, raw_point[1] + lead_y)
                move_x, move_y, inside = calculate_aim_move(target_point, self.config)

                if inside:
                    stable_frames += 1
                    if stable_frames >= self.config.aim_stable_frames:
                        if self.config.debug_samples:
                            try:
                                self._save_debug_sample(image, "aim_ready")
                            except AutoFlowerError as exc:
                                print(
                                    f"[自动投球] 警告：瞄准诊断截图保存失败：{exc}"
                                )
                        self.client.mouse_up("LEFT")
                        released = True
                        if self.config.debug_samples:
                            try:
                                self._save_debug_sample(
                                    self._capture(),
                                    "throw_completed",
                                )
                            except AutoFlowerError as exc:
                                print(
                                    f"[自动投球] 警告：投球完成截图保存失败：{exc}"
                                )
                        print("[自动投球] 投球动作完成，等待球数确认")
                        self._wait_interruptible(
                            self.config.throw_cooldown_seconds
                        )
                        return True
                else:
                    stable_frames = 0
                    self.client.move_relative(
                        move_x,
                        move_y,
                        duration_ms=self.config.aim_move_duration_ms,
                    )
                    # 本次观测后的位移来自镜头控制，不能用于估计精灵自身速度。
                    history.clear()
                    self._wait_interruptible(self.config.aim_settle_seconds)
                previous = target

            self._last_throw_failure_reason = "aim_timeout"
            self._save_debug_sample(self._capture(), "aim_timeout")
            print("[自动投球] 瞄准迭代超时，目标仍未进入死区")
            return False
        finally:
            if not released:
                self._release_inputs_safely()

    def _estimate_lead(
        self,
        history: list[tuple[float, tuple[float, float]]],
    ) -> tuple[float, float]:
        if len(history) < 2 or self.config.aim_lead_seconds <= 0:
            return 0.0, 0.0
        first_time, first_point = history[-2]
        last_time, last_point = history[-1]
        elapsed = last_time - first_time
        if elapsed <= 0:
            return 0.0, 0.0

        lead_x = (last_point[0] - first_point[0]) / elapsed
        lead_y = (last_point[1] - first_point[1]) / elapsed
        lead_x *= self.config.aim_lead_seconds
        lead_y *= self.config.aim_lead_seconds
        maximum = self.config.aim_max_lead_pixels
        return (
            min(max(lead_x, -maximum), maximum),
            min(max(lead_y, -maximum), maximum),
        )

    def _scan_once(self) -> None:
        self._check_interrupt()
        pattern = build_scan_pattern(self.config.scan_step)
        delta_x = pattern[self.scan_index]
        self.scan_index = (self.scan_index + 1) % len(pattern)
        self.client.move_relative(
            delta_x,
            0,
            duration_ms=self.config.scan_move_duration_ms,
        )
        self._wait_interruptible(self.config.scan_settle_seconds)

    def _observe_target(
        self,
        image: Any,
        *,
        previous: Target | None = None,
    ) -> Target | None:
        targets = self._run_detector(image)
        self._last_detected_targets = list(targets)
        selected = choose_target(
            targets,
            aim_center=(self.config.aim_center_x, self.config.aim_center_y),
            anchor_y=self.config.aim_anchor_y,
            vertical_offset_pixels=self.config.aim_vertical_offset_pixels,
            distance_reference_height_pixels=self.config.aim_distance_reference_height_pixels,
            distance_raise_gain=self.config.aim_distance_raise_gain,
            distance_raise_max_pixels=self.config.aim_distance_raise_max_pixels,
            previous=previous,
            hysteresis=self.config.detector_hysteresis,
        )
        if selected is None:
            if self._no_target_started_at is None:
                self._no_target_started_at = self.clock()
        else:
            self._no_target_started_at = None
        return selected

    def _save_target_selection_sample(
        self,
        image: Any,
        targets: Iterable[Target],
        selected: Target,
    ) -> None:
        """Best-effort diagnostic image saved immediately before ball select."""
        if not self.config.debug_samples:
            return
        try:
            marked = annotate_target_boxes(image, targets, selected)
            self._save_debug_sample(marked, "target_selected")
        except Exception as exc:
            # Diagnostics must never prevent E/LEFT input or alter targeting.
            print(f"[自动投球] 警告：目标选择截图保存失败：{exc}")

    def _run_detector(self, image: Any) -> list[Target]:
        detail = self.context.run_recognition_direct(
            JRecognitionType.NeuralNetworkDetect,
            JNeuralNetworkDetect(
                model="sprite.onnx",
                expected=[0],
                labels=["sprite"],
                threshold=[self.config.detector_threshold],
                order_by="Score",
            ),
            image,
        )
        if detail is None:
            raise AutoFlowerError("YOLO 精灵识别未能启动")

        targets: list[Target] = []
        for result in detail.all_results:
            if not hasattr(result, "box") or not hasattr(result, "score"):
                continue
            score = float(result.score)
            if score < self.config.detector_threshold:
                continue
            box = tuple(int(value) for value in result.box)
            if box[3] < self.config.detector_min_height_pixels:
                continue
            targets.append(
                Target(
                    box,
                    score,
                )
            )
        return targets

    def _capture(self) -> Any:
        screenshot = self.context.tasker.controller.post_screencap().wait()
        if not screenshot.succeeded:
            raise AutoFlowerError("读取游戏画面失败")
        return screenshot.get()

    def _poll_control_event(self) -> str | None:
        if self._keys.rising(VK_ESCAPE):
            return "stop"
        return None

    def _check_interrupt(self) -> None:
        if self.context.tasker.stopping:
            raise StopRequested
        if self._background_grace_expired():
            raise StopRequested
        event = self._poll_control_event()
        if event == "stop":
            raise StopRequested

    def _wait_interruptible(self, seconds: float) -> None:
        deadline = self.clock() + seconds
        while self.clock() < deadline:
            self._check_interrupt()
            remaining = deadline - self.clock()
            self.sleep(min(self.config.hotkey_poll_seconds, max(remaining, 0)))

    def _release_inputs_safely(self) -> None:
        try:
            self.client.release_all_inputs()
        except AutoFlowerError as exc:
            print(f"[自动投球] 警告：{exc}")

    def _save_debug_sample(self, image: Any, reason: str) -> None:
        if not self.config.debug_samples:
            return
        try:
            debug_dir = (
                Path(__file__).resolve().parent.parent
                / "debug"
                / "auto_aim_throw"
            )
            debug_dir.mkdir(parents=True, exist_ok=True)
            self._debug_sequence += 1
            timestamp = int(time.time() * 1000)
            path = debug_dir / f"{timestamp}_{self._debug_sequence:04d}_{reason}.bmp"
            self._write_bmp(image, path)

            samples = sorted(
                debug_dir.glob("*.bmp"),
                key=lambda candidate: candidate.stat().st_mtime,
            )
            for old_sample in samples[: -self.config.debug_sample_limit]:
                old_sample.unlink(missing_ok=True)
        except (OSError, TypeError, ValueError):
            print("[自动投球] 警告：诊断截图保存失败")

    @staticmethod
    def _write_bmp(image: Any, path: Path) -> None:
        if image is None or not hasattr(image, "shape") or len(image.shape) < 3:
            raise ValueError("无效截图")
        height, width = image.shape[:2]
        pixels = image[:, :, :3][::-1]
        row_size = width * 3
        padding = (4 - row_size % 4) % 4
        pixel_size = (row_size + padding) * height
        header_size = 14 + 40
        with path.open("wb") as file:
            file.write(
                struct.pack(
                    "<2sIHHI",
                    b"BM",
                    header_size + pixel_size,
                    0,
                    0,
                    header_size,
                )
            )
            file.write(
                struct.pack(
                    "<IIIHHIIIIII",
                    40,
                    width,
                    height,
                    1,
                    24,
                    0,
                    pixel_size,
                    2835,
                    2835,
                    0,
                    0,
                )
            )
            pad = b"\0" * padding
            for row in pixels:
                file.write(row.tobytes())
                file.write(pad)

    @staticmethod
    def _read_virtual_key(virtual_key: int) -> bool:
        if platform.system() != "Windows":
            return False
        return bool(ctypes.windll.user32.GetAsyncKeyState(virtual_key) & 0x8000)


@AgentServer.custom_action("auto_aim_throw")
class AutoAimThrowAction(CustomAction):
    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> bool:
        try:
            params = json.loads(argv.custom_action_param or "{}")
            config_name = str(params.get("config_path", "auto_aim.json"))
            agent_dir = Path(__file__).resolve().parent
            aim_config = AimConfig.load(agent_dir / config_name)
            if not detector_model_exists():
                raise AutoFlowerError(MISSING_MODEL_MESSAGE)
            require_directml(context)
            autoflower_config = AutoFlowerConfig.load(
                agent_dir / aim_config.autoflower_config_path
            )
            runtime = AutoAimRuntime(
                context,
                AutoFlowerClient(autoflower_config),
                aim_config,
            )
            # A controlled one-shot host may still pass this flag for
            # compatibility; normal Maa runs start automatically in run().
            if bool(params.get("start_active", False)):
                print("[自动投球] 受控启动参数已忽略：任务默认启动即运行")
            return runtime.run()
        except (
            AutoFlowerError,
            json.JSONDecodeError,
            OSError,
            TypeError,
            ValueError,
        ) as exc:
            print(f"[自动投球] 启动失败：{exc}")
            return False
