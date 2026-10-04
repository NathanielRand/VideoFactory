"""Apple GPU (Metal) selection, driven by a fake torch so it runs anywhere.

Real Metal behaviour can only be checked on a Mac; these pin the decisions the
code makes around it: who gets which device, and that nothing changes on
Windows or Linux.
"""

import os
import sys
import types

import pytest

from core import gpu


def _torch(cuda_available=False, mps_available=False, mps_built=True):
    t = types.SimpleNamespace()
    t.cuda = types.SimpleNamespace(
        is_available=lambda: cuda_available,
        get_device_name=lambda _i: "Fake GPU",
        get_device_capability=lambda _i: (8, 6),
        get_arch_list=lambda: ["sm_86"],
    )
    t.backends = types.SimpleNamespace(
        mps=types.SimpleNamespace(is_available=lambda: mps_available, is_built=lambda: mps_built)
    )
    return t


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(gpu, "_DEVICE", None)
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK", raising=False)

    def install(**kw):
        monkeypatch.setitem(sys.modules, "torch", _torch(**kw))

    return install


def test_apple_silicon_gets_metal(mac):
    mac(mps_available=True)
    assert gpu.accelerator() == ("mps", "Apple GPU (Metal)")
    assert gpu.torch_device() == "mps"


def test_metal_turns_on_the_cpu_fallback_for_missing_ops(mac, monkeypatch):
    mac(mps_available=True)
    gpu.accelerator()
    assert os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] == "1"


def test_models_not_validated_on_metal_stay_on_the_cpu(mac):
    mac(mps_available=True)
    assert gpu.torch_device(allow_mps=False) == "cpu"
    assert gpu.torch_device() == "mps"  # and the answer for others is unchanged


def test_a_mac_without_metal_says_so_instead_of_blaming_cuda(mac):
    mac(mps_available=False, mps_built=True)
    device, reason = gpu.accelerator()
    assert device == "cpu"
    assert "CUDA" not in reason and "Metal" in reason


def test_a_build_without_metal_is_named(mac):
    mac(mps_available=False, mps_built=False)
    assert "no Metal support" in gpu.accelerator()[1]


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_other_platforms_never_pick_metal(monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(gpu, "_DEVICE", None)
    # Even a torch that claims Metal is ignored off a Mac.
    monkeypatch.setitem(sys.modules, "torch", _torch(mps_available=True))
    assert gpu.accelerator()[0] == "cpu"
    assert gpu.mps_usable() == (False, "not a Mac")


def test_cuda_still_wins_when_present(monkeypatch):
    monkeypatch.setattr(gpu, "_DEVICE", None)
    monkeypatch.setitem(sys.modules, "torch", _torch(cuda_available=True, mps_available=True))
    assert gpu.accelerator()[0] == "cuda"
