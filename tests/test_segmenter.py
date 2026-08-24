"""智能断句测试：定稿断句、超时兜底、partial 不产生句子。"""

from __future__ import annotations

from subtitles.segmenter import PartialUpdate, Segmenter, SentenceDone


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_segmenter(max_seconds: float = 15.0) -> tuple[Segmenter, FakeClock]:
    clock = FakeClock()
    return Segmenter(max_sentence_seconds=max_seconds, clock=clock), clock


def test_sentence_event_finalizes_line():
    seg, _ = make_segmenter()
    seg.on_partial("hello")
    events = seg.on_sentence("hello world.")
    assert events == [SentenceDone(text="hello world.")]
    # 定稿后当前行清空，下一条 partial 另起一行
    events = seg.on_partial("next")
    assert events == [PartialUpdate(text="next")]
    assert seg.current_text == "next"


def test_partial_only_produces_no_sentence():
    seg, clock = make_segmenter()
    events = seg.on_partial("he")
    assert events == [PartialUpdate(text="he")]
    clock.advance(1.0)
    events = seg.on_partial("hello")
    assert events == [PartialUpdate(text="hello")]
    assert seg.current_text == "hello"


def test_timeout_forces_sentence_break():
    seg, clock = make_segmenter(max_seconds=15.0)
    seg.on_partial("第一句话")
    clock.advance(16.0)
    events = seg.on_partial("第二句话")
    assert events == [
        SentenceDone(text="第一句话", forced=True),
        PartialUpdate(text="第二句话"),
    ]
    assert seg.current_text == "第二句话"


def test_timeout_edge_not_triggered_before_limit():
    seg, clock = make_segmenter(max_seconds=15.0)
    seg.on_partial("还没超时")
    clock.advance(14.9)
    events = seg.on_partial("还没超时继续")
    assert events == [PartialUpdate(text="还没超时继续")]


def test_empty_partial_ignored():
    seg, _ = make_segmenter()
    assert seg.on_partial("") == []
    assert seg.on_partial("   ") == []
    assert seg.current_text == ""


def test_empty_sentence_ignored():
    seg, _ = make_segmenter()
    assert seg.on_sentence("") == []


def test_flush_finalizes_pending_line():
    seg, _ = make_segmenter()
    assert seg.flush() == []
    seg.on_partial("未说完的一句")
    assert seg.flush() == [SentenceDone(text="未说完的一句", forced=True)]
    assert seg.flush() == []
