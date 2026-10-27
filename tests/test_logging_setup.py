import json
from pathlib import Path
from service.logging_setup import service_logger, JsonFormatter


def test_json_lines_format(tmp_path):
    log = service_logger("test_jsonlines", tmp_path)
    log.info("hello %s", "world")
    lines = (tmp_path / "service.log").read_text().strip().splitlines()
    rec = json.loads(lines[0])
    assert rec["msg"] == "hello world"
    assert rec["logger"] == "test_jsonlines"
    assert rec["level"] == "INFO"
    assert "ts" in rec and "thread" in rec


def test_formatter_does_not_raise_on_nondict_detail():
    import logging
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "msg %s", ("a",), None)
    out = json.loads(JsonFormatter().format(rec))
    assert out["msg"] == "msg a"


def test_same_name_does_not_duplicate_handlers(tmp_path):
    a = service_logger("dup_name", tmp_path)
    b = service_logger("dup_name", tmp_path)
    assert len(a.handlers) == len(b.handlers)
