import numpy as np
import onnxruntime as ort
from tools.export import provider_variants, validate_onnx, active_variant_for

def test_variants_cover_three_providers():
    v = provider_variants()
    assert set(v) == {"cpu", "dml", "cuda"}
    assert v["cpu"]["precision"] == "fp32"

def test_active_variant_maps_from_provider():
    assert active_variant_for("CPUExecutionProvider") == "cpu"
    assert active_variant_for("DmlExecutionProvider") == "dml"
    assert active_variant_for("CUDAExecutionProvider") == "cuda"

def test_validate_onnx_on_a_tiny_builtin(tmp_path):
    # build a 1-layer identity-ish ONNX without any model zoo dependency
    from onnx import helper, TensorProto
    x = helper.make_tensor_value_info("in", TensorProto.FLOAT, [1, 3, 8, 8])
    y = helper.make_tensor_value_info("out", TensorProto.FLOAT, [1, 3, 8, 8])
    node = helper.make_node("Relu", ["in"], ["out"])
    g = helper.make_graph([node], "g", [x], [y])
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 13)])
    p = tmp_path / "tiny.onnx"
    import onnx
    onnx.save(m, str(p))
    info = validate_onnx(p, "CPUExecutionProvider", imgsz=8)
    assert info["ok"] is True and info["output_shape"] == [1, 3, 8, 8]
