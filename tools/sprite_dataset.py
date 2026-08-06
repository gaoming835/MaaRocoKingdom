from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
DEFAULT_SAMPLE_FPS = 2.0
DEFAULT_DEDUPE_THRESHOLD = 3.0
DEFAULT_SEED = 20260725
DEFAULT_FAR_HEIGHT_THRESHOLD = 0.05


@dataclass(frozen=True)
class ExtractionResult:
    recording: str
    source: str
    sampled: int
    kept: int


def recording_key(video_path: Path) -> str:
    key = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", video_path.stem).strip("._")
    if not key:
        key = "recording"
    digest = hashlib.sha1(
        str(video_path.resolve()).encode("utf-8")
    ).hexdigest()[:8]
    return f"{key}_{digest}"


def extract_recording(
    video_path: Path,
    dataset_root: Path,
    *,
    sample_fps: float = DEFAULT_SAMPLE_FPS,
    dedupe_threshold: float = DEFAULT_DEDUPE_THRESHOLD,
    jpeg_quality: int = 95,
) -> ExtractionResult:
    if sample_fps <= 0:
        raise ValueError("sample_fps 必须大于 0")
    if dedupe_threshold < 0:
        raise ValueError("dedupe_threshold 不能为负数")
    if not 1 <= jpeg_quality <= 100:
        raise ValueError("jpeg_quality 必须在 1～100 之间")
    if not video_path.is_file():
        raise FileNotFoundError(video_path)

    try:
        import cv2
        import numpy
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "抽帧需要安装 tools/sprite_dataset.requirements.txt"
        ) from exc

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"无法打开录屏：{video_path}")

    source_fps = float(capture.get(cv2.CAP_PROP_FPS))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if source_fps <= 0 or frame_count <= 0:
        capture.release()
        raise RuntimeError(f"无法读取录屏时长：{video_path}")

    duration_seconds = frame_count / source_fps
    key = recording_key(video_path)
    image_dir = dataset_root / "images" / key
    label_dir = dataset_root / "labels" / key
    image_dir.mkdir(parents=True, exist_ok=True)
    label_dir.mkdir(parents=True, exist_ok=True)

    sampled = 0
    kept = 0
    previous_preview = None
    sample_index = 0
    try:
        while True:
            time_seconds = sample_index / sample_fps
            if time_seconds > duration_seconds:
                break
            capture.set(cv2.CAP_PROP_POS_MSEC, time_seconds * 1000)
            succeeded, frame = capture.read()
            if not succeeded:
                break
            sampled += 1

            preview = cv2.resize(frame, (64, 36), interpolation=cv2.INTER_AREA)
            preview = cv2.cvtColor(preview, cv2.COLOR_BGR2GRAY)
            keep = previous_preview is None
            if previous_preview is not None:
                difference = numpy.mean(
                    numpy.abs(
                        preview.astype("int16")
                        - previous_preview.astype("int16")
                    )
                )
                keep = float(difference) >= dedupe_threshold

            if keep:
                timestamp_ms = round(time_seconds * 1000)
                output_path = image_dir / (
                    f"frame_{kept:06d}_{timestamp_ms:09d}ms.jpg"
                )
                if not cv2.imwrite(
                    str(output_path),
                    frame,
                    [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality],
                ):
                    raise RuntimeError(f"无法写入抽帧图片：{output_path}")
                previous_preview = preview
                kept += 1
            sample_index += 1
    finally:
        capture.release()

    return ExtractionResult(
        recording=key,
        source=str(video_path.resolve()),
        sampled=sampled,
        kept=kept,
    )


def assign_recording_splits(
    recordings: Sequence[str],
    *,
    seed: int = DEFAULT_SEED,
) -> dict[str, list[str]]:
    unique = sorted(set(recordings))
    if len(unique) < 3:
        raise ValueError("至少需要三段独立录屏才能划分 train/val/test")

    shuffled = unique[:]
    random.Random(seed).shuffle(shuffled)
    total = len(shuffled)
    train_count = max(1, round(total * 0.70))
    val_count = max(1, round(total * 0.15))
    while train_count + val_count > total - 1:
        if train_count > 1:
            train_count -= 1
        else:
            val_count -= 1

    return {
        "train": shuffled[:train_count],
        "val": shuffled[train_count : train_count + val_count],
        "test": shuffled[train_count + val_count :],
    }


def discover_recordings(dataset_root: Path) -> list[str]:
    image_root = dataset_root / "images"
    if not image_root.is_dir():
        return []
    return sorted(
        path.name
        for path in image_root.iterdir()
        if path.is_dir() and any(iter_images(path))
    )


def iter_images(path: Path) -> Iterable[Path]:
    return (
        candidate
        for candidate in sorted(path.iterdir())
        if candidate.is_file() and candidate.suffix.lower() in IMAGE_EXTENSIONS
    )


def label_has_far_target(
    label_path: Path,
    *,
    height_threshold: float = DEFAULT_FAR_HEIGHT_THRESHOLD,
) -> bool:
    if not 0 < height_threshold <= 1:
        raise ValueError("远距离目标高度阈值必须在 0～1 之间")
    if not label_path.is_file():
        return False

    for line in label_path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) != 5:
            continue
        try:
            class_id = int(fields[0])
            normalized_height = float(fields[4])
        except ValueError:
            continue
        if class_id == 0 and 0 < normalized_height <= height_threshold:
            return True
    return False


