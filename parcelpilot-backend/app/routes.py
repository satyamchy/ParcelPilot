"""
The only two endpoints that matter for the assessment:

  POST /chat          — send a message, get a reply (+ tool trace, and a
                         pending_action if the agent wants to escalate)
  POST /chat/confirm   — confirm or cancel a pending_action

Plus /health for sanity checking a deployment.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.graph import get_graph
from app.access_control import Role

router = APIRouter()


# ---- request/response models ----------------------------------------------

class UserContextIn(BaseModel):
    user_id: str
    role: str                      # customer | internal_support | internal_admin
    account_id: str | None = None  # required if role == customer


class ChatRequest(BaseModel):
    message: str
    thread_id: str
    user: UserContextIn


class ChatResponse(BaseModel):
    reply: str
    tool_trace: list[dict] = []
    pending_action: dict | None = None
    cited_sources: list[str] = []
    confidence: str | None = None


class ConfirmRequest(BaseModel):
    thread_id: str
    confirmed: bool


class ConfirmResponse(BaseModel):
    reply: str
    tool_trace: list[dict] = []
    action_result: dict | None = None


# ---- routes -----------------------------------------------------------------

@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    if req.user.role == "customer" and not req.user.account_id:
        raise HTTPException(400, "account_id is required for role=customer")
    try:
        Role(req.user.role)
    except ValueError:
        raise HTTPException(400, f"Invalid role: {req.user.role}")

    graph = get_graph()
    config = {"configurable": {"thread_id": req.thread_id}}

    # If this thread is paused awaiting confirmation, don't push a new
    # message through it — resolve the pending action first.
    existing = graph.get_state(config)
    if existing.next and "execute_action" in existing.next:
        return ChatResponse(
            reply="There's a pending action awaiting your confirmation — "
                  "please confirm or cancel it before sending a new message.",
        )

    result = graph.invoke({"user_ctx": req.user.model_dump(), "query": req.message}, config)

    state_after = graph.get_state(config)
    awaiting_confirmation = bool(state_after.next and "execute_action" in state_after.next)
    synthesis = result.get("synthesis") or {}

    return ChatResponse(
        reply=result.get("final_reply", ""),
        tool_trace=result.get("tool_trace", []),
        pending_action=result.get("pending_action") if awaiting_confirmation else None,
        cited_sources=synthesis.get("cited_sources", []),
        confidence=synthesis.get("confidence"),
    )


@router.post("/chat/confirm", response_model=ConfirmResponse)
def confirm(req: ConfirmRequest):
    graph = get_graph()
    config = {"configurable": {"thread_id": req.thread_id}}

    state = graph.get_state(config)
    if not state.next or "execute_action" not in state.next:
        raise HTTPException(409, "No pending action awaiting confirmation on this thread.")

    graph.update_state(config, {"action_confirmed": req.confirmed})
    result = graph.invoke(None, config)

    return ConfirmResponse(
        reply=result.get("final_reply", ""),
        tool_trace=result.get("tool_trace", []),
        action_result=result.get("action_result"),
    )
