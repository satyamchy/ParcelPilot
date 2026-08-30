"""
The ParcelPilot support agent.

Flow:
    router -> (retrieve_docs?) -> (retrieve_data?) -> reconcile -> synthesize
             -> respond
                  ^ or, if the router detected an action request:
             -> prepare_action -> [INTERRUPT] -> execute_action -> respond

The graph pauses right before execute_action (interrupt_before) whenever an
action is about to be committed. app/routes.py surfaces that as
`pending_action` to the client and resumes the graph later via
/chat/confirm — see that file for the resume mechanics.
"""
import sqlite3

from pydantic import BaseModel, Field
from langchain_groq import ChatGroq
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from typing import TypedDict, Annotated
from operator import add

from app.config import settings
from app.access_control import UserContext, Role, AccessDeniedError
from app.tools.documents import search_documents
from app.tools.data import (
    get_order, get_ticket, list_orders_for_account,
    resolve_account_id_by_name, calculate_delay_hours,
    calculate_service_credit, calculate_cancellation_fee,
)
from app.tools.actions import prepare_escalation, commit_action

# ============================================================================
# STATE
# ============================================================================

class AgentState(TypedDict, total=False):
    user_ctx: dict
    query: str
    history: list[dict]

    router_decision: dict | None
    retrieved_docs: list[dict]
    order_data: dict | None
    account_data: dict | None
    ticket_data: dict | None
    calc_result: dict | None
    reconciliation: dict | None
    synthesis: dict | None

    pending_action: dict | None
    action_confirmed: bool | None
    action_result: dict | None

    tool_trace: Annotated[list[dict], add]
    final_reply: str | None


# ============================================================================
# STRUCTURED-OUTPUT SCHEMAS (what each LLM call must return)
# ============================================================================

class RouterDecision(BaseModel):
    needs_document_search: bool = Field(
        description="True if answering requires searching policies/SOPs/contracts")
    doc_search_query: str | None = Field(default=None)
    doc_type_filter: str | None = Field(
        default=None,
        description="support_policy_current | cancellation_sop | product_ops | contract | null")

    needs_structured_data: bool = Field(
        description="True if answering requires order/account/ticket data or a calculation")
    order_id: str | None = Field(default=None, description="e.g. ORD-1001")
    ticket_id: str | None = Field(default=None, description="e.g. TCK-501")
    account_name_hint: str | None = Field(default=None, description="e.g. 'Northstar'")

    wants_action: bool = Field(
        description="True if the user is explicitly asking to escalate/file a request")
    action_reason: str | None = Field(default=None)


class ReconciliationResult(BaseModel):
    has_conflict: bool
    winning_source: str | None = Field(default=None, description="source_file that governs")
    conflict_explanation: str | None = Field(default=None)


class AnswerSynthesis(BaseModel):
    answer_text: str
    confidence: str = Field(description="'confident' or 'uncertain'")
    needs_escalation: bool
    escalation_reason: str | None = Field(default=None)
    cited_sources: list[str] = Field(default_factory=list)


# ============================================================================
# PROMPTS
# ============================================================================

ROUTER_PROMPT = """You are the routing component of ParcelPilot's support agent.
Decide what's needed to answer the user's message: document search
(policies/SOPs/contracts), structured data (orders/accounts/tickets/
calculations), and/or whether the user is explicitly requesting an action
(escalation). Extract any order IDs (ORD-####), ticket IDs (TCK-###), and
customer names mentioned. Do not invent IDs that weren't stated."""

RECONCILE_PROMPT = """You are reconciling potentially conflicting ParcelPilot
source documents. Each chunk is tagged with source_file, doc_type, status
(current/deprecated), and authority_rank (lower = more authoritative).

Rule: a customer's own signed contract overrides general policy on any term
it explicitly addresses; a current policy/SOP always overrides a deprecated
one. If two equally-authoritative current sources genuinely conflict with
no resolution rule available, say so (has_conflict=True, winning_source=None)
rather than picking one arbitrarily."""

