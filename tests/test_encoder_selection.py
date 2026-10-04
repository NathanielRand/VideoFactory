"""Which hardware encoder each OS tries, and in what order."""

import sys

import pytest

from video import encoding


@pytest.fixture
def probe(monkeypatch):
    """Pretend only the named encoders work; record which were tried."""
    tried = []

    def install(working):
        def fake(args):
            name = next(n for n, a in encoding._CANDIDATES.items() if a == args)
            tried.append(name)
            return name in working
        monkeypatch.setattr(encoding, "_probe", fake)
        return tried

    return install


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_windows_and_linux_try_nvenc_then_amf_then_qsv(monkeypatch, probe, platform):
    monkeypatch.setattr(sys, "platform", platform)
    tried = probe(working={"qsv"})
    assert encoding._select("auto")[0] == "qsv"
    assert tried == ["nvenc", "amf", "qsv"]


def test_a_mac_only_tries_videotoolbox(monkeypatch, probe):
    monkeypatch.setattr(sys, "platform", "darwin")
    tried = probe(working={"videotoolbox"})
    name, args = encoding._select("auto")
    assert name == "videotoolbox" and "h264_videotoolbox" in args
    assert tried == ["videotoolbox"]


def test_a_mac_whose_probe_fails_falls_back_to_the_cpu(monkeypatch, probe):
    monkeypatch.setattr(sys, "platform", "darwin")
    probe(working=set())
    assert encoding._select("auto") == ("cpu", encoding.CPU_ARGS)


def test_videotoolbox_never_falls_back_to_software_silently():
    args = encoding._CANDIDATES["videotoolbox"]
    assert args[args.index("-allow_sw") + 1] == "0"


def test_every_hardware_encoder_still_ends_in_the_playback_args():
    for name, args in encoding._CANDIDATES.items():
        assert args[-4:] == ["-pix_fmt", "yuv420p", "-g", "60"], name


def test_forcing_an_encoder_that_fails_its_probe_uses_the_cpu(monkeypatch, probe):
    probe(working=set())
    assert encoding._select("videotoolbox") == ("cpu", encoding.CPU_ARGS)
