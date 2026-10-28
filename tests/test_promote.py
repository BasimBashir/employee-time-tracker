import json
from pathlib import Path
from tools.promote import promote, REQUIRED_REPORT_KEYS, load_report

def _candidate(root: Path, kind="detector", variant="cpu"):
    # candidate_dir is the leaf directory that holds the variant files
    p = root / f"{kind}_{variant}.onnx"
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
