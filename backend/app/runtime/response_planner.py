from __future__ import annotations


FORBIDDEN_PROMISE_TERMS = (
    "一定通过",
    "保证通过",
    "肯定审批",
    "固定价格",
    "法律效力",
)


class ResponsePlanner:
    def plan(self, candidate_text: str) -> str:
        text = " ".join(candidate_text.strip().split())
        if not text:
            return "抱歉，我刚才没有生成有效回答。您可以再说一遍诉求，我继续帮您确认。"

        for term in FORBIDDEN_PROMISE_TERMS:
            text = text.replace(term, "需要进一步确认")

        if "智能客服" not in text and len(text) < 80:
            text = f"{text} 我是智能客服，会尽量先帮您梳理清楚。"
        return text
