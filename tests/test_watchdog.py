import time
from service.config import Config
from service.watchdog import Watchdog


def test_sample_reports_fields_and_flag():
    cfg = Config(); cfg.watchdog_rss_ceiling_mb = 1  # force over-ceiling
    s = Watchdog(cfg, restart_callback=lambda: None).sample()
    assert "rss_mb" in s and "threads" in s
    assert s["over_ceiling"] is True


def test_sample_under_ceiling_is_false():
    cfg = Config(); cfg.watchdog_rss_ceiling_mb = 10_000_000
    s = Watchdog(cfg, restart_callback=lambda: None).sample()
    assert s["over_ceiling"] is False


def test_stop_is_clean():
    w = Watchdog(Config(), restart_callback=lambda: None)
    w.start(); time.sleep(0.2); w.stop()
    assert w.thread.is_alive() is False


def test_restart_callback_fires_once_over_ceiling():
    """Over the ceiling the callback fires exactly once — not on every check."""
    calls = []
    cfg = Config(); cfg.watchdog_rss_ceiling_mb = 1
    w = Watchdog(cfg, restart_callback=lambda: calls.append(1))
    for _ in range(5):
        w.check()
    assert calls == [1]
    assert w.restart_requested is True
