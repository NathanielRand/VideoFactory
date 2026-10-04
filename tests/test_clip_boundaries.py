"""Clip edges should hold a COMPLETE moment: the setup in, the payoff finished.

Three things used to cut moments short: the start snap only moved inward
(dropping setup), signal peaks grew forward-only (starting after the thing
they react to), and nothing let the model place edges once it had a shortlist.
"""

import numpy as np

from analysis.fusion import _signal_peak_windows
from analysis.highlights import _fit_to_segments, refine_boundaries
from core.models import ClipCandidate, Segment
from llm.base import LLMBackend

MIN, MAX = 10.0, 60.0


class Canned(LLMBackend):
    def __init__(self, reply: str):
        self.reply = reply

    def generate(self, prompt, *, json_mode=False):
        return self.reply

    @property
    def name(self):
        return "canned"


def talk(*spans):
    return [Segment(start=a, end=b, text=t) for a, b, t in spans]


def test_start_reaches_back_to_the_sentence_it_belongs_to():
    segs = talk(
        (0.0, 4.0, "Somebody asked me why I quit"),
        (4.0, 9.0, "and honestly it was the money."),   # sentence ends here
        (9.0, 14.0, "I could not believe the offer"),   # candidate starts mid-sentence
        (14.0, 22.0, "they wanted me to work for free."),
        (22.0, 30.0, "So I walked."),
    )
    out = _fit_to_segments(ClipCandidate(start=9.0, end=30.0, score=80), segs, MIN, MAX)
    assert out.start == 9.0  # nearest sentence start within reach is 9.0 itself
    segs[1].text = "and honestly it was the money"        # no full stop: 9.0 is mid-sentence
    segs[0].text = "Somebody asked me why I quit."
    out = _fit_to_segments(ClipCandidate(start=9.0, end=30.0, score=80), segs, MIN, MAX)
    assert out.start == 4.0  # reached back 5s to open on a sentence start


def test_lead_in_grows_backwards_for_a_signal_peak():
    segs = [Segment(start=float(i * 4), end=float(i * 4 + 4), text=f"line {i}.") for i in range(20)]
    peak = ClipCandidate(start=40.0, end=48.0, score=60, source="signal")
    plain = _fit_to_segments(ClipCandidate(start=40.0, end=48.0, score=60), segs, MIN, MAX, target_duration=28.0)
    led = _fit_to_segments(peak, segs, MIN, MAX, target_duration=28.0, lead_in=True)
    assert led.start < plain.start
    assert led.start < 40.0 <= led.end


def test_signal_windows_are_padded_mostly_before_the_peak():
    signal = np.zeros(200, dtype=np.float32)
    signal[100] = 1.0
    segs = [Segment(start=0.0, end=200.0, text="x")]
    (start, end), *_ = _signal_peak_windows(signal, segs, 99, MIN, MAX, [])
    assert 100 - start > end - 101


def test_pre_fit_span_is_recorded():
    segs = talk((0.0, 6.0, "One."), (6.0, 13.0, "Two."), (13.0, 21.0, "Three."))
    c = _fit_to_segments(ClipCandidate(start=7.0, end=12.0, score=70), segs, MIN, MAX)
    assert c.proposed == (7.0, 12.0)


def _story():
    return talk(
        (0.0, 5.0, "Earlier tangent about lunch."),
        (5.0, 10.0, "So what did he actually say to you?"),
        (10.0, 20.0, "He said the whole thing was a scam."),
        (20.0, 30.0, "And I lost my mind."),
        (30.0, 40.0, "Anyway, different topic."),
    )


def test_refine_moves_the_edges_onto_the_models_indexes():
    segs = _story()
    c = ClipCandidate(start=10.0, end=20.0, score=80)
    moved = refine_boundaries(c, segs, Canned('{"start_index": 1, "end_index": 3}'), MIN, MAX)
    assert moved and (c.start, c.end) == (5.0, 30.0)


def test_refine_ignores_unusable_answers():
    segs = _story()
    for reply in ("not json", '{"start_index": 3, "end_index": 1}', '{"start_index": 0, "end_index": 99}',
                  '{"start_index": 2, "end_index": 2}'):  # 10s ok; but unchanged -> no move
        c = ClipCandidate(start=10.0, end=20.0, score=80)
        assert not refine_boundaries(c, segs, Canned(reply), MIN, MAX)
        assert (c.start, c.end) == (10.0, 20.0)


def test_refine_never_breaks_the_duration_range():
    segs = _story()
    c = ClipCandidate(start=10.0, end=20.0, score=80)
    assert not refine_boundaries(c, segs, Canned('{"start_index": 0, "end_index": 4}'), MIN, 30.0)
    assert (c.start, c.end) == (10.0, 20.0)
