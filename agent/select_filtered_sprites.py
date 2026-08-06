from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

from autoflower_client import AutoFlowerClient, AutoFlowerConfig, AutoFlowerError


PAGE_PATTERN = re.compile(r"(\d+)\s*/\s*(\d+)")
SELECTED_PATTERN = re.compile(r"已选\s*(\d+)")
MAX_PAGE_COUNT = 100


class SpriteClicker(Protocol):
    def click_box(self, box: tuple[int, int, int, int]) -> None: ...

    def click_boxes(self, boxes: list[tuple[int, int, int, int]]) -> None: ...


@dataclass(frozen=True)
class SelectionLayout:
    """1280×720 识别画面中的精灵网格与翻页控件。"""

    first_slot_x: int = 244
    first_slot_y: int = 164
    column_step: int = 85
    row_step: int = 84
    columns: int = 6
    rows: int = 5
    selection_mode_box: tuple[int, int, int, int] = (258, 586, 40, 40)
    left_page_box: tuple[int, int, int, int] = (338, 586, 40, 40)
    right_page_box: tuple[int, int, int, int] = (538, 586, 40, 40)

    def slot_boxes(self) -> list[tuple[int, int, int, int]]:
        boxes: list[tuple[int, int, int, int]] = []
        for row in range(self.rows):
            columns = (
                range(self.columns)
                if row % 2 == 0
                else range(self.columns - 1, -1, -1)
            )
            boxes.extend(
                (
                    self.first_slot_x + column * self.column_step,
                    self.first_slot_y + row * self.row_step,
                    1,
                    1,
                )
                for column in columns
            )
        return boxes


def parse_page_indicator(texts: list[str]) -> tuple[int, int] | None:
    for text in texts:
        match = PAGE_PATTERN.search(text)
        if not match:
            continue

        current, total = (int(value) for value in match.groups())
        if 1 <= current <= total <= MAX_PAGE_COUNT:
            return current, total
    return None


def parse_selected_indicator(texts: list[str]) -> int | None:
    for text in texts:
        match = SELECTED_PATTERN.search(text)
        if match:
            return int(match.group(1))
        # 新版 UI 的空选择数字 0 较暗，OCR 实测会读成“已选·”。
        if "已选" in text:
            return 0
    return None


def prepare_selection_mode(
    clicker: SpriteClicker,
    read_selected: Callable[[], int | None],
    *,
    layout: SelectionLayout = SelectionLayout(),
    settle: Callable[[], None] = lambda: None,
) -> None:
    """清空已有勾选并进入干净的放生多选状态。"""
    if read_selected() is not None:
        clicker.click_box(layout.selection_mode_box)
        settle()
        if read_selected() is not None:
            raise AutoFlowerError("无法退出已有的多选状态")

    clicker.click_box(layout.selection_mode_box)
    settle()
    selected = read_selected()
    if selected != 0:
        raise AutoFlowerError(
            f"无法进入空的多选状态：已选数量为 {selected!r}"
        )


def select_filtered_pages(
    clicker: SpriteClicker,
    read_page: Callable[[], tuple[int, int] | None],
    *,
    layout: SelectionLayout = SelectionLayout(),
    settle: Callable[[], None] = lambda: None,
) -> int:
    """从任意当前页回到第一页，选择每页网格，再逐页前进至末页。"""
    page = read_page()
    if page is None:
        raise AutoFlowerError("未识别到“筛选中 当前页/总页数”，请先打开筛选结果页")

    current, total = page

    for expected_page in range(current - 1, 0, -1):
        clicker.click_box(layout.left_page_box)
        settle()
        actual = read_page()
        if actual != (expected_page, total):
            raise AutoFlowerError(
                f"返回第一页失败：期望 {expected_page}/{total}，"
                f"实际 {actual or '未识别'}"
            )

    slots = layout.slot_boxes()
    for current_page in range(1, total + 1):
        clicker.click_boxes(slots)
        if current_page == total:
            break

        clicker.click_box(layout.right_page_box)
        settle()
        actual = read_page()
        expected = (current_page + 1, total)
        if actual != expected:
            raise AutoFlowerError(
                f"自动翻页失败：期望 {expected[0]}/{total}，"
                f"实际 {actual or '未识别'}"
            )

    return total


@AgentServer.custom_action("select_filtered_sprites")
class SelectFilteredSpritesAction(CustomAction):
    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> bool:
        try:
            params = json.loads(argv.custom_action_param or "{}")
            config_name = params.get("config_path", "autoflower.local.json")
            settle_seconds = float(params.get("page_settle_seconds", 0.5))
            if settle_seconds < 0:
                raise ValueError("page_settle_seconds 不能为负数")

            config_path = Path(__file__).resolve().parent / config_name
            config = AutoFlowerConfig.load(config_path)
            client = AutoFlowerClient(config)
            settle = lambda: time.sleep(settle_seconds)

            prepare_selection_mode(
                client,
                lambda: self._read_selected(context),
                settle=settle,
            )
            total = select_filtered_pages(
                client,
                lambda: self._read_page(context),
                settle=settle,
            )
            print(f"[一键选择] 已处理当前筛选的全部 {total} 页")
            return True
        except (
            AutoFlowerError,
            json.JSONDecodeError,
            OSError,
            TypeError,
            ValueError,
        ) as exc:
            print(f"[一键选择] 执行失败：{exc}")
            return False

    @staticmethod
    def _read_page(context: Context) -> tuple[int, int] | None:
        return parse_page_indicator(
            SelectFilteredSpritesAction._read_texts(
                context,
                "SelectFilteredSprites.PageIndicator",
            )
        )

    @staticmethod
    def _read_selected(context: Context) -> int | None:
        return parse_selected_indicator(
            SelectFilteredSpritesAction._read_texts(
                context,
                "SelectFilteredSprites.SelectedIndicator",
            )
        )

    @staticmethod
    def _read_texts(context: Context, node_name: str) -> list[str]:
        screenshot = context.tasker.controller.post_screencap().wait()
        if not screenshot.succeeded:
            raise AutoFlowerError("读取游戏画面失败")

        detail = context.run_recognition(
            node_name,
            screenshot.get(),
        )
        if detail is None:
            raise AutoFlowerError(f"OCR 未能启动：{node_name}")

        return [
            result.text
            for result in detail.all_results
            if hasattr(result, "text")
        ]