SYNTHESIZE_PROMPT = """You are ParcelPilot's customer support agent, answering
a customer directly. Use ONLY the provided document excerpts and structured
data — no outside knowledge.

- If a winning source was identified, base your answer on it and briefly
  say why it governs (e.g. "your Enterprise agreement waives this fee").
- If the sources/data clearly answer the question, answer confidently
  (confidence="confident", needs_escalation=False).
- If fault attribution, eligibility, or the applicable rule is genuinely
  ambiguous, or no source covers this case, do NOT guess — set
  confidence="uncertain", needs_escalation=True, and explain what's unclear.
- Never present a deprecated policy as current guidance.
- Historical ticket notes are context only, not binding — don't cite them
  as the basis for an answer. If a historical ticket's resolution
  contradicts what the current SOP/contract/data calculation says, say so
  explicitly rather than repeating the old (possibly wrong) answer.
- If calc_result includes a cancellation or service-credit determination,
  use its reason and amount directly rather than recalculating — just
  explain it in plain language. Amounts are in INR.
- If account_data lists multiple orders and the user's question doesn't
  specify which shipment they mean (no order ID given), do NOT guess which
  order matches — ask the customer to confirm the order ID. Set
  confidence="uncertain" and needs_escalation=False in that case (it's a
  clarifying question, not an escalation).
- If a service credit's requires_manager_approval is true, mention that
  approval is needed before it's finalized.
- List which source_file(s) you actually relied on in cited_sources.
- Be conversational and explain the "why", not just the verdict."""

# ============================================================================
# LLM
# ============================================================================

_llm = ChatGroq(model=settings.groq_model, api_key=settings.groq_api_key, temperature=0)


def _uctx(state: AgentState) -> UserContext:
    d = state["user_ctx"]
    return UserContext(user_id=d["user_id"], role=Role(d["role"]), account_id=d.get("account_id"))


# ============================================================================
# NODES
# ============================================================================

def router_node(state: AgentState) -> dict:
    history_text = "\n".join(f"{h['role']}: {h['content']}" for h in state.get("history", [])[-6:])
    prompt = f"Conversation so far:\n{history_text}\n\nCurrent message:\n{state['query']}"
    decision: RouterDecision = _llm.with_structured_output(RouterDecision).invoke(
        [("system", ROUTER_PROMPT), ("user", prompt)]
    )
    result = decision.model_dump()
    if result.get("order_id") or result.get("ticket_id"):
        result["needs_structured_data"] = True

    # Critical: reset all per-turn retrieval/reasoning fields at the start
    # of every turn. Without this, a turn that doesn't need document or
    # data retrieval would silently inherit the PREVIOUS turn's retrieved
    # docs/order data (LangGraph persists state across turns on the same
    # thread_id, and a node that doesn't run doesn't clear old values) —
    # this is what caused an unrelated question to get answered with
    # leftover Northstar/ORD-1001 context from an earlier turn.
    return {
        "router_decision": result,
        "retrieved_docs": [],
        "order_data": None,
        "account_data": None,
        "ticket_data": None,
        "calc_result": None,
        "reconciliation": None,
        "synthesis": None,
        "pending_action": None,
        "action_confirmed": None,
        "action_result": None,
    }


def retrieve_docs_node(state: AgentState) -> dict:
    rd = state["router_decision"]
    if not rd.get("needs_document_search"):
        return {"retrieved_docs": []}

    uctx = _uctx(state)
    query = rd.get("doc_search_query") or state["query"]
    customer_id = resolve_account_id_by_name(rd["account_name_hint"]) if rd.get("account_name_hint") else None

    try:
        docs = search_documents(uctx, query=query, doc_type=rd.get("doc_type_filter"), customer_id=customer_id)
    except AccessDeniedError as e:
        return {"retrieved_docs": [],
                "tool_trace": [{"tool": "search_documents", "input_summary": query,
                                "output_summary": f"DENIED: {e}"}]}

    sources = ", ".join(sorted(set(d["source_file"] for d in docs))) or "none"
    return {"retrieved_docs": docs,
            "tool_trace": [{"tool": "search_documents", "input_summary": query,
                            "output_summary": f"{len(docs)} chunks from: {sources}"}]}


