from tools.eval_detector import verdict

def test_verdict_promotes_above_target():
    m = {"mAP50": 0.91, "mAP50-95": 0.62, "per_class": {"person": {"mAP50": 0.91}}}
    assert "PROMOTE" in verdict(m).upper()

def test_verdict_blocks_below_target():
    m = {"mAP50": 0.61, "mAP50-95": 0.31, "per_class": {"person": {"mAP50": 0.61}}}
    assert "KEEP" in verdict(m).upper() or "NOT" in verdict(m).upper()

def test_verdict_blocks_when_person_class_weak():
    m = {"mAP50": 0.95, "mAP50-95": 0.70,
         "per_class": {"person": {"mAP50": 0.55}, "dog": {"mAP50": 1.0}}}
    # overall looks fine but the class we actually care about is weak
    assert "PROMOTE" not in verdict(m).upper()
