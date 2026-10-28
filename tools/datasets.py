"""The only module that touches dataset files. Trainers go through this."""
from dataclasses import dataclass
from pathlib import Path
import cv2

# A "hard" pose is a dark or oblique crop. Brightness is a cheap stand-in; replace
# with a face-angle estimate when the enrollment UI records pose metadata.
HARD_BRIGHTNESS = 110


def _images(dirpath: Path) -> list[Path]:
    exts = ("*.jpg", "*.jpeg", "*.png", "*.bmp")
    out: list[Path] = []
    for e in exts:
        out.extend(sorted(dirpath.rglob(e)))
    return out


@dataclass
class DetectorDataset:
    root: Path
    @property
    def images(self) -> Path: return self.root / "images"
    @property
    def labels(self) -> Path: return self.root / "labels"

    def image_paths(self) -> list[Path]:
        return _images(self.images)

    def label_paths(self) -> list[Path]:
        return sorted(self.labels.rglob("*.txt"))

    def class_counts(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for lp in self.label_paths():
            for line in lp.read_text().splitlines():
                if not line.strip():
                    continue
                cls = int(line.split()[0])
                counts[cls] = counts.get(cls, 0) + 1
        return counts

    def yaml_path(self) -> Path:
        return self.root / "data.yaml"


@dataclass
class ReidDataset:
    root: Path
    def identities(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir()
                      if p.is_dir() and p.name.isdigit())
    def train_split(self) -> list[Path]:
        return [p for i in self.identities()
                for p in _images(self.root / i)]
    def query_split(self) -> list[Path]:
        return self.train_split()[:1] if self.identities() else []
    def identity_of(self, path: Path) -> str:
        return path.parent.name


@dataclass
class FaceDataset:
    root: Path
    def identities(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())
    def images_for(self, identity: str) -> list[Path]:
        return _images(self.root / identity)


def detector_dataset(root: Path) -> DetectorDataset:
    return DetectorDataset(Path(root))


def reid_dataset(root: Path) -> ReidDataset:
    return ReidDataset(Path(root))


def face_dataset(root: Path) -> FaceDataset:
    return FaceDataset(Path(root))


def _brightness(path: Path) -> float:
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    return float(img.mean()) if img is not None else 0.0


def enrollment_pose_count(dirpath: Path) -> int:
    return len(_images(Path(dirpath)))


def hard_pose_ratio(dirpath: Path) -> float:
    """Share of crops that look like hard poses. Used to keep enrollment-quality
    images out of training sets."""
    imgs = _images(Path(dirpath))
    if not imgs:
        return 0.0
    hard = sum(1 for p in imgs if _brightness(p) < HARD_BRIGHTNESS)
    return hard / len(imgs)


def write_split(paths: list[Path], out: Path) -> Path:
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(str(p) for p in paths) + "\n")
    return out