def retrieve_data_node(state: AgentState) -> dict:
    rd = state["router_decision"]
    if not rd.get("needs_structured_data"):
        return {}

    uctx = _uctx(state)
    trace, order_data, account_data, ticket_data, calc_result = [], None, None, None, None

    try:
        if rd.get("order_id"):
            order_data = get_order(uctx, rd["order_id"])
            trace.append({"tool": "get_order", "input_summary": rd["order_id"],
                          "output_summary": "found" if order_data else "not found"})
            if order_data:
                # Compute both possible calculations eagerly — the query
                # could be about cancellation or about a delay credit, and
                # both are cheap given we already have the order row.
                delay = calculate_delay_hours(order_data)
                credit = calculate_service_credit(order_data)
                cancellation = calculate_cancellation_fee(order_data)
                calc_result = {
                    "delay_hours": delay,
                    "service_credit": credit,
                    "cancellation": cancellation,
                }
                trace.append({"tool": "calculate_service_credit", "input_summary": rd["order_id"],
                              "output_summary": f"eligible={credit['eligible']}, credit_inr={credit.get('credit_inr')}"})
                trace.append({"tool": "calculate_cancellation_fee", "input_summary": rd["order_id"],
                              "output_summary": f"cancellable={cancellation['cancellable']}, fee_inr={cancellation.get('fee_inr')}"})

        if rd.get("ticket_id"):
            ticket_data = get_ticket(uctx, rd["ticket_id"])
            trace.append({"tool": "get_ticket", "input_summary": rd["ticket_id"],
                          "output_summary": "found" if ticket_data else "not found"})

        if not order_data and not ticket_data and rd.get("account_name_hint"):
            account_id = resolve_account_id_by_name(rd["account_name_hint"])
            if account_id:
                if uctx.role == Role.CUSTOMER:
                    account_id = uctx.account_id
                orders = list_orders_for_account(uctx, account_id)
                trace.append({"tool": "list_orders_for_account", "input_summary": account_id,
                              "output_summary": f"{len(orders)} orders"})
                account_data = {"account_id": account_id, "orders": orders}

        # No specific order/ticket/account was named at all, but the query
        # clearly needs data (e.g. "a pickup is 3 hours late, do I get a
        # credit?" with no order ID given). For a customer we already know
        # their account — pull their orders so the agent has something to
        # reason over, and can ask which shipment if more than one could
        # match, rather than fabricating or reusing stale context.
        if not order_data and not ticket_data and not account_data and uctx.role == Role.CUSTOMER:
            orders = list_orders_for_account(uctx, uctx.account_id)
            trace.append({"tool": "list_orders_for_account", "input_summary": uctx.account_id,
                          "output_summary": f"{len(orders)} orders (no specific order named by user)"})
            account_data = {"account_id": uctx.account_id, "orders": orders}

    except AccessDeniedError as e:
        trace.append({"tool": "structured_data_lookup", "input_summary": str(rd), "output_summary": f"DENIED: {e}"})
        return {"tool_trace": trace, "calc_result": {"access_denied": str(e)}}

    return {"order_data": order_data, "account_data": account_data, "ticket_data": ticket_data,
            "calc_result": calc_result, "tool_trace": trace}


def reconcile_node(state: AgentState) -> dict:
    docs = state.get("retrieved_docs") or []
    if len(set(d["source_file"] for d in docs)) < 2:
        return {"reconciliation": {"has_conflict": False, "winning_source": None, "conflict_explanation": None}}

    docs_text = "\n\n".join(
        f"[{d['source_file']} | doc_type={d['doc_type']} | status={d['status']} | rank={d['authority_rank']}]\n{d['text']}"
        for d in docs
    )
    result: ReconciliationResult = _llm.with_structured_output(ReconciliationResult).invoke(
        [("system", RECONCILE_PROMPT), ("user", f"User question: {state['query']}\n\nSources:\n{docs_text}")]
    )
    return {"reconciliation": result.model_dump()}


def synthesize_node(state: AgentState) -> dict:
    docs = state.get("retrieved_docs") or []
    docs_text = "\n\n".join(f"[{d['source_file']} | status={d['status']}]\n{d['text']}" for d in docs) \
        or "(no documents retrieved)"

    data_bits = []
    for label in ("order_data", "calc_result", "account_data", "ticket_data"):
        if state.get(label):
            data_bits.append(f"{label}: {state[label]}")
    data_text = "\n".join(data_bits) or "(no structured data retrieved)"

    recon = state.get("reconciliation") or {}
    recon_text = f"Reconciliation: has_conflict={recon.get('has_conflict')}, " \
                 f"winning_source={recon.get('winning_source')}, explanation={recon.get('conflict_explanation')}"

    prompt = f"User question: {state['query']}\n\nDocuments:\n{docs_text}\n\nData:\n{data_text}\n\n{recon_text}"
    result: AnswerSynthesis = _llm.with_structured_output(AnswerSynthesis).invoke(
        [("system", SYNTHESIZE_PROMPT), ("user", prompt)]
    )
    return {"synthesis": result.model_dump()}


