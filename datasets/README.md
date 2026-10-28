# Datasets

Three directories, one per model. **Keep them separate.**

- `detector/` — YOLO format (`images/`, `labels/`, `data.yaml`).
- `reid/` — one numeric directory per identity (`0001/`, `0002/`, …).
- `faces/` — one directory per identity, named as the employee is named.

Enrollment captures (clean frontal poses from the in-app flow) belong in **none** of
these. Training needs the hard cases: sideways, masked, backlit, partial, low light.
See spec §12.
