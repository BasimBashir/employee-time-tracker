import numpy as np
from service.framesource import FrameSource, SyntheticSource

def test_source_is_abstract():
    try:
        FrameSource()
    except TypeError:
        return
    raise AssertionError("FrameSource must be abstract")

def test_synthetic_returns_none_at_end():
    src = SyntheticSource(frame_count=5, width=320, height=240)
    got = [src.read() for _ in range(7)]
    assert sum(g is not None for g in got) == 5
    assert got[-1] is None
    src.stop()

def test_synthetic_frame_shape_and_motion():
    src = SyntheticSource(frame_count=3, width=320, height=240)
    f0 = src.read(); f1 = src.read()
    assert f0.image.shape == (240, 320, 3) and f0.image.dtype == np.uint8
    assert not np.array_equal(f0.image, f1.image)   # rect moved
    src.stop()

def test_file_source_reads_synth_clip(tmp_path):
    from tools.synth_clip import write_clip
    from service.framesource import FileSource
    clip = tmp_path / "clip.mp4"
    write_clip(str(clip), seconds=1, fps=10, width=320, height=240)
    src = FileSource(clip)
    frames = []
    while True:
        f = src.read()
        if f is None:
            break
        frames.append(f)
    src.stop()
    assert len(frames) >= 9          # ~1 s at 10 fps; encoder may round
    assert frames[0].image.shape == (240, 320, 3)