def prepare_action_node(state: AgentState) -> dict:
    uctx = _uctx(state)
    rd = state["router_decision"]
    synthesis = state.get("synthesis") or {}
    reason = rd.get("action_reason") or synthesis.get("escalation_reason") or "Customer requested escalation."
    order_id = rd.get("order_id") or (state.get("order_data") or {}).get("order_id")
    ticket_id = rd.get("ticket_id") or (state.get("ticket_data") or {}).get("ticket_id")

    try:
        preview = prepare_escalation(uctx, reason=reason, ticket_id=ticket_id, order_id=order_id,
                                      priority="high" if synthesis.get("needs_escalation") else "medium")
    except AccessDeniedError as e:
        return {"pending_action": None,
                "tool_trace": [{"tool": "prepare_escalation", "input_summary": reason, "output_summary": f"DENIED: {e}"}]}

    return {"pending_action": preview,
            "tool_trace": [{"tool": "prepare_escalation", "input_summary": reason,
                            "output_summary": f"preview {preview.get('preview_id')} awaiting confirmation"}]}


def execute_action_node(state: AgentState) -> dict:
    uctx = _uctx(state)
    preview, confirmed = state.get("pending_action"), state.get("action_confirmed")
    if preview is None or confirmed is None:
        return {}
    result = commit_action(uctx, preview["preview_id"], confirmed=confirmed)
    return {"action_result": result,
            "tool_trace": [{"tool": "commit_action", "input_summary": preview["preview_id"],
                            "output_summary": result.get("status")}]}


def respond_node(state: AgentState) -> dict:
    synthesis = state.get("synthesis") or {}
    reply = synthesis.get("answer_text", "")

    action_result = state.get("action_result")
    if action_result:
        if action_result.get("status") == "committed":
            reply += f"\n\nDone — escalation {action_result['action_id'][:8]} has been created."
        elif action_result.get("status") == "cancelled":
            reply += "\n\nOkay, I won't file that escalation."
    elif state.get("pending_action"):
        reply += "\n\nI've prepared this but haven't submitted it — please confirm to proceed."
    elif synthesis.get("needs_escalation"):
        reply += "\n\nI'd like to route this to a support team member rather than guess — should I create that escalation?"

    history = state.get("history", []) + [
        {"role": "user", "content": state["query"]},
        {"role": "assistant", "content": reply},
    ]
    return {"final_reply": reply, "history": history}


# ============================================================================
# EDGES
# ============================================================================

def route_after_router(state: AgentState) -> str:
    rd = state["router_decision"]
    if rd.get("needs_document_search"):
        return "retrieve_docs"
    if rd.get("needs_structured_data"):
        return "retrieve_data"
    return "synthesize"


def route_after_docs(state: AgentState) -> str:
    return "retrieve_data" if state["router_decision"].get("needs_structured_data") else "reconcile"


def route_after_synthesis(state: AgentState) -> str:
    return "prepare_action" if state["router_decision"].get("wants_action") else "respond"


# ============================================================================
# BUILD
# ============================================================================

def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("router", router_node)
    graph.add_node("retrieve_docs", retrieve_docs_node)
    graph.add_node("retrieve_data", retrieve_data_node)
    graph.add_node("reconcile", reconcile_node)
    graph.add_node("synthesize", synthesize_node)
    graph.add_node("prepare_action", prepare_action_node)
    graph.add_node("execute_action", execute_action_node)
    graph.add_node("respond", respond_node)

    graph.set_entry_point("router")
    graph.add_conditional_edges("router", route_after_router,
                                 {"retrieve_docs": "retrieve_docs", "retrieve_data": "retrieve_data",
                                  "synthesize": "synthesize"})
    graph.add_conditional_edges("retrieve_docs", route_after_docs,
                                 {"retrieve_data": "retrieve_data", "reconcile": "reconcile"})
    graph.add_edge("retrieve_data", "reconcile")
    graph.add_edge("reconcile", "synthesize")
    graph.add_conditional_edges("synthesize", route_after_synthesis,
                                 {"prepare_action": "prepare_action", "respond": "respond"})
    graph.add_edge("prepare_action", "execute_action")
    graph.add_edge("execute_action", "respond")
    graph.add_edge("respond", END)

    conn = sqlite3.connect(settings.checkpoint_db_path, check_same_thread=False)
    return graph.compile(checkpointer=SqliteSaver(conn), interrupt_before=["execute_action"])


_compiled = None


def get_graph():
    global _compiled
    if _compiled is None:
        _compiled = build_graph()
    return _compiled
