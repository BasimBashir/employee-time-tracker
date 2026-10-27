"""Configuration and data-root paths.

This module is the ONLY place that builds a path under the data root. Every other
module takes these values from a Config instance so the layout stays in one place.
"""
import json
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_DATA_ROOT = Path(r"C:\ProgramData\EmployeeTracker")
APP_ROOT = Path(r"C:\Program Files\EmployeeTracker")


@dataclass
class Config:
    data_root: Path = DEFAULT_DATA_ROOT
    port: int = 8080
    watchdog_rss_ceiling_mb: int = 1500
    clip_retention_days: int = 90
    disk_floor_gb: int = 10
    analysis_target_latency_ms: int = 120
    confirm_threshold: float = 0.45
    uncertain_threshold: float = 0.35
    _extra: dict = field(default_factory=dict)

    @property
    def app_root(self) -> Path:
        return APP_ROOT

    @property
    def logs_dir(self) -> Path:
        return self.data_root / "logs"

    @property
    def clips_dir(self) -> Path:
        return self.data_root / "clips"

    @property
    def models_dir(self) -> Path:
        return self.data_root / "models"

    @property
    def data_dir(self) -> Path:
        return self.data_root / "data"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tracker.db"

    @property
    def config_path(self) -> Path:
        return self.data_root / "config" / "config.json"

    @classmethod
    def known_keys(cls) -> set[str]:
        return {f.name for f in fields(cls) if not f.name.startswith("_")}

    def ensure_dirs(self) -> None:
        for d in (self.logs_dir, self.clips_dir, self.models_dir,
                  self.data_dir, self.config_path.parent):
            d.mkdir(parents=True, exist_ok=True)

    def get(self, key, default=None):
        return self._extra.get(key, default)

    def save(self) -> None:
        self.ensure_dirs()
        payload = {
            "data_root": str(self.data_root),
            "port": self.port,
            "watchdog_rss_ceiling_mb": self.watchdog_rss_ceiling_mb,
            "clip_retention_days": self.clip_retention_days,
            "disk_floor_gb": self.disk_floor_gb,
            "analysis_target_latency_ms": self.analysis_target_latency_ms,
            "confirm_threshold": self.confirm_threshold,
            "uncertain_threshold": self.uncertain_threshold,
            **self._extra,
        }
        self.config_path.write_text(json.dumps(payload, indent=2))


def load_config(path: Path | None = None) -> Config:
    """Load config.json; unknown keys are preserved in `_extra` so the installer and
    later phases can add settings without a migration."""
    cfg = Config()
    if path is None:
        path = cfg.config_path
    path = Path(path)
    if not path.exists():
        return cfg
    data = json.loads(path.read_text())
    known = Config.known_keys()
    for key, value in data.items():
        if key in known:
            setattr(cfg, key, Path(value) if key == "data_root" else value)
        else:
            cfg._extra[key] = value
    return cfg
