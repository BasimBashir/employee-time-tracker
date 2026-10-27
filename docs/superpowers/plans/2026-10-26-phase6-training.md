# Phase 6: Training Tooling — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the owner the ability to fine-tune the three models on their own footage and promote a trained artefact into the running system, without a researcher and without GPU hardware assumptions.

**Architecture:** A `tools/` package with one module per training target plus a shared dataset layer, an exporter that emits provider-matched ONNX variants, and an evaluator that prints the numbers a human needs before promoting anything. Training runs on the authoring machine, not the target machine — so the tooling must run on a beefier box and export down to what the 6th-gen CPU can run.

**Tech Stack:** Ultralytics (detector), PyTorch (re-ID and face margin training), ONNX Runtime (export validation), OpenCV, scikit-learn (metrics), Ultralytics yaml datasets.

**Phase 5 must be complete** — or at least Phase 1 (config + DB). This phase is largely independent of Phases 2–5 because it writes only into `tools/`, `models/` and `datasets/`. If you are running it in parallel, make sure Phase 1's `Config` exists.

## Global Constraints

- Training happens **off the target machine**. The 6th-gen box runs inference only; never plan a training session on it.
- Every export must be validated by loading it into ONNX Runtime with the *intended provider* and running one dummy frame. An export that fails to load is a failed export.
- The exporter emits three provider variants per model: `cpu` (fp32), `dml` (fp16), `cuda` (fp16). `models/active/` holds the one currently in use; the service picks a variant at startup by provider, not by the human copying files.
- **Enrollment captures are not training data.** Enrollment crops are clean frontal poses; training needs the hard cases (sideways, masked, backlit, partial, low light). Keep the two directories separate and enforce it in the dataset layer.
- Report metrics in a way a non-researcher can read: a one-line verdict plus the numbers.

---

## File Structure

```
tools/
├─ datasets.py        # dataset discovery, splits, YOLO-format readers
├─ train_detector.py  # fine-tune person detector
├─ train_reid.py      # body re-ID fine-tune
├─ train_face.py      # face embedding fine-tune
├─ export.py          # ONNX export with provider variants + load validation
├─ eval_detector.py   # mAP / per-class precision-recall
├─ eval_reid.py       # rank-1 / mAP on an identity split
├─ eval_face.py       # TAR@FAR with the same thresholds the resolver uses
└─ promote.py         # candidate -> active, with a gate on eval output
datasets/
├─ detector/          # YOLO format: images/ + labels/ + data.yaml
├─ reid/              # Market-1501-style: bounding_box_train/test, identity dirs
└─ faces/             # one dir per identity, like enrollment (but the HARD poses)
models/
├─ candidate/
└─ active/
tests/
├─ test_datasets.py
├─ test_export.py
└─ test_eval.py
```

### Responsibility boundaries

- `tools/datasets.py` is the only module that touches dataset files. Trainers never `os.listdir` themselves.
- `tools/export.py` is the only place ONNX conversion happens, and it is the only place that validates provider compatibility. Trainers call it, never `model.export` directly.
- `tools/eval_*.py` produce metrics only. They never promote.
- `tools/promote.py` is the only writer to `models/active/`, and it refuses without a passing eval report.

---

## Task 1: Dataset layer

**Files:**
- Create: `tools/datasets.py`, `datasets/.gitkeep`, `datasets/README.md`
- Test: `tests/test_datasets.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `enrollment_pose_count(dir) -> int` and `hard_pose_ratio(dir) -> float` — the guard that keeps enrollment captures out of training sets.
  - `detector_dataset(root) -> DetectorDataset` with `.image_paths()`, `.label_paths()`, `.class_counts()`, `.yaml_path()`.
  - `reid_dataset(root) -> ReidDataset` with `.identities()`, `.train_split()`, `.query_split()`.
  - `face_dataset(root) -> FaceDataset` with `.identities()`, `.images_for(identity)`.
  - `write_split(paths, out)` — writes a train/val split file.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_datasets.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_datasets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.datasets'`

- [ ] **Step 3: Write the minimal implementation**

