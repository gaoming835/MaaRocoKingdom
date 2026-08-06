from __future__ import annotations

import sys
import unittest
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT_DIR))

from autoflower_client import (  # noqa: E402
    AutoFlowerClient,
    AutoFlowerConfig,
    AutoFlowerError,
)
from select_filtered_sprites import (  # noqa: E402
    SelectionLayout,
    parse_page_indicator,
    parse_selected_indicator,
    prepare_selection_mode,
    select_filtered_pages,
)


class FakeClicker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def click_box(self, box: tuple[int, int, int, int]) -> None:
        self.calls.append(("one", box))

    def click_boxes(self, boxes: list[tuple[int, int, int, int]]) -> None:
        self.calls.append(("many", boxes))


class SelectFilteredSpritesTests(unittest.TestCase):
    def test_parse_page_indicator(self) -> None:
        self.assertEqual(
            parse_page_indicator(["其他文字", "筛选中 1 / 6"]),
            (1, 6),
        )
        self.assertIsNone(parse_page_indicator(["筛选中 0/6", "无页码"]))
        self.assertIsNone(parse_page_indicator(["筛选中 9/6"]))

    def test_parse_selected_indicator(self) -> None:
        self.assertEqual(parse_selected_indicator(["已选 12"]), 12)
        self.assertEqual(parse_selected_indicator(["已选·"]), 0)
        self.assertIsNone(parse_selected_indicator(["暂无精灵信息"]))

    def test_layout_contains_six_by_five_grid(self) -> None:
        boxes = SelectionLayout().slot_boxes()
        self.assertEqual(len(boxes), 30)
        self.assertEqual(boxes[0], (244, 164, 1, 1))
        self.assertEqual(boxes[5], (669, 164, 1, 1))
        self.assertEqual(boxes[6], (669, 248, 1, 1))
        self.assertEqual(boxes[11], (244, 248, 1, 1))
        self.assertEqual(boxes[-1], (669, 500, 1, 1))

    def test_selects_all_pages_from_middle_page(self) -> None:
        clicker = FakeClicker()
        readings = iter(
            [
                (3, 4),
                (2, 4),
                (1, 4),
                (2, 4),
                (3, 4),
                (4, 4),
            ]
        )

        total = select_filtered_pages(clicker, lambda: next(readings))

        self.assertEqual(total, 4)
        single_clicks = [call for call in clicker.calls if call[0] == "one"]
        page_clicks = [call for call in clicker.calls if call[0] == "many"]
        self.assertEqual(len(single_clicks), 5)
        self.assertEqual(len(page_clicks), 4)
        self.assertTrue(all(len(call[1]) == 30 for call in page_clicks))

    def test_prepare_selection_mode_clears_existing_selection(self) -> None:
        clicker = FakeClicker()
        readings = iter([3, None, 0])

        prepare_selection_mode(clicker, lambda: next(readings))

        self.assertEqual(
            clicker.calls,
            [
                ("one", SelectionLayout().selection_mode_box),
                ("one", SelectionLayout().selection_mode_box),
            ],
        )

    def test_stops_when_page_does_not_advance(self) -> None:
        clicker = FakeClicker()
        readings = iter([(1, 2), (1, 2)])

        with self.assertRaisesRegex(AutoFlowerError, "自动翻页失败"):
            select_filtered_pages(clicker, lambda: next(readings))

    def test_builds_one_serpentine_page_script(self) -> None:
        config = AutoFlowerConfig(
            base_url="http://127.0.0.1:8765",
            pin="123456",
            script_move_step=10,
            script_move_duration_ms=10,
        )
        client = AutoFlowerClient(config)
        boxes = SelectionLayout().slot_boxes()
        targets = [(box[0], box[1]) for box in boxes]

        commands = client._build_click_script(targets).splitlines()

        self.assertEqual(commands.count("{CLICK LEFT}"), 30)
        self.assertEqual(len(commands), 291)
        self.assertEqual(commands[1], "{MOVE 10 0 10}")
        self.assertEqual(commands[9], "{MOVE 5 0 10}")
        self.assertEqual(commands[11], "{MOVE 10 0 10}")
        self.assertNotIn("{MOVE -10 10 10}", commands)


if __name__ == "__main__":
    unittest.main()
