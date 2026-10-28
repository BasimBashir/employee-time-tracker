import argparse
import numpy as np, cv2
from pathlib import Path
from tools.train_detector import build_parser, bootstrap_labels

def test_parser_contract():
    a = build_parser().parse_args(["--data", "datasets/detector", "--epochs", "50"])
    assert a.data == "datasets/detector" and a.epochs == 50
    assert a.imgsz == 640 and a.project == "runs/detector"
    assert a.device is None  # auto: cuda on a GPU box, cpu elsewhere

def test_bootstrap_labels_needs_no_model_when_no_images(tmp_path):
    src = tmp_path / "raw"; src.mkdir()
    out = tmp_path / "ds"
    (out / "images").mkdir(parents=True); (out / "labels").mkdir()
    n = bootstrap_labels(src, out)
    assert n == 0
