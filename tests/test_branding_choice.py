"""Branding is chosen per video, with three answers.

A profile, explicitly none, or neither key — the creator's default. "None"
has to survive as its own answer: without it a video by a creator who has a
default logo could not be made unbranded at all.
"""

import pytest

api = pytest.importorskip("server.api")


def test_a_profile_is_kept():
    payload = api._process_options(api.JobIn(url="u", watermark_profile_id=3))
    assert payload["watermark_profile_id"] == 3
    assert "no_watermark" not in payload


def test_no_branding_is_kept():
    payload = api._process_options(api.JobIn(url="u", no_watermark=True))
    assert payload["no_watermark"] is True
    assert "watermark_profile_id" not in payload


def test_neither_means_the_creator_default():
    payload = api._process_options(api.JobIn(url="u"))
    assert "watermark_profile_id" not in payload
    assert "no_watermark" not in payload


def test_a_patch_to_a_profile_drops_no_branding():
    payload = api._process_options(api.JobPatch(watermark_profile_id=5), {"no_watermark": True})
    assert payload == {"watermark_profile_id": 5}


def test_a_patch_to_no_branding_drops_the_profile():
    payload = api._process_options(api.JobPatch(no_watermark=True), {"watermark_profile_id": 5})
    assert payload == {"no_watermark": True}


def test_clearing_both_goes_back_to_the_creator_default():
    before = {"watermark_profile_id": 5}
    patch = api.JobPatch(clear=["watermark_profile_id", "no_watermark"])
    assert api._process_options(patch, before) == {}
