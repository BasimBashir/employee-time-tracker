"""The only writer to models/active/. Refuses without a passing eval report."""
import json, shutil
from pathlib import Path

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
