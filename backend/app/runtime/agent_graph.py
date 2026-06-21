from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from backend.app.providers.base import LLMProvider
from backend.app.runtime.response_planner import ResponsePlanner


class AgentState(TypedDict, total=False):
    session_id: str
    user_text: str
    history: list[dict[str, Any]]
    candidate_reply: str
    response_text: str


class LangGraphAgentRuntime:
    def __init__(self, llm_provider: LLMProvider, response_planner: ResponsePlanner | None = None) -> None:
        self.llm_provider = llm_provider
        self.response_planner = response_planner or ResponsePlanner()
        self.graph = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(AgentState)

        async def call_llm(state: AgentState) -> AgentState:
            result = await self.llm_provider.generate_reply(
                session_id=state["session_id"],
                user_text=state["user_text"],
                history=state.get("history", []),
            )
            return {"candidate_reply": result.text}

        def plan_response(state: AgentState) -> AgentState:
            return {"response_text": self.response_planner.plan(state.get("candidate_reply", ""))}

        workflow.add_node("llm", call_llm)
        workflow.add_node("response_planner", plan_response)
        workflow.set_entry_point("llm")
        workflow.add_edge("llm", "response_planner")
        workflow.add_edge("response_planner", END)
        return workflow.compile()

    async def handle_user_turn(
        self,
        *,
        session_id: str,
        user_text: str,
        history: list[dict[str, Any]] | None = None,
    ) -> str:
        state = await self.graph.ainvoke(
            {"session_id": session_id, "user_text": user_text, "history": history or []}
        )
        return state["response_text"]