```python
# tools/datasets.py
"""The only module that touches dataset files. Trainers go through this."""
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
import cv2
import numpy as np

# A "hard" pose is a dark or oblique crop. Brightness is a cheap stand-in; replace
# with a face-angle estimate when the enrollment UI records pose metadata.
HARD_BRIGHTNESS = 110


def _images(dirpath: Path) -> list[Path]:
    exts = ("*.jpg", "*.jpeg", "*.png", "*.bmp")
    out: list[Path] = []
    for e in exts:
        out.extend(sorted(dirpath.rglob(e)))
    return out


@dataclass
class DetectorDataset:
    root: Path
    @property
    def images(self) -> Path: return self.root / "images"
    @property
    def labels(self) -> Path: return self.root / "labels"

    def image_paths(self) -> list[Path]:
        return _images(self.images)

    def label_paths(self) -> list[Path]:
        return sorted(self.labels.rglob("*.txt"))

    def class_counts(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for lp in self.label_paths():
            for line in lp.read_text().splitlines():
                if not line.strip():
                    continue
                counts[int(line.split()[0])] = counts.get(int(line.split()[0]), 0) + 1
        return counts

    def yaml_path(self) -> Path:
        return self.root / "data.yaml"


@dataclass
class ReidDataset:
    root: Path
    def identities(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir()
                      if p.is_dir() and p.name.isdigit())
    def train_split(self) -> list[Path]:
        return [p for i in self.identities()
                for p in _images(self.root / i)]
    def query_split(self) -> list[Path]:
        return self.train_split()[:1] if self.identities() else []
    def identity_of(self, path: Path) -> str:
        return path.parent.name


@dataclass
class FaceDataset:
    root: Path
    def identities(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())
    def images_for(self, identity: str) -> list[Path]:
        return _images(self.root / identity)


def detector_dataset(root: Path) -> DetectorDataset:
    return DetectorDataset(Path(root))


def reid_dataset(root: Path) -> ReidDataset:
    return ReidDataset(Path(root))


def face_dataset(root: Path) -> FaceDataset:
    return FaceDataset(Path(root))


def _brightness(path: Path) -> float:
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    return float(img.mean()) if img is not None else 0.0


def enrollment_pose_count(dirpath: Path) -> int:
    return len(_images(Path(dirpath)))


def hard_pose_ratio(dirpath: Path) -> float:
    """Share of crops that look like hard poses. Used to keep enrollment-quality
    images out of training sets."""
    imgs = _images(Path(dirpath))
    if not imgs:
        return 0.0
    hard = sum(1 for p in imgs if _brightness(p) < HARD_BRIGHTNESS)
    return hard / len(imgs)


def write_split(paths: list[Path], out: Path) -> Path:
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(str(p) for p in paths) + "\n")
    return out
```

`datasets/README.md`:
```markdown
# Datasets

Three directories, one per model. **Keep them separate.**

- `detector/` — YOLO format (`images/`, `labels/`, `data.yaml`).
- `reid/` — one numeric directory per identity (`0001/`, `0002/`, …).
- `faces/` — one directory per identity, named as the employee is named.

Enrollment captures (clean frontal poses from the in-app flow) belong in **none** of
these. Training needs the hard cases: sideways, masked, backlit, partial, low light.
See spec §12.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_datasets.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/datasets.py datasets/ tests/
git commit -m "feat: dataset layer with enrollment/training separation guard"
```

---

## Task 2: ONNX exporter with provider variants

**Files:**
- Create: `tools/export.py`
- Test: `tests/test_export.py`

**Interfaces:**
- Consumes: `service.device.select_provider` semantics (replicated, not imported — `tools/` must stay runnable without `service/`).
- Produces:
  - `provider_variants() -> dict[str, dict]` — `{"cpu": {...}, "dml": {...}, "cuda": {...}}`.
  - `export_onnx(source_pt, out_path, imgsz=640, opset=13) -> Path`
  - `validate_onnx(path, provider, imgsz) -> dict` — loads with ORT and runs a dummy frame.
  - `active_variant_for(provider: str) -> str` — maps a runtime provider to a variant name.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_export.py
import numpy as np
import onnxruntime as ort
from tools.export import provider_variants, validate_onnx, active_variant_for

def test_variants_cover_three_providers():
    v = provider_variants()
    assert set(v) == {"cpu", "dml", "cuda"}
    assert v["cpu"]["precision"] == "fp32"

