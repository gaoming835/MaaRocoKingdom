from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import cv2
import numpy
from maa.controller import CustomController
from maa.pipeline import JNeuralNetworkDetect, JRecognitionType
from maa.resource import Resource
from maa.tasker import Tasker


class ImageOnlyController(CustomController):
    """只提供固定截图的控制器；所有输入回调均拒绝执行。"""

    def __init__(self, image: numpy.ndarray) -> None:
        self.image = image
        super().__init__()

    def connect(self) -> bool:
        return True

    def request_uuid(self) -> str:
        return "directml-detector-verification"

    def start_app(self, intent: str) -> bool:
        del intent
        return False

    def stop_app(self, intent: str) -> bool:
        del intent
        return False

    def screencap(self) -> numpy.ndarray:
        return self.image

    def click(self, x: int, y: int) -> bool:
        del x, y
        return False

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration: int,
    ) -> bool:
        del x1, y1, x2, y2, duration
        return False

    def touch_down(
        self,
        contact: int,
        x: int,
        y: int,
        pressure: int,
    ) -> bool:
        del contact, x, y, pressure
        return False

    def touch_move(
        self,
        contact: int,
        x: int,
        y: int,
        pressure: int,
    ) -> bool:
        del contact, x, y, pressure
        return False

    def touch_up(self, contact: int) -> bool:
        del contact
        return False

    def click_key(self, keycode: int) -> bool:
        del keycode
        return False

    def input_text(self, text: str) -> bool:
        del text
        return False

    def key_down(self, keycode: int) -> bool:
        del keycode
        return False

    def key_up(self, keycode: int) -> bool:
        del keycode
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="使用 MaaFramework DirectML 对单张图片执行 sprite.onnx 推理",
    )
    parser.add_argument("image", type=Path)
    parser.add_argument(
        "--resource",
        type=Path,
        default=Path("assets/resource"),
    )
    parser.add_argument("--threshold", type=float, default=0.4)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if args.runs <= 0:
        raise SystemExit("--runs 必须大于 0")

    image = cv2.imread(str(args.image))
    if image is None:
        raise SystemExit(f"无法读取测试图片：{args.image}")

    resource = Resource()
    bundle_job = resource.post_bundle(args.resource).wait()
    if not bundle_job.succeeded or not resource.loaded:
        raise SystemExit("Maa 资源加载失败")
    # 与实际任务一致：前端先加载资源，CustomAction 启动后再强制切换后端。
    if not resource.use_directml():
        raise SystemExit("DirectML 设置失败；未执行 CPU 回退")

    controller = ImageOnlyController(image)
    if not controller.post_connection().wait().succeeded:
        raise SystemExit("只读测试控制器连接失败")

    tasker = Tasker()
    if not tasker.bind(resource, controller) or not tasker.inited:
        raise SystemExit("Maa Tasker 初始化失败")

    elapsed_samples: list[float] = []
    recognition = None
    for _ in range(args.runs):
        started = time.perf_counter()
        job = tasker.post_recognition(
            JRecognitionType.NeuralNetworkDetect,
            JNeuralNetworkDetect(
                model="sprite.onnx",
                expected=[0],
                threshold=[args.threshold],
            ),
            image,
        ).wait()
        elapsed_samples.append((time.perf_counter() - started) * 1000)
        if not job.succeeded:
            raise SystemExit("DirectML 推理失败；未执行 CPU 回退")

        task_detail = job.get()
        recognition = next(
            (
                node.recognition
                for node in reversed(task_detail.nodes)
                if node.recognition is not None
            ),
            None,
        )
        if recognition is None:
            raise SystemExit("DirectML 推理完成，但 Maa 未返回识别详情")

    detections = [
        {
            "box": list(result.box),
            "score": round(float(result.score), 6),
        }
        for result in recognition.all_results
    ]
    print(
        json.dumps(
            {
                "execution_provider": "DirectML",
                "cpu_fallback_requested": False,
                "model": "sprite.onnx",
                "threshold": args.threshold,
                "runs": args.runs,
                "elapsed_ms": [round(value, 1) for value in elapsed_samples],
                "warm_median_ms": round(
                    statistics.median(elapsed_samples[1:] or elapsed_samples),
                    1,
                ),
                "detections": detections,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
