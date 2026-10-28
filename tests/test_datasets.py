import json
from pathlib import Path
import numpy as np
import cv2
from tools.datasets import (detector_dataset, reid_dataset, face_dataset,
                            enrollment_pose_count, hard_pose_ratio)


def test_detector_dataset_reads_yolo_labels(tmp_path):
    root = tmp_path / "det"
    (root / "images").mkdir(parents=True)
    (root / "labels").mkdir()
    img = root / "images" / "a.jpg"
    cv2.imwrite(str(img), np.zeros((100, 100, 3), np.uint8))
    (root / "labels" / "a.txt").write_text("0 0.5 0.5 0.2 0.4\n")
    ds = detector_dataset(root)
    assert len(ds.image_paths()) == 1
    assert ds.class_counts()[0] == 1
    (root / "data.yaml").write_text("names: ['person']\nnc: 1\n")
    assert ds.yaml_path().exists()

def test_reid_identity_layout(tmp_path):
    root = tmp_path / "reid"
    for who in ("0001", "0002"):
        d = root / who
        d.mkdir(parents=True)
        cv2.imwrite(str(d / "a.jpg"), np.zeros((128, 64, 3), np.uint8))
    ds = reid_dataset(root)
    assert set(ds.identities()) == {"0001", "0002"}
    assert len(ds.train_split()) == 2

def test_face_dataset_one_dir_per_identity(tmp_path):
    root = tmp_path / "faces"
    for who in ("alice", "bob"):
        d = root / who; d.mkdir(parents=True)
        cv2.imwrite(str(d / "a.jpg"), np.zeros((160, 160, 3), np.uint8))
    ds = face_dataset(root)
    assert len(ds.identities()) == 2
    assert len(ds.images_for("alice")) == 1

def test_enrollment_guard_detects_clean_only(tmp_path):
    clean = tmp_path / "clean"
    clean.mkdir()
    for i in range(8):
        cv2.imwrite(str(clean / f"{i}.jpg"), np.full((160, 160, 3), 200, np.uint8))
    # a hard pose = a dark/oblique crop; brightness is our stand-in
    assert enrollment_pose_count(clean) == 8
    assert hard_pose_ratio(clean) == 0.0      # all bright, none hard