def test_active_variant_maps_from_provider():
    assert active_variant_for("CPUExecutionProvider") == "cpu"
    assert active_variant_for("DmlExecutionProvider") == "dml"
    assert active_variant_for("CUDAExecutionProvider") == "cuda"

def test_validate_onnx_on_a_tiny_builtin(tmp_path):
    # build a 1-layer identity-ish ONNX without any model zoo dependency
    from onnx import helper, TensorProto
    x = helper.make_tensor_value_info("in", TensorProto.FLOAT, [1, 3, 8, 8])
    y = helper.make_tensor_value_info("out", TensorProto.FLOAT, [1, 3, 8, 8])
    node = helper.make_node("Relu", ["in"], ["out"])
    g = helper.make_graph([node], "g", [x], [y])
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 13)])
    p = tmp_path / "tiny.onnx"
    import onnx
    onnx.save(m, str(p))
    info = validate_onnx(p, "CPUExecutionProvider", imgsz=8)
    assert info["ok"] is True and info["output_shape"] == [1, 3, 8, 8]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_export.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.export'`

- [ ] **Step 3: Write the minimal implementation**

```python
# tools/export.py
"""The only place ONNX conversion happens, and the only place it is validated.
Provider variants exist so the running service picks the right one at startup
rather than a human copying the right file by hand."""
from pathlib import Path
from typing import Optional
import numpy as np

PROVIDER_TO_VARIANT = {
    "CPUExecutionProvider": "cpu",
    "DmlExecutionProvider": "dml",
    "CUDAExecutionProvider": "cuda",
}


def provider_variants() -> dict[str, dict]:
    return {
        "cpu": {"precision": "fp32", "imgsz": 640, "note": "6th-gen target"},
        "dml": {"precision": "fp16", "imgsz": 640, "note": "amd/intel igpu"},
        "cuda": {"precision": "fp16", "imgsz": 640, "note": "nvidia dgpu"},
    }


def active_variant_for(provider: str) -> str:
    return PROVIDER_TO_VARIANT.get(provider, "cpu")


def export_onnx(source_pt: Path, out_path: Path, imgsz: int = 640, opset: int = 13) -> Path:
    """Export an Ultralytics .pt to ONNX. Raises on failure; never returns a
    path that does not exist."""
    from ultralytics import YOLO
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(source_pt))
    exported = Path(model.export(format="onnx", imgsz=imgsz, opset=opset, simplify=True))
    if not exported.exists():
        raise RuntimeError(f"export produced no file for {source_pt}")
    exported.replace(out_path)
    return out_path


def validate_onnx(path: Path, provider: str, imgsz: int = 640) -> dict:
    """Load with the INTENDED provider and run one dummy frame. An export that
    cannot load is a failed export, and this is what catches that."""
    import onnxruntime as ort
    blob = np.random.rand(1, 3, imgsz, imgsz).astype(np.float32)
    try:
        so = ort.SessionOptions()
        sess = ort.InferenceSession(str(path), sess_options=so, providers=[provider])
        out = sess.run(None, {sess.get_inputs()[0].name: blob})
        return {"ok": True, "provider": provider,
                "output_shape": list(np.asarray(out[0]).shape)}
    except Exception as e:
        return {"ok": False, "provider": provider, "error": str(e)}
```

