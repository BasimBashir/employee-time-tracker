"""Fine-tune the person detector. Bootstrap labelling first — see spec §12."""
import argparse
from pathlib import Path
from tools.datasets import detector_dataset
from tools.export import export_onnx, validate_onnx
from tools.eval_detector import evaluate, verdict

BOOTSTRAP_CONF = 0.55

PROVIDERS = {"cpu": "CPUExecutionProvider", "dml": "DmlExecutionProvider",
             "cuda": "CUDAExecutionProvider"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fine-tune the person detector")
    p.add_argument("--data", default="datasets/detector")
    p.add_argument("--epochs", type=int, default=100)
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--model", default="yolo11n.pt",
                   help="starting point; yolo11n for the CPU target")
    p.add_argument("--project", default="runs/detector")
    p.add_argument("--name", default="exp")
    p.add_argument("--device", default=None,
                   help="torch device; default lets ultralytics pick (cuda if present)")
    return p


def bootstrap_labels(unlabelled_dir: Path, out_dataset: Path, conf: float = BOOTSTRAP_CONF) -> int:
    """Run a pretrained person model over unlabelled footage and write YOLO labels.
    These are a STARTING POINT for human correction, not ground truth (spec §12)."""
    src = Path(unlabelled_dir)
    out = Path(out_dataset)
    imgs = sorted(src.rglob("*.jpg")) + sorted(src.rglob("*.png"))
    if not imgs:
        return 0
    from ultralytics import YOLO
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)
    model = YOLO("yolo11n.pt")
    written = 0
    for img_path in imgs:
        res = model.predict(str(img_path), classes=[0], conf=conf, verbose=False)
        if not res:
            continue
        lines = ["0 " + " ".join(f"{v:.6f}" for v in b)
                 for b in res[0].boxes.xywhn.tolist()]
        if not lines:
            continue
        (out / "images" / img_path.name).write_bytes(img_path.read_bytes())
        (out / "labels" / (img_path.stem + ".txt")).write_text("\n".join(lines) + "\n")
        written += 1
    if written:
        ds = detector_dataset(out)
        ds.yaml_path().write_text(
            f"path: {out}\ntrain: images\nval: images\nnc: 1\nnames: ['person']\n")
    return written


def train(dataset_root: Path, epochs: int = 100, imgsz: int = 640,
          model: str = "yolo11n.pt", project: str = "runs/detector",
          name: str = "exp", device: str | None = None) -> Path:
    from ultralytics import YOLO
    ds = detector_dataset(dataset_root)
    m = YOLO(model)
    m.train(data=str(ds.yaml_path()), epochs=epochs, imgsz=imgsz, device=device,
            project=project, name=name)
    best = Path(project) / name / "weights" / "best.pt"
    if not best.exists():
        raise RuntimeError("training produced no best.pt")
    return best


def main() -> None:
    a = build_parser().parse_args()
    best = train(a.data, a.epochs, a.imgsz, a.model, a.project, a.name, a.device)
    print("trained", best)
    metrics = evaluate(best, a.data, a.imgsz)
    print(verdict(metrics))
    for variant, provider in PROVIDERS.items():
        out = Path("models/candidate") / f"detector_{variant}.onnx"
        export_onnx(best, out, a.imgsz)
        print(variant, validate_onnx(out, provider, a.imgsz))


if __name__ == "__main__":
    main()
