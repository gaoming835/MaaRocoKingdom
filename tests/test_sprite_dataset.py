from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from sprite_dataset import (  # noqa: E402
    assign_recording_splits,
    discover_recordings,
    label_has_far_target,
    recording_key,
    write_dataset_splits,
)


class SpriteDatasetTests(unittest.TestCase):
    def test_recording_key_is_stable_and_path_specific(self) -> None:
        first = recording_key(Path("D:/captures/雨天 海滩.mp4"))
        second = recording_key(Path("D:/other/雨天 海滩.mp4"))
        self.assertRegex(first, r"^雨天_海滩_[0-9a-f]{8}$")
        self.assertNotEqual(first, second)

    def test_split_keeps_recordings_disjoint(self) -> None:
        assignments = assign_recording_splits(
            [f"recording_{index}" for index in range(10)]
        )
        self.assertEqual(
            {name: len(values) for name, values in assignments.items()},
            {"train": 7, "val": 2, "test": 1},
        )
        combined = [
            recording
            for values in assignments.values()
            for recording in values
        ]
        self.assertEqual(len(combined), len(set(combined)))

    def test_writes_yolo_lists_and_dataset_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            recordings = [f"recording_{index}" for index in range(3)]
            for recording in recordings:
                image_dir = root / "images" / recording
                label_dir = root / "labels" / recording
                image_dir.mkdir(parents=True)
                label_dir.mkdir(parents=True)
                (image_dir / "frame.jpg").write_bytes(b"fake")
                (label_dir / "frame.txt").write_text("", encoding="utf-8")

            self.assertEqual(discover_recordings(root), recordings)
            assignments = {
                "train": [recordings[0]],
                "val": [recordings[1]],
                "test": [recordings[2]],
            }
            (root / "labels" / recordings[2] / "frame.txt").write_text(
                "0 0.5 0.5 0.03 0.04\n",
                encoding="utf-8",
            )
            counts = write_dataset_splits(root, assignments)

            self.assertEqual(
                counts,
                {"train": 1, "val": 1, "test": 1, "far_test": 1},
            )
            yaml_text = (root / "dataset.yaml").read_text(encoding="utf-8")
            self.assertIn("0: sprite", yaml_text)
            far_yaml_text = (root / "far_dataset.yaml").read_text(
                encoding="utf-8"
            )
            self.assertIn("test: splits/far_test.txt", far_yaml_text)
            far_list = (root / "splits" / "far_test.txt").read_text(
                encoding="utf-8"
            )
            self.assertIn("recording_2", far_list)
            saved_assignments = json.loads(
                (root / "recording_splits.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved_assignments, assignments)

    def test_far_group_uses_normalized_sprite_height(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            label_path = Path(temp_dir) / "frame.txt"
            label_path.write_text(
                "0 0.5 0.5 0.04 0.05\n"
                "0 0.5 0.5 0.20 0.30\n",
                encoding="utf-8",
            )

            self.assertTrue(label_has_far_target(label_path))
            self.assertFalse(
                label_has_far_target(label_path, height_threshold=0.04)
            )

    def test_missing_labels_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            recordings = [f"recording_{index}" for index in range(3)]
            for recording in recordings:
                image_dir = root / "images" / recording
                image_dir.mkdir(parents=True)
                (image_dir / "frame.jpg").write_bytes(b"fake")

            assignments = assign_recording_splits(recordings)
            with self.assertRaisesRegex(ValueError, "缺少同名 YOLO 标签"):
                write_dataset_splits(root, assignments)


if __name__ == "__main__":
    unittest.main()
