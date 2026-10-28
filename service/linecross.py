"""Pure geometry. No state, no I/O, no time."""

def _nearest_segment_cross(point, line_points) -> float:
    """Cross product against the nearest segment of the polyline.

    Image coordinates (y grows down): for a +x line, smaller y -> positive.
    """
    px, py = point
    best = None
    for i in range(len(line_points) - 1):
        (x0, y0), (x1, y1) = line_points[i], line_points[i + 1]
        dx, dy = x1 - x0, y1 - y0
        cross = dy * (px - x0) - dx * (py - y0)
        seg_len_sq = dx * dx + dy * dy or 1.0
        t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / seg_len_sq))
        cx, cy = x0 + t * dx, y0 + t * dy
        d = (px - cx) ** 2 + (py - cy) ** 2
        if best is None or d < best[0]:
            best = (d, cross)
    return best[1] if best else 0.0


def side_of(point, line_points) -> int:
    if len(line_points) < 2:
        raise ValueError("a zone needs at least two line points")
    cross = _nearest_segment_cross(point, line_points)
    if cross > 0:
        return 1
    if cross < 0:
        return -1
    return 0


def signedness(direction_out: str) -> tuple[int, int]:
    if direction_out == "POS_TO_NEG":
        return (1, -1)
    if direction_out == "NEG_TO_POS":
        return (-1, 1)
    raise ValueError(f"unknown direction_out: {direction_out!r}")


def cross_direction(prev_side: int, new_side: int, direction_out: str) -> str | None:
    """Which way did we cross? 'OUT', 'IN', or None (no crossing).

    A side of 0 (on the line) counts as moving toward whichever side the
    point actually lands on.
    """
    if prev_side == new_side:
        return None
    out_from, out_to = signedness(direction_out)
    if prev_side == 0:
        return "OUT" if new_side == out_to else "IN"
    if new_side == 0:
        return "OUT" if prev_side == out_from else "IN"
    if (prev_side, new_side) == (out_from, out_to):
        return "OUT"
    if (prev_side, new_side) == (out_to, out_from):
        return "IN"
    return None
