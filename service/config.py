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
    # Generated reports (CSV/XLSX/PDF) live longer than clips — the accountant
    # may want last quarter's file, but not forever.
    report_retention_days: int = 365
    disk_floor_gb: int = 10
    analysis_target_latency_ms: int = 120
    confirm_threshold: float = 0.45
    uncertain_threshold: float = 0.35
    # Daily out-of-floor budget per employee when they have no override of
    # their own. Delivery trips never count against it.
    default_allowed_out_minutes: int = 60
    # WhatsApp notifications (see service/notifier.py and whatsapp_bridge/).
    # Off until the operator pairs the bridge with a dedicated SIM.
    notify_enabled: bool = False
    notify_bridge_url: str = "http://127.0.0.1:8090"
    notify_bridge_key: str = ""
    # The operator's own number; off-window alerts go here (employees are
    # never told on each other). Empty disables the alert.
    notify_owner_phone: str = ""
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
    def media_dir(self) -> Path:
        """Employee media: profile avatars (flat) and the recognition-training
        gallery (one folder per employee)."""
        return self.data_root / "media"

    def employee_media_dir(self, employee_id: int) -> Path:
        """Training captures for one employee, plain files on disk on purpose:
        the recognition pipeline just scans folders; nothing about these files
        lives in the database."""
        return self.media_dir / "employees" / str(employee_id)

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
                  self.data_dir, self.media_dir, self.config_path.parent):
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
            "report_retention_days": self.report_retention_days,
            "disk_floor_gb": self.disk_floor_gb,
            "analysis_target_latency_ms": self.analysis_target_latency_ms,
            "confirm_threshold": self.confirm_threshold,
            "uncertain_threshold": self.uncertain_threshold,
            "default_allowed_out_minutes": self.default_allowed_out_minutes,
            "notify_enabled": self.notify_enabled,
            "notify_bridge_url": self.notify_bridge_url,
            "notify_bridge_key": self.notify_bridge_key,
            "notify_owner_phone": self.notify_owner_phone,
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
