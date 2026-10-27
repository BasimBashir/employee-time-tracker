"""Device and decoder probing — the only place that asks about GPUs/decoders.

Provider choice happens once at startup: CUDA -> Dml -> CPU. This machine class
(Windows 11, Intel iGPU) is expected to land on CPU or Dml; CUDA is honoured
only when both ONNX Runtime offers it and torch can actually see a card.
"""
import shutil
import subprocess

import onnxruntime as ort

ORDER = ["CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"]


def _cuda_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:                     # torch is optional; no torch -> no CUDA
        return False


def select_provider() -> tuple[str, str]:
    available = ort.get_available_providers()
    if "CUDAExecutionProvider" in available and _cuda_available():
        return "CUDAExecutionProvider", "nvidia dgpu detected"
    if "DmlExecutionProvider" in available:
        return "DmlExecutionProvider", "directml (amd/intel igpu)"
    return "CPUExecutionProvider", "no gpu provider available"


def model_variant(provider: str) -> dict:
    if provider == "CPUExecutionProvider":
        return {"input_size": 640, "precision": "fp32"}
    return {"input_size": 640, "precision": "fp16"}


def pick_decoder() -> str:
    """d3d11h264dec if the plugin is on the PATH-registered GST_PLUGIN_PATH, else avdec_h264."""
    gst = shutil.which("gst-inspect-1.0")
    if gst:
        try:
            out = subprocess.run([gst, "d3d11h264dec"],
                                 capture_output=True, text=True).stdout
            if "d3d11h264dec" in out:
                return "d3d11h264dec"
        except Exception:
            pass
    return "avdec_h264"
