from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


TurnAction = Literal["answer", "confirm_transcript"]


@dataclass(frozen=True)
class TurnDecision:
    action: TurnAction
    reason: str | None = None
    response_text: str | None = None


class TurnManager:
    def __init__(self, *, low_confidence_threshold: float) -> None:
        self.low_confidence_threshold = low_confidence_threshold

    def decide_transcript(self, *, text: str, confidence: float | None) -> TurnDecision:
        if confidence is None or confidence >= self.low_confidence_threshold:
            return TurnDecision(action="answer")

        normalized = " ".join(text.strip().split())
        quoted = f"“{normalized[:80]}”" if normalized else "刚才那句话"
        return TurnDecision(
            action="confirm_transcript",
            reason="asr.low_confidence",
            response_text=(
                f"我刚才可能没有听清楚，您是说{quoted}吗？"
                "麻烦您确认或再说一遍，我再继续帮您处理。"
            ),
        )
