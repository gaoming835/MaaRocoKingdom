from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy
from maa.pipeline import JRecognitionType


AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT_DIR))

from auto_aim_throw import (  # noqa: E402
    AimConfig,
    AutoAimRuntime,
    BallCount,
    KeyEdgeDetector,
    Target,
    annotate_target_boxes,
    ball_icon_is_active,
    ball_count_consensus,
    ball_count_decreased,
    parse_ball_count_text,
    build_scan_pattern,
    calculate_aim_move,
    choose_target,
    require_directml,
)
from autoflower_client import (  # noqa: E402
    AutoFlowerClient,
    AutoFlowerConfig,
    AutoFlowerError,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeAutoFlowerClient:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.held_mouse_buttons: set[str] = set()

    def is_game_window_foreground(self) -> bool:
        return True

    def game_client_rect(self) -> tuple[int, int, int, int]:
        return (0, 0, 1280, 720)

    def press_key(self, key: str) -> None:
        self.calls.append(("key", key))

    def move_relative(
        self,
        delta_x: int,
        delta_y: int,
        *,
        duration_ms: int | None = None,
        bionic: bool = False,
    ) -> None:
        self.calls.append(("move", delta_x, delta_y, duration_ms, bionic))

    def mouse_down(self, button: str = "LEFT") -> None:
        self.calls.append(("down", button))
        self.held_mouse_buttons.add(button)

    def mouse_up(self, button: str = "LEFT") -> None:
        if button not in self.held_mouse_buttons:
            return
        self.calls.append(("up", button))
        self.held_mouse_buttons.discard(button)

    def release_all_inputs(self) -> None:
        for button in tuple(self.held_mouse_buttons):
            self.mouse_up(button)


class FakeCommandClient(AutoFlowerClient):
    def __init__(self) -> None:
        super().__init__(
            AutoFlowerConfig(
                base_url="http://127.0.0.1:8765",
                pin="123456",
            )
        )
        self.commands: list[str] = []

    def _prepare_input(self) -> None:
        pass

    def _send_command(self, command: str) -> None:
        self.commands.append(command)


class RuntimeForTest(AutoAimRuntime):
    def __init__(
        self,
        client: FakeAutoFlowerClient,
        config: AimConfig,
        observations: list[Target | None],
        images: list[numpy.ndarray] | None = None,
    ) -> None:
        self.fake_clock = FakeClock()
        context = SimpleNamespace(
            tasker=SimpleNamespace(
                stopping=False,
                controller=None,
            ),
        )
        super().__init__(
            context,
            client,
            config,
            sleep=self.fake_clock.sleep,
            clock=self.fake_clock,
            key_reader=lambda _: False,
        )
        self.observations = iter(observations)
        self.images = iter(images or [numpy.zeros((720, 1280, 3), dtype="uint8")])
        self.saved_debug_reasons: list[str] = []
        self.saved_debug_images: list[object] = []
        self._detector_enabled = False

    def _capture(self) -> numpy.ndarray:
        try:
            return next(self.images)
        except StopIteration:
            return numpy.zeros((720, 1280, 3), dtype="uint8")

    def _observe_target(
        self,
        image: object,
        *,
        previous: Target | None = None,
    ) -> Target | None:
        del image, previous
        return next(self.observations)

    def _save_debug_sample(self, image: object, reason: str) -> None:
        self.saved_debug_images.append(image)
        self.saved_debug_reasons.append(reason)


class AutoAimMathTests(unittest.TestCase):
    def test_ball_count_parser_and_decrease(self) -> None:
        self.assertEqual(parse_ball_count_text(["球 120"]), BallCount(120))
        self.assertEqual(parse_ball_count_text(["999+"]), BallCount(999, True))
        self.assertTrue(ball_count_decreased(BallCount(120), BallCount(119)))
        self.assertFalse(ball_count_decreased(BallCount(263), BallCount(162)))
        self.assertFalse(ball_count_decreased(BallCount(999, True), BallCount(999, True)))

    def test_ball_count_consensus_rejects_single_ocr_glitch(self) -> None:
        self.assertEqual(
            ball_count_consensus(
                [BallCount(130), BallCount(60), BallCount(130), BallCount(130)]
            ),
            BallCount(130),
        )

    def test_smaller_target_raises_aim_point(self) -> None:
        near = Target((600, 300, 80, 180), 0.9)
        far = Target((600, 300, 40, 60), 0.9)
        near_y = near.aim_point(0.25, -30, 180, 0.5, 90)[1]
        far_y = far.aim_point(0.25, -30, 180, 0.5, 90)[1]
        self.assertLess(far_y, near_y)

    def test_scan_pattern_has_no_net_drift(self) -> None:
        pattern = build_scan_pattern(35)
        self.assertEqual(pattern, (35, 35, -35, -35, -35, -35, 35, 35))
        self.assertEqual(sum(pattern), 0)

    def test_selects_target_nearest_crosshair(self) -> None:
        left = Target((100, 300, 40, 80), 0.9)
        center = Target((620, 320, 40, 80), 0.7)
        selected = choose_target([left, center])
        self.assertEqual(selected, center)

    def test_target_hysteresis_keeps_tracked_candidate(self) -> None:
        previous = Target((600, 300, 60, 100), 0.9)
        tracked = Target((610, 300, 60, 100), 0.8)
        newcomer = Target((630, 320, 20, 40), 0.95)
        selected = choose_target(
            [tracked, newcomer],
            previous=previous,
            hysteresis=0.5,
        )
        self.assertEqual(selected, tracked)

    def test_target_hysteresis_rejects_distant_replacement(self) -> None:
        previous = Target((620, 300, 60, 100), 0.9)
        distant = Target((1180, 360, 70, 40), 0.5)
        self.assertIsNone(
            choose_target(
                [distant],
                previous=previous,
            )
        )

    def test_aim_move_uses_deadzone_and_clamp(self) -> None:
        config = AimConfig(aim_gain_x=0.5, aim_gain_y=0.5, aim_max_step=35)
        self.assertEqual(calculate_aim_move((650, 370), config), (0, 0, True))
        self.assertEqual(calculate_aim_move((1000, 100), config), (35, -35, False))

    def test_ball_active_color_heuristic(self) -> None:
        config = AimConfig(
            ball_roi=(0, 0, 20, 20),
            ball_active_min_pixels=10,
        )
        inactive = numpy.zeros((20, 20, 3), dtype="uint8")
        active = inactive.copy()
        active[:5, :5] = (180, 40, 200)
        self.assertFalse(ball_icon_is_active(inactive, config))
        self.assertTrue(ball_icon_is_active(active, config))

    def test_config_rejects_invalid_hid_duration_and_center(self) -> None:
        with self.assertRaisesRegex(Exception, "aim_move_duration_ms"):
            AimConfig(aim_move_duration_ms=9).validate()
        with self.assertRaisesRegex(Exception, "瞄准中心"):
            AimConfig(aim_center_x=-1).validate()
        with self.assertRaisesRegex(Exception, "detector_min_height_pixels"):
            AimConfig(detector_min_height_pixels=-1).validate()
        with self.assertRaisesRegex(Exception, "detector_min_height_pixels"):
            AimConfig(detector_min_height_pixels=721).validate()
        with self.assertRaisesRegex(
            Exception,
            "max_throw_attempts_per_activation",
        ):
            AimConfig(max_throw_attempts_per_activation=-1).validate()
        with self.assertRaisesRegex(Exception, "activation_timeout_seconds"):
            AimConfig(activation_timeout_seconds=-0.1).validate()
        with self.assertRaisesRegex(
            Exception,
            "aim_target_miss_tolerance_frames",
        ):
            AimConfig(aim_target_miss_tolerance_frames=11).validate()
        with self.assertRaisesRegex(Exception, "aim_vertical_offset_pixels"):
            AimConfig(aim_vertical_offset_pixels=-721).validate()
        with self.assertRaisesRegex(Exception, "target_lock_confirm_frames"):
            AimConfig(target_lock_confirm_frames=0).validate()
        AimConfig(detector_min_height_pixels=0).validate()
        AimConfig(
            max_throw_attempts_per_activation=0,
            activation_timeout_seconds=0,
        ).validate()

    def test_config_loads_near_target_height_boundary(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "auto_aim.json"
            path.write_text(
                '{"detector_threshold": 0.4, '
                '"detector_min_height_pixels": 0, '
                '"max_throw_attempts_per_activation": 0, '
                '"activation_timeout_seconds": 60, '
                '"foreground_grace_seconds": 60, '
                '"aim_anchor_y": 0.25, '
                '"aim_vertical_offset_pixels": -30, '
                '"aim_target_miss_tolerance_frames": 2, '
                '"target_lock_confirm_frames": 2, '
                '"debug_samples": true}',
                encoding="utf-8",
            )
            config = AimConfig.load(path)

        self.assertEqual(config.detector_threshold, 0.4)
        self.assertEqual(config.detector_min_height_pixels, 0)
        self.assertEqual(config.max_throw_attempts_per_activation, 0)
        self.assertEqual(config.activation_timeout_seconds, 60)
        self.assertEqual(config.foreground_grace_seconds, 60)
        self.assertEqual(config.aim_anchor_y, 0.25)
        self.assertEqual(config.aim_vertical_offset_pixels, -30)
        self.assertEqual(config.aim_target_miss_tolerance_frames, 2)
        self.assertEqual(config.target_lock_confirm_frames, 2)


class HotkeyTests(unittest.TestCase):
    def test_escape_detector_reports_only_debounced_rising_edges(self) -> None:
        clock = FakeClock()
        state = {0x1B: False}
        detector = KeyEdgeDetector(
            lambda key: state[key],
            clock,
            debounce_seconds=0.2,
        )

        self.assertFalse(detector.rising(0x1B))
        state[0x1B] = True
        self.assertTrue(detector.rising(0x1B))
        self.assertFalse(detector.rising(0x1B))
        state[0x1B] = False
        self.assertFalse(detector.rising(0x1B))
        clock.sleep(0.1)
        state[0x1B] = True
        self.assertFalse(detector.rising(0x1B))
        state[0x1B] = False
        detector.rising(0x1B)
        clock.sleep(0.2)
        state[0x1B] = True
        self.assertTrue(detector.rising(0x1B))


class AutoFlowerInputTests(unittest.TestCase):
    def test_tracks_mouse_hold_and_release(self) -> None:
        client = FakeCommandClient()
        client.mouse_down("left")
        client.mouse_down("LEFT")
        client.move_relative(12, -8, duration_ms=80)
        client.mouse_up("left")

        self.assertEqual(
            client.commands,
            [
                "{MOUSE_DOWN LEFT}",
                "{MOVE 12 -8 80}",
                "{MOUSE_UP LEFT}",
            ],
        )
        self.assertEqual(client.held_mouse_buttons, frozenset())

    def test_release_all_inputs_pairs_mouse_down(self) -> None:
        client = FakeCommandClient()
        client.mouse_down("LEFT")
        client.release_all_inputs()
        self.assertEqual(
            client.commands,
            ["{MOUSE_DOWN LEFT}", "{MOUSE_UP LEFT}"],
        )

    def test_focus_fast_path_skips_set_foreground_delay(self) -> None:
        with (
            patch("autoflower_client.ctypes.windll.user32") as user32,
            patch("autoflower_client.time.sleep") as sleep,
        ):
            user32.GetForegroundWindow.return_value = 123
            AutoFlowerClient._focus_window(123)
            user32.SetForegroundWindow.assert_not_called()
            sleep.assert_not_called()


class AutoAimRuntimeTests(unittest.TestCase):
    def test_directml_is_required_without_cpu_fallback(self) -> None:
        resource = Mock()
        resource.use_directml.return_value = True
        context = SimpleNamespace(
            tasker=SimpleNamespace(resource=resource),
        )

        require_directml(context)

        resource.use_directml.assert_called_once_with()

    def test_directml_failure_rejects_task(self) -> None:
        resource = Mock()
        resource.use_directml.return_value = False
        context = SimpleNamespace(
            tasker=SimpleNamespace(resource=resource),
        )

        with self.assertRaisesRegex(AutoFlowerError, "DirectML GPU"):
            require_directml(context)

    def test_remote_agent_accepts_host_preconfigured_directml(self) -> None:
        resource = Mock()
        resource.use_directml.return_value = False
        context = SimpleNamespace(
            tasker=SimpleNamespace(resource=resource),
        )

        with patch.dict(os.environ, {"MAA_AGENT_SERVER_PROCESS": "1"}):
            require_directml(context)

        resource.use_directml.assert_not_called()

    def test_missing_model_fails_before_any_autoflower_call(self) -> None:
        context = SimpleNamespace(tasker=SimpleNamespace(stopping=False))
        client = Mock(spec=FakeAutoFlowerClient)
        runtime = AutoAimRuntime(
            context,
            client,
            AimConfig(),
            key_reader=lambda _: False,
        )
        runtime._detector_enabled = False

        with self.assertRaisesRegex(AutoFlowerError, "未找到精灵检测模型"):
            runtime.run()

        self.assertEqual(client.mock_calls, [])

    def test_function_key_is_not_a_control_key_and_escape_stops(self) -> None:
        state = {0x77: True, 0x1B: False}
        runtime = AutoAimRuntime(
            SimpleNamespace(tasker=SimpleNamespace(stopping=False)),
            FakeAutoFlowerClient(),
            AimConfig(),
            key_reader=lambda key: state.get(key, False),
        )

        self.assertIsNone(runtime._poll_control_event())
        state[0x1B] = True
        self.assertEqual(runtime._poll_control_event(), "stop")

    def test_run_starts_activation_immediately(self) -> None:
        runtime = AutoAimRuntime(
            SimpleNamespace(tasker=SimpleNamespace(stopping=False)),
            FakeAutoFlowerClient(),
            AimConfig(),
            key_reader=lambda _: False,
        )
        runtime._detector_enabled = True
        runtime._poll_control_event = Mock(return_value="stop")  # type: ignore[method-assign]

        with patch("auto_aim_throw.platform.system", return_value="Windows"):
            self.assertTrue(runtime.run())

        self.assertTrue(runtime.active)
        self.assertEqual(runtime._throw_attempts, 0)

    def test_target_annotation_marks_selected_and_other_boxes(self) -> None:
        image = numpy.zeros((72, 128, 3), dtype="uint8")
        selected = Target((100, 100, 200, 100), 0.95)
        other = Target((500, 200, 100, 100), 0.80)

        annotated = annotate_target_boxes(image, [selected, other], selected)

        self.assertEqual(tuple(annotated[10, 15, :3]), (0, 255, 0))
        self.assertEqual(tuple(annotated[20, 55, :3]), (0, 220, 255))
        self.assertEqual(int(image.sum()), 0)

    def test_target_annotation_clips_invalid_boxes_without_raising(self) -> None:
        image = numpy.zeros((72, 128, 3), dtype="uint8")
        clipped = Target((-50, -20, 100, 60), 0.9)
        invalid = Target((100, 100, 0, 20), 0.8)

        annotated = annotate_target_boxes(image, [clipped, invalid], clipped)

        self.assertEqual(annotated.shape, image.shape)
        self.assertGreater(int(annotated.sum()), 0)

    def test_target_selection_sample_uses_latest_detection_list(self) -> None:
        runtime = RuntimeForTest(FakeAutoFlowerClient(), AimConfig(), [])
        selected = Target((100, 100, 200, 100), 0.95)
        other = Target((500, 200, 100, 100), 0.80)
        image = numpy.zeros((72, 128, 3), dtype="uint8")

        runtime._save_target_selection_sample(
            image,
            [selected, other],
            selected,
        )

        self.assertEqual(runtime.saved_debug_reasons, ["target_selected"])
        saved = runtime.saved_debug_images[0]
        self.assertEqual(tuple(saved[10, 15, :3]), (0, 255, 0))
        self.assertEqual(tuple(saved[20, 55, :3]), (0, 220, 255))

    def test_target_selection_sample_disabled_does_not_save(self) -> None:
        runtime = RuntimeForTest(
            FakeAutoFlowerClient(),
            AimConfig(debug_samples=False),
            [],
        )
        target = Target((100, 100, 200, 100), 0.95)

        runtime._save_target_selection_sample(
            numpy.zeros((72, 128, 3), dtype="uint8"),
            [target],
            target,
        )

        self.assertEqual(runtime.saved_debug_reasons, [])

    def test_target_selection_save_failure_does_not_raise(self) -> None:
        runtime = RuntimeForTest(FakeAutoFlowerClient(), AimConfig(), [])
        runtime._save_debug_sample = Mock(side_effect=OSError("read-only"))  # type: ignore[method-assign]
        target = Target((100, 100, 200, 100), 0.95)

        runtime._save_target_selection_sample(
            numpy.zeros((72, 128, 3), dtype="uint8"),
            [target],
            target,
        )

    def test_throw_command_order(self) -> None:
        config = AimConfig(
            aim_stable_frames=2,
            aim_settle_seconds=0.01,
            throw_cooldown_seconds=0.01,
            hotkey_poll_seconds=0.005,
        )
        client = FakeAutoFlowerClient()
        centered = Target((622, 336, 36, 48), 1.0)
        runtime = RuntimeForTest(client, config, [centered, centered])

        self.assertTrue(runtime._throw_at(centered))
        self.assertEqual(client.calls, [("down", "LEFT"), ("up", "LEFT")])
        self.assertEqual(client.held_mouse_buttons, set())
        self.assertEqual(
            runtime.saved_debug_reasons,
            ["aim_ready", "throw_completed"],
        )

    def test_restart_activation_resets_attempt_count_and_timer(self) -> None:
        runtime = RuntimeForTest(
            FakeAutoFlowerClient(),
            AimConfig(),
            [],
        )

        runtime._start_activation()
        runtime._throw_attempts = 1
        runtime.fake_clock.sleep(10)
        runtime._pause_activation()
        runtime.fake_clock.sleep(5)
        runtime._start_activation()

        self.assertTrue(runtime.active)
        self.assertEqual(runtime._throw_attempts, 0)
        self.assertEqual(runtime._activation_started_at, 15)

    def test_task_run_has_no_throw_count_limit(self) -> None:
        config = AimConfig(
            max_throw_attempts_per_activation=0,
            aim_stable_frames=2,
            aim_settle_seconds=0.01,
            throw_cooldown_seconds=0.01,
            hotkey_poll_seconds=0.005,
            debug_samples=False,
        )
        client = FakeAutoFlowerClient()
        centered = Target((622, 311, 36, 108), 1.0)
        runtime = RuntimeForTest(
            client,
            config,
            [centered, centered, centered, centered],
        )
        runtime._start_activation()

        self.assertTrue(runtime._attempt_throw(centered))
        self.assertTrue(runtime.active)
        self.assertTrue(runtime._attempt_throw(centered))
        self.assertEqual(
            [call for call in client.calls if call[0] == "down"],
            [("down", "LEFT"), ("down", "LEFT")],
        )

    def test_background_window_grace_expires_after_one_minute(self) -> None:
        runtime = RuntimeForTest(
            FakeAutoFlowerClient(),
            AimConfig(foreground_grace_seconds=60),
            [],
        )
        runtime._task_started_at = 0
        runtime.client.is_game_window_foreground = lambda: False  # type: ignore[method-assign]

        runtime.fake_clock.now = 59.9
        self.assertFalse(runtime._background_grace_expired())
        runtime.fake_clock.now = 60
        self.assertTrue(runtime._background_grace_expired())

    def test_target_loss_after_attempt_returns_to_search(self) -> None:
        config = AimConfig(
            max_throw_attempts_per_activation=1,
            aim_settle_seconds=0.01,
            hotkey_poll_seconds=0.005,
            aim_target_miss_tolerance_frames=0,
        )
        client = FakeAutoFlowerClient()
        target = Target((622, 306, 80, 108), 1.0)
        runtime = RuntimeForTest(client, config, [None])
        runtime._start_activation()

        self.assertFalse(runtime._attempt_throw(target))
        self.assertTrue(runtime.active)
        self.assertEqual(client.calls, [("down", "LEFT"), ("up", "LEFT")])
        self.assertEqual(client.held_mouse_buttons, set())

    def test_timeout_finishes_current_scan_cycle_before_pausing(self) -> None:
        config = AimConfig(
            activation_timeout_seconds=60,
            scan_settle_seconds=0.01,
        )
        client = FakeAutoFlowerClient()
        runtime = RuntimeForTest(client, config, [])
        runtime._start_activation()
        for _ in range(3):
            runtime._scan_once()
        runtime._no_target_started_at = 0
        runtime.fake_clock.now = 60

        self.assertTrue(runtime._pause_if_activation_timed_out())
        moves = [int(call[1]) for call in client.calls if call[0] == "move"]
        self.assertEqual(len(moves), 8)
        self.assertEqual(sum(moves), 0)
        self.assertEqual(runtime.scan_index, 0)
        self.assertFalse(runtime.active)

    def test_zero_disables_attempt_limit_and_activation_timeout(self) -> None:
        config = AimConfig(
            max_throw_attempts_per_activation=0,
            activation_timeout_seconds=0,
            aim_stable_frames=2,
            aim_settle_seconds=0.01,
            throw_cooldown_seconds=0.01,
            hotkey_poll_seconds=0.005,
            debug_samples=False,
        )
        client = FakeAutoFlowerClient()
        centered = Target((600, 311, 80, 108), 1.0)
        runtime = RuntimeForTest(
            client,
            config,
            [centered, centered, centered, centered],
        )
        runtime._start_activation()
        runtime.fake_clock.now = 600

        self.assertFalse(runtime._pause_if_activation_timed_out())
        self.assertTrue(runtime._attempt_throw(centered))
        self.assertTrue(runtime._attempt_throw(centered))
        self.assertTrue(runtime.active)
        self.assertEqual(
            len([call for call in client.calls if call[0] == "down"]),
            2,
        )

    def test_ball_verification_failure_does_not_consume_attempt(self) -> None:
        config = AimConfig(
            ball_roi=(0, 0, 20, 20),
            ball_active_min_pixels=10,
            ball_select_settle_seconds=0.01,
            hotkey_poll_seconds=0.005,
        )
        inactive = numpy.zeros((20, 20, 3), dtype="uint8")
        runtime = RuntimeForTest(
            FakeAutoFlowerClient(),
            config,
            [],
            images=[inactive],
        )
        runtime._start_activation()

        with self.assertRaisesRegex(AutoFlowerError, "按 E 后仍未识别到"):
            runtime._ensure_ball_selected(inactive)
        self.assertEqual(runtime._throw_attempts, 0)

    def test_target_loss_still_releases_mouse(self) -> None:
        config = AimConfig(
            aim_settle_seconds=0.01,
            hotkey_poll_seconds=0.005,
            aim_target_miss_tolerance_frames=0,
        )
        client = FakeAutoFlowerClient()
        target = Target((622, 336, 36, 48), 1.0)
        runtime = RuntimeForTest(client, config, [None])

        self.assertFalse(runtime._throw_at(target))
        self.assertEqual(client.calls, [("down", "LEFT"), ("up", "LEFT")])
        self.assertEqual(client.held_mouse_buttons, set())

    def test_tolerates_transient_detection_miss_during_aim(self) -> None:
        config = AimConfig(
            aim_anchor_y=0.25,
            aim_target_miss_tolerance_frames=1,
            aim_stable_frames=2,
            aim_settle_seconds=0.01,
            throw_cooldown_seconds=0.01,
            hotkey_poll_seconds=0.005,
            debug_samples=False,
        )
        client = FakeAutoFlowerClient()
        centered = Target((622, 348, 36, 48), 1.0)
        runtime = RuntimeForTest(client, config, [None, centered, centered])

        self.assertTrue(runtime._throw_at(centered))
        self.assertEqual(client.calls, [("down", "LEFT"), ("up", "LEFT")])
        self.assertEqual(client.held_mouse_buttons, set())

    def test_selects_ball_only_when_inactive(self) -> None:
        config = AimConfig(
            ball_roi=(0, 0, 20, 20),
            ball_active_min_pixels=10,
            ball_select_settle_seconds=0.01,
            hotkey_poll_seconds=0.005,
        )
        inactive = numpy.zeros((20, 20, 3), dtype="uint8")
        active = inactive.copy()
        active[:5, :5] = (180, 40, 200)
        client = FakeAutoFlowerClient()
        runtime = RuntimeForTest(client, config, [], images=[active])

        runtime._ensure_ball_selected(inactive)
        runtime._ensure_ball_selected(active)
        self.assertEqual(client.calls, [("key", "E")])

    def test_camera_move_clears_velocity_history(self) -> None:
        config = AimConfig(
            aim_stable_frames=2,
            aim_settle_seconds=0.01,
            throw_cooldown_seconds=0.01,
            hotkey_poll_seconds=0.005,
        )
        client = FakeAutoFlowerClient()
        off_center = Target((800, 336, 36, 48), 0.9)
        centered = Target((622, 336, 36, 48), 0.9)
        runtime = RuntimeForTest(
            client,
            config,
            [off_center, centered, centered],
        )
        history_lengths: list[int] = []
        original_estimate_lead = runtime._estimate_lead

        def recording_estimate_lead(
            history: list[tuple[float, tuple[float, float]]],
        ) -> tuple[float, float]:
            history_lengths.append(len(history))
            return original_estimate_lead(history)

        runtime._estimate_lead = recording_estimate_lead  # type: ignore[method-assign]

        self.assertTrue(runtime._throw_at(off_center))
        self.assertEqual(history_lengths, [1, 1, 2])
        self.assertEqual(
            [call[0] for call in client.calls],
            ["down", "move", "up"],
        )

    def test_only_distant_yolo_boxes_scan_without_throw(self) -> None:
        run_recognition_direct = Mock(
            return_value=SimpleNamespace(
                all_results=[
                    SimpleNamespace(box=(634, 350, 40, 107), score=0.8),
                ]
            )
        )
        context = SimpleNamespace(
            tasker=SimpleNamespace(stopping=False),
            run_recognition_direct=run_recognition_direct,
        )
        client = FakeAutoFlowerClient()
        clock = FakeClock()
        runtime = AutoAimRuntime(
            context,
            client,
            AimConfig(
                detector_min_height_pixels=108,
                scan_settle_seconds=0.01,
            ),
            sleep=clock.sleep,
            clock=clock,
            key_reader=lambda _: False,
        )

        image = numpy.zeros((720, 1280, 3), dtype="uint8")
        self.assertIsNone(runtime._observe_target(image))
        runtime._scan_once()
        self.assertEqual(
            client.calls,
            [("move", 35, 0, 80, False)],
        )

    def test_detector_accepts_108px_and_ignores_107px_box(self) -> None:
        run_recognition_direct = Mock(
            return_value=SimpleNamespace(
                all_results=[
                    SimpleNamespace(box=(620, 300, 60, 107), score=0.99),
                    SimpleNamespace(box=(100, 250, 100, 108), score=0.8),
                ]
            )
        )
        context = SimpleNamespace(
            tasker=SimpleNamespace(stopping=False),
            run_recognition_direct=run_recognition_direct,
        )
        runtime = AutoAimRuntime(
            context,
            FakeAutoFlowerClient(),
            AimConfig(detector_min_height_pixels=108),
            key_reader=lambda _: False,
        )

        targets = runtime._run_detector(
            numpy.zeros((720, 1280, 3), dtype="uint8")
        )

        self.assertEqual(targets, [Target((100, 250, 100, 108), 0.8)])
        self.assertEqual(
            runtime._observe_target(
                numpy.zeros((720, 1280, 3), dtype="uint8")
            ),
            targets[0],
        )
        self.assertEqual(runtime._last_detected_targets, targets)

    def test_detector_height_filter_disabled_accepts_small_box(self) -> None:
        run_recognition_direct = Mock(
            return_value=SimpleNamespace(
                all_results=[
                    SimpleNamespace(box=(620, 300, 40, 50), score=0.8),
                ]
            )
        )
        context = SimpleNamespace(
            tasker=SimpleNamespace(stopping=False),
            run_recognition_direct=run_recognition_direct,
        )
        runtime = AutoAimRuntime(
            context,
            FakeAutoFlowerClient(),
            AimConfig(detector_min_height_pixels=0),
            key_reader=lambda _: False,
        )

        targets = runtime._run_detector(
            numpy.zeros((720, 1280, 3), dtype="uint8")
        )

        self.assertEqual(targets, [Target((620, 300, 40, 50), 0.8)])

    def test_target_becoming_distant_releases_mouse_and_pauses_throw(self) -> None:
        run_recognition_direct = Mock(
            return_value=SimpleNamespace(
                all_results=[
                    SimpleNamespace(box=(620, 300, 80, 107), score=0.9),
                ]
            )
        )
        context = SimpleNamespace(
            tasker=SimpleNamespace(stopping=False),
            run_recognition_direct=run_recognition_direct,
        )
        client = FakeAutoFlowerClient()
        clock = FakeClock()
        runtime = AutoAimRuntime(
            context,
            client,
            AimConfig(
                detector_min_height_pixels=108,
                aim_settle_seconds=0.01,
                hotkey_poll_seconds=0.005,
                aim_target_miss_tolerance_frames=0,
            ),
            sleep=clock.sleep,
            clock=clock,
            key_reader=lambda _: False,
        )
        runtime._capture = lambda: numpy.zeros(  # type: ignore[method-assign]
            (720, 1280, 3),
            dtype="uint8",
        )

        self.assertFalse(runtime._throw_at(Target((620, 300, 80, 108), 0.9)))
        self.assertEqual(client.calls, [("down", "LEFT"), ("up", "LEFT")])
        self.assertEqual(client.held_mouse_buttons, set())

    def test_no_detection_returns_none_and_scan_sends_only_move(self) -> None:
        run_recognition_direct = Mock(
            return_value=SimpleNamespace(all_results=[])
        )
        context = SimpleNamespace(
            tasker=SimpleNamespace(stopping=False),
            run_recognition_direct=run_recognition_direct,
        )
        client = FakeAutoFlowerClient()
        clock = FakeClock()
        runtime = AutoAimRuntime(
            context,
            client,
            AimConfig(scan_settle_seconds=0.01),
            sleep=clock.sleep,
            clock=clock,
            key_reader=lambda _: False,
        )
        runtime._detector_enabled = True

        image = numpy.zeros((720, 1280, 3), dtype="uint8")
        self.assertIsNone(runtime._observe_target(image))
        runtime._scan_once()

        self.assertEqual(
            client.calls,
            [("move", 35, 0, 80, False)],
        )

    def test_yolo_uses_direct_recognition_only_when_enabled(self) -> None:
        result = SimpleNamespace(
            box=(600, 300, 80, 120),
            score=0.8,
        )
        run_recognition_direct = Mock(
            return_value=SimpleNamespace(all_results=[result])
        )
        context = SimpleNamespace(
            tasker=SimpleNamespace(stopping=False),
            run_recognition_direct=run_recognition_direct,
        )
        client = FakeAutoFlowerClient()
        runtime = AutoAimRuntime(
            context,
            client,
            AimConfig(detector_threshold=0.55),
            key_reader=lambda _: False,
        )

        targets = runtime._run_detector(
            numpy.zeros((720, 1280, 3), dtype="uint8")
        )

        self.assertEqual(len(targets), 1)
        recognition_type, params, _ = run_recognition_direct.call_args.args
        self.assertEqual(recognition_type, JRecognitionType.NeuralNetworkDetect)
        self.assertEqual(params.model, "sprite.onnx")
        self.assertEqual(params.threshold, [0.55])


if __name__ == "__main__":
    unittest.main()
