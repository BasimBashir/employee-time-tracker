"""Generate a synthetic MP4 for dev/demo without RTSP."""
import argparse, cv2, numpy as np

def write_clip(path, seconds=10, fps=15, width=640, height=360):
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    for i in range(int(seconds * fps)):
        f = np.zeros((height, width, 3), np.uint8)
        x = int((i / (seconds * fps)) * (width - 60))
        f[140:220, x:x + 60] = (60, 180, 60)
        w.write(f)
    w.release()

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="tests/fixtures/synth.mp4")
    a = p.parse_args()
    write_clip(a.out)
    print("wrote", a.out)
