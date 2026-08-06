from __future__ import annotations

import json
import os
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


REPO_ROOT = Path(__file__).resolve().parents[1]
TRAIN_ROOT = REPO_ROOT / "debug" / "auto_aim_training"
BASE_PYTHON = Path(
    os.environ.get(
        "CODEX_BUNDLED_PYTHON",
        r"C:\Users\q1503\.cache\codex-runtimes\codex-primary-runtime\dependencies\python",
    )
)
SITE_PACKAGES = TRAIN_ROOT / "venv" / "Lib" / "site-packages"
DATASET = TRAIN_ROOT / "moon_bear_joint_dataset"
OUTPUT = REPO_ROOT / "moon_bear_training_bundle_20260731.zip"
ARCHIVE_ROOT = "moon_bear_training_bundle"


def wanted(path: Path) -> bool:
    return (
        path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix.lower() not in {".pyc", ".pyo"}
    )


def add_tree(
    archive: ZipFile,
    source: Path,
    destination: str,
    skip_names: set[str] | None = None,
) -> tuple[int, int]:
    skip_names = skip_names or set()
    count = 0
    total = 0
    for path in source.rglob("*"):
        if not wanted(path) or path.name in skip_names:
            continue
        relative_path = path.relative_to(source)
        if destination == "portable_python" and relative_path.parts[:2] == (
            "Lib",
            "site-packages",
        ):
            continue
        relative = relative_path.as_posix()
        archive.write(path, f"{ARCHIVE_ROOT}/{destination}/{relative}")
        count += 1
        total += path.stat().st_size
    return count, total


def add_text(archive: ZipFile, relative: str, content: str) -> None:
    archive.writestr(f"{ARCHIVE_ROOT}/{relative}", content)


def main() -> None:
    required = [
        BASE_PYTHON / "python.exe",
        BASE_PYTHON / "Lib",
        SITE_PACKAGES,
        DATASET,
        TRAIN_ROOT / "yolo26s.pt",
        TRAIN_ROOT / "train_moon_bear_huolong_style.py",
        TRAIN_ROOT / "resume_moon_bear_huolong_style.py",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("缺少打包输入:\n" + "\n".join(missing))
    if OUTPUT.exists():
        raise FileExistsError(f"输出文件已存在，请先移走或改名: {OUTPUT}")

    train_code = (TRAIN_ROOT / "train_moon_bear_huolong_style.py").read_text(
        encoding="utf-8"
    )
    train_code = train_code.replace(
        'DATA_YAML = ROOT / "moon_bear_joint_dataset" / "dataset.yaml"',
        'DATA_YAML = ROOT / "dataset.yaml"',
    )
    resume_code = (TRAIN_ROOT / "resume_moon_bear_huolong_style.py").read_text(
        encoding="utf-8"
    )

    launcher = r"""@echo off
setlocal
cd /d "%~dp0"
set "PYTHONNOUSERSITE=1"
set "YOLO_CONFIG_DIR=%~dp0config\Ultralytics"
if not exist "%~dp0portable_python\python.exe" (
    echo [ERROR] portable_python\python.exe not found.
    pause
    exit /b 1
)
echo Starting moon bear training with the bundled environment...
"%~dp0portable_python\python.exe" "%~dp0train_moon_bear_huolong_style.py"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo Training exited with code %RC%.
if "%RC%"=="0" echo Training finished. Check runs\moon_bear_huolong_style\weights\best.pt
pause
exit /b %RC%
"""
    resume_launcher = launcher.replace(
        "train_moon_bear_huolong_style.py",
        "resume_moon_bear_huolong_style.py",
    ).replace(
        "Starting moon bear training with the bundled environment...",
        "Resuming moon bear training with the bundled environment...",
    )
    readme = """月牙雪熊训练包（RocoPilot train_huolong.py 风格）

使用方法：
1. 将整个压缩包解压到一个短路径，建议不要放在中文路径或 OneDrive 同步目录。
2. 双击 run_train.bat，从 yolo26s.pt 开始训练。
3. 训练输出在 runs\\moon_bear_huolong_style\\weights\\best.pt。
4. 如果训练中断，双击 run_resume.bat 从 last.pt 继续。

本包包含 portable Python、CUDA 版 PyTorch/Ultralytics、yolo26s.pt、月牙雪熊数据集、训练脚本和启动入口。

要求：Windows x64、NVIDIA 驱动和可用 CUDA GPU。当前训练参数是 640 输入、batch 8、100 epochs、val=False，
与 RocoPilot 的 train_huolong.py 保持一致。在 4GB GTX 1650 Ti 上训练会很慢；不要关闭窗口或结束 python.exe。
"""
    dataset_yaml = """path: moon_bear_joint_dataset
train: images/train
val: images/val
test: images/test
names:
  0: sprite
"""
    reference_train = '''"""Train YOLO model on huolong dataset (all images for training)."""
import os
import shutil
from ultralytics import YOLO

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_YAML = os.path.join(_BASE, "datasets", "huolong", "data.yaml")
MODEL_PATH = os.path.join(_BASE, "yolo26s.pt")
model = YOLO(MODEL_PATH)
model.train(
    data=DATA_YAML, epochs=100, batch=8, imgsz=640, device=0, workers=0,
    plots=True, verbose=True, val=False, mosaic=1.0, flipud=0.5,
    fliplr=0.5, hsv_h=0.015, hsv_s=0.7, hsv_v=0.4, scale=0.5,
    translate=0.1, erasing=0.4,
)
'''
    environment = json.dumps(
        {
            "python": "3.12.13",
            "ultralytics": "8.4.105",
            "torch": "2.11.0+cu128",
            "input": "640x640",
            "batch": 8,
            "epochs": 100,
            "val": False,
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"

    file_count = 0
    source_bytes = 0
    with ZipFile(
        OUTPUT,
        "w",
        compression=ZIP_DEFLATED,
        compresslevel=1,
        allowZip64=True,
    ) as archive:
        for source, destination, skips in [
            (BASE_PYTHON, "portable_python", set()),
            (SITE_PACKAGES, "portable_python/Lib/site-packages", set()),
            (DATASET, "moon_bear_joint_dataset", {"dataset.yaml"}),
        ]:
            count, size = add_tree(archive, source, destination, skips)
            file_count += count
            source_bytes += size
        base_weight = TRAIN_ROOT / "yolo26s.pt"
        archive.write(base_weight, f"{ARCHIVE_ROOT}/yolo26s.pt")
        file_count += 1
        source_bytes += base_weight.stat().st_size
        add_text(archive, "train_moon_bear_huolong_style.py", train_code)
        add_text(archive, "resume_moon_bear_huolong_style.py", resume_code)
        add_text(archive, "reference_train_huolong.py", reference_train)
        add_text(archive, "dataset.yaml", dataset_yaml)
        add_text(archive, "run_train.bat", launcher)
        add_text(archive, "run_resume.bat", resume_launcher)
        add_text(archive, "README.txt", readme)
        add_text(archive, "environment.json", environment)
        add_text(archive, "config/Ultralytics/README.txt", "运行时配置会写入此目录。\n")

    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "files": file_count,
                "source_bytes": source_bytes,
                "archive_bytes": OUTPUT.stat().st_size,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
