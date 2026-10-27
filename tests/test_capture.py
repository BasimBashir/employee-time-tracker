import pytest
from service.capture import RtspGstSource, build_pipeline_string

def test_pipeline_string_defaults():
    s = build_pipeline_string("rtsp://cam/1", "avdec_h264", 200)
    assert "rtspsrc location=rtsp://cam/1" in s
    assert "latency=200" in s
    assert "avdec_h264" in s
    assert "appsink max-buffers=2 drop=true" in s

def test_d3d11_decoder_selected_when_given():
    s = build_pipeline_string("rtsp://cam/1", "d3d11h264dec", 150)
    assert "d3d11h264dec" in s

def test_backoff_caps_at_30s():
    pytest.importorskip("gi")            # needs the GStreamer runtime
    src = RtspGstSource("rtsp://cam/1")
    delays = [src._next_delay() for _ in range(10)]
    # 1 s first, doubling to a 30 s cap; 0-0.5 s jitter on top of each.
    assert 1.0 <= delays[0] <= 1.5
    assert 30.0 <= max(delays) <= 30.5
    src.stop()
