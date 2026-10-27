import numpy as np
from service.detector import Detector, Detection

def test_detection_dataclass():
    d = Detection(bbox_xyxy=(10, 10, 50, 90), confidence=0.9, class_id=0)
    assert d.confidence == 0.9

def test_detector_defaults_to_startup_provider(tmp_path, monkeypatch):
    from service import detector as detmod
    monkeypatch.setattr(detmod, "select_provider",
                        lambda: ("CPUExecutionProvider", "test"))
    d = Detector(tmp_path / "nope.onnx")
    assert d.provider == "CPUExecutionProvider" and d.input_size == 640

def test_detector_handles_missing_model(tmp_path):
    det = Detector(tmp_path / "nope.onnx", provider="CPUExecutionProvider", input_size=640)
    frame = np.zeros((540, 960, 3), np.uint8)
    assert det.detect(frame) == []          # no model -> no detections, no crash
