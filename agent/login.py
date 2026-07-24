from __future__ import annotations

import json
from pathlib import Path

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

from autoflower_client import AutoFlowerClient, AutoFlowerConfig, AutoFlowerError


@AgentServer.custom_action("autoflower_click")
class AutoFlowerClickAction(CustomAction):
    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> bool:
        del context

        try:
            params = json.loads(argv.custom_action_param or "{}")
            config_name = params.get("config_path", "autoflower.local.json")
            config_path = Path(__file__).resolve().parent / config_name
            config = AutoFlowerConfig.load(config_path)
            AutoFlowerClient(config).click_box(tuple(argv.box))
            return True
        except (AutoFlowerError, json.JSONDecodeError, TypeError, ValueError) as exc:
            # 不输出请求参数，避免 PIN 或令牌进入日志。
            print(f"[Login] AutoFlower 点击失败：{exc}")
            return False