def write_dataset_splits(
    dataset_root: Path,
    assignments: dict[str, list[str]],
    *,
    allow_missing_labels: bool = False,
    far_height_threshold: float = DEFAULT_FAR_HEIGHT_THRESHOLD,
) -> dict[str, int]:
    if not 0 < far_height_threshold <= 1:
        raise ValueError("远距离目标高度阈值必须在 0～1 之间")

    split_dir = dataset_root / "splits"
    split_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    missing_labels: list[Path] = []
    far_test_paths: list[Path] = []

    for split_name in ("train", "val", "test"):
        image_paths: list[Path] = []
        for recording in assignments.get(split_name, []):
            recording_images = dataset_root / "images" / recording
            for image_path in iter_images(recording_images):
                label_path = (
                    dataset_root
                    / "labels"
                    / recording
                    / f"{image_path.stem}.txt"
                )
                if not label_path.is_file():
                    missing_labels.append(label_path)
                elif split_name == "test" and label_has_far_target(
                    label_path,
                    height_threshold=far_height_threshold,
                ):
                    far_test_paths.append(image_path.resolve())
                image_paths.append(image_path.resolve())

        content = "\n".join(path.as_posix() for path in image_paths)
        if content:
            content += "\n"
        (split_dir / f"{split_name}.txt").write_text(
            content,
            encoding="utf-8",
        )
        counts[split_name] = len(image_paths)

    far_content = "\n".join(path.as_posix() for path in far_test_paths)
    if far_content:
        far_content += "\n"
    (split_dir / "far_test.txt").write_text(
        far_content,
        encoding="utf-8",
    )
    counts["far_test"] = len(far_test_paths)

    if missing_labels and not allow_missing_labels:
        examples = "、".join(str(path) for path in missing_labels[:3])
        raise ValueError(
            f"有 {len(missing_labels)} 张图片缺少同名 YOLO 标签，例如：{examples}"
        )

    dataset_yaml = (
        f"path: {json.dumps(str(dataset_root.resolve()), ensure_ascii=False)}\n"
        "train: splits/train.txt\n"
        "val: splits/val.txt\n"
        "test: splits/test.txt\n"
        "names:\n"
        "  0: sprite\n"
    )
    (dataset_root / "dataset.yaml").write_text(dataset_yaml, encoding="utf-8")
    far_dataset_yaml = (
        f"path: {json.dumps(str(dataset_root.resolve()), ensure_ascii=False)}\n"
        "train: splits/train.txt\n"
        "val: splits/far_test.txt\n"
        "test: splits/far_test.txt\n"
        "names:\n"
        "  0: sprite\n"
    )
    (dataset_root / "far_dataset.yaml").write_text(
        far_dataset_yaml,
        encoding="utf-8",
    )
    (dataset_root / "recording_splits.json").write_text(
        json.dumps(assignments, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return counts


def update_extraction_manifest(
    dataset_root: Path,
    results: Sequence[ExtractionResult],
) -> None:
    manifest_path = dataset_root / "recordings.json"
    existing: dict[str, dict[str, object]] = {}
    if manifest_path.is_file():
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            existing = loaded

    for result in results:
        existing[result.recording] = {
            "source": result.source,
            "sampled": result.sampled,
            "kept": result.kept,
        }
    manifest_path.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从洛克王国世界录屏准备单类别 YOLO 精灵数据集"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract = subparsers.add_parser("extract", help="按固定 FPS 抽帧并去重")
    extract.add_argument("dataset_root", type=Path)
    extract.add_argument("videos", type=Path, nargs="+")
    extract.add_argument("--fps", type=float, default=DEFAULT_SAMPLE_FPS)
    extract.add_argument(
        "--dedupe-threshold",
        type=float,
        default=DEFAULT_DEDUPE_THRESHOLD,
    )
    extract.add_argument("--jpeg-quality", type=int, default=95)

    split = subparsers.add_parser("split", help="按整段录屏划分数据集")
    split.add_argument("dataset_root", type=Path)
    split.add_argument("--seed", type=int, default=DEFAULT_SEED)
    split.add_argument("--allow-missing-labels", action="store_true")
    split.add_argument(
        "--far-height-threshold",
        type=float,
        default=DEFAULT_FAR_HEIGHT_THRESHOLD,
        help="归入远距离测试清单的精灵框归一化高度上限",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "extract":
        unsupported = [
            path
            for path in args.videos
            if path.suffix.lower() not in VIDEO_EXTENSIONS
        ]
        if unsupported:
            raise ValueError(f"不支持的录屏格式：{unsupported[0]}")
        results = [
            extract_recording(
                video,
                args.dataset_root,
                sample_fps=args.fps,
                dedupe_threshold=args.dedupe_threshold,
                jpeg_quality=args.jpeg_quality,
            )
            for video in args.videos
        ]
        update_extraction_manifest(args.dataset_root, results)
        for result in results:
            print(
                f"{result.recording}: 抽样 {result.sampled}，"
                f"保留 {result.kept}"
            )
        return 0

    recordings = discover_recordings(args.dataset_root)
    assignments = assign_recording_splits(recordings, seed=args.seed)
    counts = write_dataset_splits(
        args.dataset_root,
        assignments,
        allow_missing_labels=args.allow_missing_labels,
        far_height_threshold=args.far_height_threshold,
    )
    print(
        "数据集划分完成："
        + "，".join(f"{name} {count} 张" for name, count in counts.items())
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
