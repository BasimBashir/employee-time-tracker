from unittest.mock import patch
from service.device import select_provider, model_variant

@patch("service.device.ort.get_available_providers", return_value=["CPUExecutionProvider"])
@patch("service.device.shutil.which", return_value=None)
def test_falls_back_to_cpu(_w, _o):
    prov, _ = select_provider()
    assert prov == "CPUExecutionProvider"
    assert model_variant(prov) == {"input_size": 640, "precision": "fp32"}

@patch("service.device.ort.get_available_providers",
       return_value=["CUDAExecutionProvider", "CPUExecutionProvider"])
@patch("service.device._cuda_available", return_value=True)
def test_prefers_cuda_when_present(_c, _o):
    prov, _ = select_provider()
    assert prov == "CUDAExecutionProvider"
    assert model_variant(prov)["precision"] == "fp16"

@patch("service.device.ort.get_available_providers", return_value=["DmlExecutionProvider",
                                                                   "CPUExecutionProvider"])
@patch("service.device._cuda_available", return_value=False)
def test_dml_on_igpu_without_cuda(_c, _o):
    prov, _ = select_provider()
    assert prov == "DmlExecutionProvider"
