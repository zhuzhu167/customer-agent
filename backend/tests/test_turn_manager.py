from __future__ import annotations

from backend.app.runtime.turn_manager import TurnManager


def test_turn_manager_allows_answer_when_confidence_is_missing() -> None:
    decision = TurnManager(low_confidence_threshold=0.72).decide_transcript(
        text="我想了解办理资料",
        confidence=None,
    )

    assert decision.action == "answer"


def test_turn_manager_confirms_low_confidence_transcript() -> None:
    decision = TurnManager(low_confidence_threshold=0.72).decide_transcript(
        text="办业务资料",
        confidence=0.41,
    )

    assert decision.action == "confirm_transcript"
    assert decision.reason == "asr.low_confidence"
    assert "办业务资料" in (decision.response_text or "")
    assert "确认" in (decision.response_text or "")
