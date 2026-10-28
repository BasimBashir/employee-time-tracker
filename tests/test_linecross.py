import pytest
from service.linecross import side_of, cross_direction

LINE = [(0.0, 270.0), (960.0, 270.0)]      # horizontal, pointing +x

@pytest.mark.parametrize("point,expected", [
    ((480.0, 100.0),  1),    # above (smaller y) -> positive side
    ((480.0, 500.0), -1),    # below  -> negative side
    ((480.0, 270.0),  0),    # exactly on the line
    ((-50.0,  50.0),  1),    # far from the segment, still above
])
def test_side_of(point, expected):
    assert side_of(point, LINE) == expected

def test_polyline_uses_nearest_segment():
    poly = [(0.0, 0.0), (500.0, 0.0), (500.0, 540.0)]
    assert side_of((100.0, 50.0), poly) == -1    # nearest = first segment, below it
    assert side_of((450.0, 400.0), poly) == -1   # nearest = vertical segment, left of it

@pytest.mark.parametrize("prev,new,expected", [
    ( 1, -1, "OUT"),   # positive -> negative   (direction_out = POS_TO_NEG)
    (-1,  1, "IN"),
    ( 1,  1, None),    # no crossing
    (-1, -1, None),
    ( 0, -1, "OUT"),   # stepping off the line onto the OUT side
    ( 0,  1, "IN"),
])
def test_cross_direction_pos_to_neg(prev, new, expected):
    assert cross_direction(prev, new, "POS_TO_NEG") == expected

def test_cross_direction_is_inverted_for_neg_to_pos():
    assert cross_direction(1, -1, "NEG_TO_POS") == "IN"
    assert cross_direction(-1, 1, "NEG_TO_POS") == "OUT"
