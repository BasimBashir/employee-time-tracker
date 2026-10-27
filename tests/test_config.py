import json
from pathlib import Path
from service.config import load_config

def test_defaults_and_override(tmp_path):
    cfgpath = tmp_path / "config.json"
    cfgpath.write_text(json.dumps({"port": 9090}))
    cfg = load_config(cfgpath)
    assert cfg.port == 9090
    assert cfg.data_root == Path(r"C:\ProgramData\EmployeeTracker")

def test_data_root_creates_dirs(tmp_path):
    cfgpath = tmp_path / "config.json"
    cfg = load_config(cfgpath)
    cfg.data_root = tmp_path / "data"
    cfg.ensure_dirs()
    assert (tmp_path / "data" / "logs").is_dir()
    assert (tmp_path / "data" / "clips").is_dir()
    assert (tmp_path / "data" / "models").is_dir()
    assert (tmp_path / "data" / "tracker.db").name == "tracker.db"
