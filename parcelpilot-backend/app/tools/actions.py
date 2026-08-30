"""
Tool 3 — State-changing action (mocked locally): create an escalation.

Two-step commit:
  1. prepare_escalation(...) -> returns a PREVIEW, writes nothing.
  2. commit_action(preview_id, confirmed) -> writes only if confirmed=True.

The confirmation gate itself is enforced by the LangGraph interrupt in
app/graph.py — this module independently re-checks confirmed as defense
in depth.
"""
import sqlite3
import uuid
from datetime import datetime

from app.config import settings
from app.access_control import UserContext, check_account_access, check_action_permission
from app.tools.data import get_ticket, get_order

_PENDING: dict[str, dict] = {}  # in-memory, fine for a single-process demo


def _ensure_table():
    conn = sqlite3.connect(settings.sqlite_db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mock_actions (
            action_id TEXT PRIMARY KEY,
            action_type TEXT,
            ticket_id TEXT,
            order_id TEXT,
            account_id TEXT,
            reason TEXT,
            priority TEXT,
            created_by TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def prepare_escalation(
    user_ctx: UserContext,
    reason: str,
    ticket_id: str | None = None,
    order_id: str | None = None,
    priority: str = "medium",
) -> dict:
    check_action_permission(user_ctx, "create_escalation")

    if ticket_id:
        ticket = get_ticket(user_ctx, ticket_id)
        if ticket is None:
            return {"error": f"Ticket {ticket_id} not found."}
        account_id = ticket["account_id"]
    elif order_id:
        order = get_order(user_ctx, order_id)
        if order is None:
            return {"error": f"Order {order_id} not found."}
        account_id = order["account_id"]
    else:
        check_account_access(user_ctx, user_ctx.account_id)
        account_id = user_ctx.account_id

    preview_id = str(uuid.uuid4())
    preview = {
        "preview_id": preview_id,
        "action_type": "create_escalation",
        "ticket_id": ticket_id,
        "order_id": order_id,
        "account_id": account_id,
        "reason": reason,
        "priority": priority,
        "status": "pending_confirmation",
    }
    _PENDING[preview_id] = preview
    return preview


def commit_action(user_ctx: UserContext, preview_id: str, confirmed: bool) -> dict:
    preview = _PENDING.get(preview_id)
    if preview is None:
        return {"error": "No pending action found for this preview_id."}

    check_account_access(user_ctx, preview["account_id"])

    if not confirmed:
        _PENDING.pop(preview_id, None)
        return {"status": "cancelled", "preview_id": preview_id}

    _ensure_table()
    conn = sqlite3.connect(settings.sqlite_db_path)
    conn.execute(
        "INSERT INTO mock_actions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (preview_id, preview["action_type"], preview["ticket_id"], preview["order_id"],
         preview["account_id"], preview["reason"], preview["priority"],
         user_ctx.user_id, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    _PENDING.pop(preview_id, None)
    return {"status": "committed", "action_id": preview_id, "action_type": preview["action_type"]}
