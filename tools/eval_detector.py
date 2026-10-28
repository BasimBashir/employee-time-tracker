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