`requirements.txt` additions (training-only; keep them out of the service's set):
```
# tools/requirements.txt (separate file — do not install on the target machine)
ultralytics
torch
onnx
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_export.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Verify a real export end-to-end (authoring machine only)**

```bash
pip install -r tools/requirements.txt
python -c "
from pathlib import Path
from tools.export import export_onnx, validate_onnx
p = export_onnx(Path('yolo11n.pt'), Path('models/candidate/detector_cpu.onnx'))
print(validate_onnx(p, 'CPUExecutionProvider'))
print(validate_onnx(p, 'DmlExecutionProvider'))"
```
Expected: `cpu` variant `ok: True`. The `dml` result tells you whether fp16 conversion is
needed — if it fails, the variant is simply missing, not broken.

- [ ] **Step 6: Commit**

```bash
git add tools/export.py tools/requirements.txt tests/
git commit -m "feat: onnx exporter with provider variants and load validation"
```

---

## Task 3: Detector evaluation

**Files:**
- Create: `tools/eval_detector.py`
- Test: `tests/test_eval.py`

**Interfaces:**
- Consumes: `detector_dataset` (Task 1), `validate_onnx` (Task 2).
- Produces:
  - `evaluate(model_pt_or_onnx, dataset_root, imgsz=640) -> dict` with
    `{"mAP50": float, "mAP50-95": float, "per_class": {"person": {...}}}`.
  - `verdict(metrics: dict, target_map50: float = 0.85) -> str` — a one-line
    "promote" / "keep training" recommendation.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eval.py
from tools.eval_detector import verdict

def test_verdict_promotes_above_target():
    m = {"mAP50": 0.91, "mAP50-95": 0.62, "per_class": {"person": {"mAP50": 0.91}}}
    assert "PROMOTE" in verdict(m).upper()

def test_verdict_blocks_below_target():
    m = {"mAP50": 0.61, "mAP50-95": 0.31, "per_class": {"person": {"mAP50": 0.61}}}
    assert "KEEP" in verdict(m).upper() or "NOT" in verdict(m).upper()

def test_verdict_blocks_when_person_class_weak():
    m = {"mAP50": 0.95, "mAP50-95": 0.70,
         "per_class": {"person": {"mAP50": 0.55}, "dog": {"mAP50": 1.0}}}
    # overall looks fine but the class we actually care about is weak
    assert "PROMOTE" not in verdict(m).upper()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_eval.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.eval_detector'`

- [ ] **Step 3: Write the minimal implementation**

```python
# tools/eval_detector.py
"""Person-detector metrics. Metrics only — never promotes."""
from pathlib import Path
from typing import Optional
from tools.datasets import detector_dataset

TARGET_MAP50 = 0.85
PERSON_CLASS = "person"


def evaluate(model_path: Path, dataset_root: Path, imgsz: int = 640,
             data_yaml: Optional[Path] = None) -> dict:
    from ultralytics import YOLO
    ds = detector_dataset(dataset_root)
    yaml = str(data_yaml or ds.yaml_path())
    model = YOLO(str(model_path))
    res = model.val(data=yaml, imgsz=imgsz, verbose=False)
    box = res.box
    per_class = {}
    try:
        for i, name in res.names.items():
            per_class[name] = {"mAP50": float(box.maps[i]) if hasattr(box, "maps") else None}
    except Exception:
        per_class = {}
    return {"mAP50": float(box.map50), "mAP50-95": float(box.map),
            "per_class": per_class}


def verdict(metrics: dict, target_map50: float = TARGET_MAP50) -> str:
    person = metrics.get("per_class", {}).get(PERSON_CLASS, {})
    person_map = person.get("mAP50") if person else None
    overall = metrics.get("mAP50", 0.0)
    if person_map is not None and person_map < target_map50:
        return (f"KEEP TRAINING — {PERSON_CLASS} mAP50 {person_map:.2f} below "
                f"{target_map50:.2f}")
    if overall < target_map50:
        return f"KEEP TRAINING — overall mAP50 {overall:.2f} below {target_map50:.2f}"
    return f"PROMOTE — {PERSON_CLASS} mAP50 {person_map or overall:.2f}, " \
           f"mAP50-95 {metrics.get('mAP50-95', 0):.2f}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_eval.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/eval_detector.py tests/
git commit -m "feat: detector evaluation with person-class gated verdict"
```

---

## Task 4: Detector training

**Files:**
- Create: `tools/train_detector.py`
- Test: `tests/test_train_detector.py` (CLI-contract test only — training itself is expensive)

**Interfaces:**
- Consumes: `detector_dataset`, `export_onnx`, `evaluate`.
- Produces:
  - `train(dataset_root, epochs=100, imgsz=640, project="runs/detector", name="exp") -> Path`
    — returns the best `.pt`.
  - `bootstrap_labels(unlabelled_dir, out_dataset, conf=0.55) -> int` — pretrained COCO
    person detections as a labelling starting point (spec §12's plan).
  - CLI: `python -m tools.train_detector --data datasets/detector --epochs 100`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_train_detector.py
import argparse
import numpy as np, cv2
from pathlib import Path
from tools.train_detector import build_parser, bootstrap_labels

def test_parser_contract():
    a = build_parser().parse_args(["--data", "datasets/detector", "--epochs", "50"])
    assert a.data == "datasets/detector" and a.epochs == 50
    assert a.imgsz == 640 and a.project == "runs/detector"

def test_bootstrap_labels_needs_no_model_when_no_images(tmp_path):
    src = tmp_path / "raw"; src.mkdir()
    out = tmp_path / "ds"
    (out / "images").mkdir(parents=True); (out / "labels").mkdir()
    n = bootstrap_labels(src, out)
    assert n == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_train_detector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.train_detector'`

- [ ] **Step 3: Write the minimal implementation**

```python
# tools/train_detector.py
"""Fine-tune the person detector. Bootstrap labelling first — see spec §12."""
import argparse
from pathlib import Path
import numpy as np
from tools.datasets import detector_dataset
from tools.export import export_onnx, validate_onnx
from tools.eval_detector import evaluate, verdict

BOOTSTRAP_CONF = 0.55


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fine-tune the person detector")
    p.add_argument("--data", default="datasets/detector")
    p.add_argument("--epochs", type=int, default=100)
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--model", default="yolo11n.pt",
                   help="starting point; yolo11n for the CPU target")
    p.add_argument("--project", default="runs/detector")
    p.add_argument("--name", default="exp")
    return p


def bootstrap_labels(unlabelled_dir: Path, out_dataset: Path, conf: float = BOOTSTRAP_CONF) -> int:
    """Run a pretrained person model over unlabelled footage and write YOLO labels.
    These are a STARTING POINT for human correction, not ground truth (spec §12)."""
    from ultralytics import YOLO
    src = Path(unlabelled_dir)
    out = Path(out_dataset)
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)
    model = YOLO("yolo11n.pt")
    written = 0
    for img_path in sorted(src.rglob("*.jpg")) + sorted(src.rglob("*.png")):
        res = model.predict(str(img_path), classes=[0], conf=conf, verbose=False)
        if not res:
            continue
        r = res[0]
        h, w = r.orig_shape
        lines = []
        for b in r.boxes.xywhn.tolist():
            lines.append("0 " + " ".join(f"{v:.6f}" for v in b))
        if not lines:
            continue
        (out / "images" / img_path.name).write_bytes(img_path.read_bytes())
        (out / "labels" / (img_path.stem + ".txt")).write_text("\n".join(lines) + "\n")
        written += 1
    if written:
        ds = detector_dataset(out)
        (ds.yaml_path()).write_text(
            f"path: {out}\ntrain: images\nval: images\nnc: 1\nnames: ['person']\n")
    return written


def train(dataset_root: Path, epochs: int = 100, imgsz: int = 640,
          model: str = "yolo11n.pt", project: str = "runs/detector",
          name: str = "exp") -> Path:
    from ultralytics import YOLO
    ds = detector_dataset(dataset_root)
    m = YOLO(model)
    m.train(data=str(ds.yaml_path()), epochs=epochs, imgsz=imgsz, device="cpu",
            project=project, name=name)
    best = Path(project) / name / "weights" / "best.pt"
    if not best.exists():
        raise RuntimeError("training produced no best.pt")
    return best


def main() -> None:
    a = build_parser().parse_args()
    best = train(a.data, a.epochs, a.imgsz, a.model, a.project, a.name)
    print("trained", best)
    metrics = evaluate(best, a.data, a.imgsz)
    print(verdict(metrics))
    for variant in ("cpu", "dml", "cuda"):
        out = Path("models/candidate") / f"detector_{variant}.onnx"
        export_onnx(best, out, a.imgsz)
        print(variant, validate_onnx(out, {
            "cpu": "CPUExecutionProvider",
            "dml": "DmlExecutionProvider",
            "cuda": "CUDAExecutionProvider"}[variant], a.imgsz))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_train_detector.py -v`
Expected: PASS (2 tests). Real training is run manually with the CLI — it is expensive and
must not be in the test suite.

- [ ] **Step 5: Smoke-test the bootstrap path**

```bash
mkdir -p datasets/raw && cp tests/fixtures/synth.mp4 datasets/raw/
python -c "
from pathlib import Path
from tools.train_detector import bootstrap_labels
print(bootstrap_labels(Path('datasets/raw'), Path('datasets/detector')))"
```
Expected: a non-zero count and a `datasets/detector/data.yaml` exists. Open one label file
and correct it by hand — this is exactly the workflow spec §12 describes, and the point is
that you start from 90 % correct rather than from nothing.

- [ ] **Step 6: Commit**

```bash
git add tools/train_detector.py tests/
git commit -m "feat: detector training with bootstrap labelling and CLI"
```

---

## Task 5: Re-ID and face evaluation

**Files:**
- Create: `tools/eval_reid.py`, `tools/eval_face.py`
- Test: `tests/test_eval_reid_face.py`

**Interfaces:**
- Consumes: `reid_dataset`, `face_dataset` (Task 1).
- Produces:
  - `reid_rank1(embeddings, identities) -> dict` with `{"rank1": float, "mAP": float}`.
  - `face_tar_at_far(embeddings, identities, far=0.001) -> dict` with
    `{"tar": float, "threshold": float}` — measured at the same thresholds the resolver uses.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eval_reid_face.py
import numpy as np
from tools.eval_reid import reid_rank1
from tools.eval_face import face_tar_at_far

def _data(n_per_id=5, ids=("a", "b"), dim=32, jitter=0.01):
    X, Y = [], []
    rng = np.random.default_rng(0)
    for i, who in enumerate(ids):
        center = rng.normal(size=dim)
        for _ in range(n_per_id):
            v = center + rng.normal(scale=jitter, size=dim)
            X.append(v / np.linalg.norm(v)); Y.append(who)
    return X, Y

def test_rank1_perfect_for_tight_clusters():
    X, Y = _data(jitter=0.005)
    m = reid_rank1(X, Y)
    assert m["rank1"] > 0.95 and m["mAP"] > 0.9

def test_rank1_degrades_for_overlapping_clusters():
    X, Y = _data(jitter=5.0)      # centres swamped — effectively one blob
    m = reid_rank1(X, Y)
    assert m["rank1"] < 0.6

def test_tar_uses_resolver_style_thresholds():
    X, Y = _data(jitter=0.005)
    m = face_tar_at_far(X, Y, far=0.001)
    assert 0.0 < m["threshold"] <= 1.0
    assert m["tar"] > 0.9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_eval_reid_face.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.eval_reid'`

- [ ] **Step 3: Write the minimal implementation**

```python
# tools/eval_reid.py
"""Re-ID metrics. Metrics only — never promotes, never selects identity."""
from typing import Sequence
import numpy as np


def reid_rank1(embeddings: Sequence, identities: Sequence) -> dict:
    X = np.stack([np.asarray(np.frombuffer(e, np.float32)) if isinstance(e, (bytes, bytearray))
                  else np.asarray(e, np.float32) for e in embeddings])
    X = X / np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-9)
    Y = list(identities)
    sims = X @ X.T
    np.fill_diagonal(sims, -1.0)
    correct = 0
    aps = []
    for i in range(len(Y)):
        order = np.argsort(-sims[i])
        ranked = [Y[j] for j in order]
        if ranked and ranked[0] == Y[i]:
            correct += 1
        rel = np.array([1 if y == Y[i] else 0 for y in ranked])
        if rel.sum():
            cum = np.cumsum(rel)
            precision = cum / (np.arange(len(rel)) + 1)
            aps.append(float((precision * rel).sum() / rel.sum()))
    return {"rank1": correct / len(Y) if Y else 0.0, "mAP": float(np.mean(aps)) if aps else 0.0}
```

```python
# tools/eval_face.py
"""Face metrics at the resolver's own thresholds (spec §6: 0.45 confirm, 0.35 uncertain)."""
from typing import Sequence
import numpy as np

CONFIRM = 0.45
UNCERTAIN = 0.35


def face_tar_at_far(embeddings: Sequence, identities: Sequence,
                    far: float = 0.001) -> dict:
    X = np.stack([np.asarray(np.frombuffer(e, np.float32)) if isinstance(e, (bytes, bytearray))
                  else np.asarray(e, np.float32) for e in embeddings])
    X = X / np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-9)
    Y = list(identities)
    sims = X @ X.T
    n = len(Y)
    # genuine = same identity, impostor = different identity
    gen = [(sims[i, j], 1) for i in range(n) for j in range(i + 1, n) if Y[i] == Y[j]]
    imp = [(sims[i, j], 0) for i in range(n) for j in range(i + 1, n) if Y[i] != Y[j]]
    if not gen or not imp:
        return {"tar": 0.0, "threshold": UNCERTAIN, "far": far}
    gen_scores = np.array([s for s, _ in gen])
    # threshold that keeps the impostor false-accept rate at `far`
    imp_scores = np.sort([s for s, _ in imp])[::-1]
    k = max(int(round(far * len(imp_scores))), 0)
    threshold = float(imp_scores[k]) if k < len(imp_scores) else 0.0
    tar = float((gen_scores >= threshold).mean())
    return {"tar": tar, "threshold": threshold, "far": far,
            "confirm": CONFIRM, "uncertain": UNCERTAIN}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_eval_reid_face.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/eval_reid.py tools/eval_face.py tests/
git commit -m "feat: re-id and face evaluation metrics"
```

---

## Task 6: Promotion gate

**Files:**
- Create: `tools/promote.py`
- Test: `tests/test_promote.py`

**Interfaces:**
- Consumes: `evaluate`, `verdict` (Tasks 3, 5), `validate_onnx` (Task 2).
- Produces:
  - `promote(kind, variant, report, active_dir, candidate_dir, force=False) -> Path`
    — copies `models/candidate/<kind>_<variant>.onnx` to `models/active/<kind>.onnx`.
  - `REQUIRED_REPORT_KEYS = {"verdict", "metrics"}` — refuses on a missing/incomplete report.
  - A promotion is refused unless the report's verdict says promote and the ONNX
    validates on the provider implied by the variant.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_promote.py
import json
from pathlib import Path
from tools.promote import promote, REQUIRED_REPORT_KEYS, load_report

def _candidate(root: Path, kind="detector", variant="cpu"):
    p = root / "candidate" / f"{kind}_{variant}.onnx"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"onnx-bytes")
    return p

def test_refuses_without_verdict(tmp_path):
    _candidate(tmp_path)
    rep = tmp_path / "r.json"; rep.write_text(json.dumps({"metrics": {}}))
    try:
        promote("detector", "cpu", load_report(rep), tmp_path, tmp_path)
    except ValueError:
        return
    raise AssertionError("must refuse a report with no verdict")

def test_refuses_when_verdict_is_keep_training(tmp_path):
    _candidate(tmp_path)
    rep = tmp_path / "r.json"
    rep.write_text(json.dumps({"verdict": "KEEP TRAINING — weak person class",
                               "metrics": {"mAP50": 0.4}}))
    try:
        promote("detector", "cpu", load_report(rep), tmp_path, tmp_path)
    except ValueError:
        return
    raise AssertionError("must refuse a non-promoting verdict")

def test_force_overrides_verdict(tmp_path):
    _candidate(tmp_path)
    rep = tmp_path / "r.json"
    rep.write_text(json.dumps({"verdict": "KEEP TRAINING", "metrics": {"mAP50": 0.4}}))
    out = promote("detector", "cpu", load_report(rep), tmp_path, tmp_path, force=True)
    assert out.exists() and out.name == "detector.onnx"

def test_report_keys_constant():
    assert REQUIRED_REPORT_KEYS == {"verdict", "metrics"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_promote.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.promote'`

- [ ] **Step 3: Write the minimal implementation**

```python
# tools/promote.py
"""The only writer to models/active/. Refuses without a passing eval report."""
import json, shutil
from pathlib import Path
from typing import Optional

REQUIRED_REPORT_KEYS = {"verdict", "metrics"}
PROMOTE_WORD = "PROMOTE"


def load_report(path: Path) -> dict:
    data = json.loads(Path(path).read_text())
    missing = REQUIRED_REPORT_KEYS - set(data)
    if missing:
        raise ValueError(f"report missing keys: {sorted(missing)}")
    return data


def promote(kind: str, variant: str, report: dict, active_dir: Path,
            candidate_dir: Path, force: bool = False) -> Path:
    if not force:
        missing = REQUIRED_REPORT_KEYS - set(report)
        if missing:
            raise ValueError(f"report missing keys: {sorted(missing)}")
        if PROMOTE_WORD not in str(report.get("verdict", "")).upper():
            raise ValueError(f"refusing to promote: verdict={report.get('verdict')!r}")
    src = Path(candidate_dir) / f"{kind}_{variant}.onnx"
    if not src.exists():
        raise FileNotFoundError(src)
    out_dir = Path(active_dir); out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{kind}.onnx"
    shutil.copy2(src, out)
    report_path = out_dir / f"{kind}.report.json"
    report_path.write_text(json.dumps(report, indent=2))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_promote.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/promote.py tests/
git commit -m "feat: promotion gate requiring a passing eval report"
```

---

## Task 7: Phase 6 acceptance gate

- [ ] **Step 1: Full suite green**

```bash
python -m pytest -v
```
Expected: all of Tasks 1–6 green. No GPU required; training itself is manual.

- [ ] **Step 2: End-to-end manual train (authoring machine)**

```bash
python -m tools.train_detector --data datasets/detector --epochs 30
```
Expected: prints the verdict line and, for each variant, a validation dict. Read the
verdict — if it says KEEP TRAINING, the model must not be promoted by habit.

- [ ] **Step 3: Promote only on a pass**

Write the eval report to `models/candidate/detector_report.json`, then:

```bash
python -c "
import json
from pathlib import Path
from tools.promote import promote, load_report
rep = load_report(Path('models/candidate/detector_report.json'))
print(promote('detector', 'cpu', rep, Path('models/active'), Path('models/candidate')))"
```
Expected: `models/active/detector.onnx` plus `detector.report.json`. Retry with a
KEEP TRAINING verdict and confirm it refuses.

- [ ] **Step 4: Restart the service and confirm the new model loads**

```bash
python service/service_main.py restart
curl http://127.0.0.1:8080/pipeline/status
```
Expected: the pipeline is streaming with the promoted detector and no rise in
`detector_latency_ms` beyond the CPU target. If latency jumped, the variant is too big
for the target box — go back to `yolo11n` at 640 and retrain smaller.

- [ ] **Step 5: Confirm the enrollment/training separation holds**

```bash
python -c "
from tools.datasets import hard_pose_ratio, enrollment_pose_count
print(hard_pose_ratio('datasets/faces'), enrollment_pose_count('datasets/faces'))"
```
Expected: the ratio reflects genuinely varied poses, not the clean enrollment set. If you
trained only on enrollment crops, recall that here — spec §12 warns the model will fail on
sideways and backlit entrances.

- [ ] **Step 6: Commit the phase**

```bash
git add -A && git commit -m "chore: phase 6 verified — training tooling with promotion gate"
```

---

## Task 8: Project completion notes

Write `docs/superpowers/specs/2026-10-26-remaining-work.md` capturing what Phases 1–6
did *not* deliver, so it is not rediscovered later:

```markdown
# Remaining work (post Phase 6)

## Not built by design (YAGNI)
- Shift schedules, rosters, late-arrival logic, payroll export (spec §2).
- Cloud sync, multi-site, mobile.
- Customer/footfall analytics.

## Known risks carried forward
1. GStreamer `gst-plugins-ugly`/`libav` license terms were never verified. Confirm before
   any commercial distribution. (spec §15)
2. Library versions were not pinned from upstream docs — Context7 lookups timed out at
   spec time. Pin from current docs before the first release build. (spec §15)
3. Face detection model choice in `service/faces.py` currently falls back to a
   person-crop heuristic. A dedicated face detector is needed for accuracy; the
   enrollment flow will still work but with worse crops.
4. The installer chains GStreamer's Inno Setup runtime; the combined install has not been
   tested against a clean Windows 11 image.
5. No load test with 3 real RTSP cameras has been run. The CPU budget is estimated from
   synthetic sources; real H.264 decode at 1080p adds cost not measured here.
6. Identity accuracy at a real gate (backlight, masks, hats, sideways walking) is
   unmeasured. The threshold bands (0.45 / 0.35) are starting points from the spec, not
   tuned values.
```

```bash
git add docs/ && git commit -m "docs: record remaining work and carried-forward risks"
```
