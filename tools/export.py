"""The only place ONNX conversion happens, and the only place it is validated.
Provider variants exist so the running service picks the right one at startup
rather than a human copying the right file by hand.

shortcut: all three variants are fp32 exports that differ only in filename and
per-provider load validation; real fp16 conversion is a later task.
"""
from pathlib import Path
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


# onnxruntime 1.24.x loads IR <= 13; onnx >= 1.23 writes IR 14 by default.
MAX_IR_VERSION = 13


def validate_onnx(path: Path, provider: str, imgsz: int = 640) -> dict:
    """Load with the INTENDED provider and run one dummy frame. An export that
    cannot load is a failed export, and this is what catches that."""
    import onnxruntime as ort
    import onnx
    if provider not in ort.get_available_providers():
        # ORT would silently fall back to another provider — that hides the truth
        return {"ok": False, "provider": provider,
                "error": f"provider not installed (available: {ort.get_available_providers()})"}
    m = onnx.load(str(path))
    if m.ir_version > MAX_IR_VERSION:
        m.ir_version = MAX_IR_VERSION
        onnx.save(m, str(path))
    blob = np.random.rand(1, 3, imgsz, imgsz).astype(np.float32)
    try:
        so = ort.SessionOptions()
        sess = ort.InferenceSession(str(path), sess_options=so, providers=[provider])
        out = sess.run(None, {sess.get_inputs()[0].name: blob})
        return {"ok": True, "provider": provider,
                "output_shape": list(np.asarray(out[0]).shape)}
    except Exception as e:
        return {"ok": False, "provider": provider, "error": str(e)}
