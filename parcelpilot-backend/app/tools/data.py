"""
Tool 2 — Structured-data lookup and calculation over the SQLite tables
loaded from the workbook (accounts / orders / tickets).

Every lookup calls check_account_access() BEFORE returning another
account's data — this is the actual enforcement point, not a prompt
instruction.

Business rules below (cancellation fees, service credits) are hardcoded
from the actual SOP (03_Cancellation_and_Service_Credit_SOP_v4.pdf) and
the two customer contracts. Contract overrides are applied by account_id;
if you add more customer-specific contracts, add their override here too
rather than relying on the LLM to compute the numbers from retrieved text.
"""
import sqlite3
from datetime import datetime

from app.config import settings
from app.access_control import UserContext, check_account_access
from app.snapshot import get_snapshot_time

# Account IDs with a signed contract overriding the default SOP.
NORTHSTAR_ACCOUNT_ID = "ACCT-001"
LUMENWORKS_ACCOUNT_ID = "ACCT-002"

# Default SOP figures (03_Cancellation_and_Service_Credit_SOP_v4.pdf)
DEFAULT_CANCELLATION_GRACE_MINUTES = 30
DEFAULT_CANCELLATION_FEE_INR = 250
DEFAULT_CREDIT_DELAY_THRESHOLD_HOURS = 2
DEFAULT_CREDIT_CAP_INR = 500
DEFAULT_CREDIT_PERCENT_OF_FEE = 0.10
MANAGER_APPROVAL_THRESHOLD_INR = 1000

# LumenWorks contract override (06_LumenWorks_Service_Agreement.pdf)
LUMENWORKS_CREDIT_DELAY_THRESHOLD_HOURS = 4
LUMENWORKS_FIXED_CREDIT_INR = 300


def _connect():
    conn = sqlite3.connect(settings.sqlite_db_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_order(user_ctx: UserContext, order_id: str) -> dict | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    order = dict(row)
    check_account_access(user_ctx, order.get("account_id"))
    return order


def get_ticket(user_ctx: UserContext, ticket_id: str) -> dict | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    ticket = dict(row)
    check_account_access(user_ctx, ticket.get("account_id"))
    return ticket


def list_orders_for_account(user_ctx: UserContext, account_id: str) -> list[dict]:
    check_account_access(user_ctx, account_id)
    conn = _connect()
    rows = conn.execute("SELECT * FROM orders WHERE account_id = ?", (account_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_tickets_for_account(user_ctx: UserContext, account_id: str) -> list[dict]:
    check_account_access(user_ctx, account_id)
    conn = _connect()
    rows = conn.execute("SELECT * FROM tickets WHERE account_id = ?", (account_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def resolve_account_id_by_name(name_hint: str) -> str | None:
    """Case-insensitive name match, e.g. 'Northstar' -> 'ACCT-001'. No
    access check needed — this returns an ID, not any account data."""
    if not name_hint:
        return None
    conn = _connect()
    rows = conn.execute("SELECT account_id, account_name FROM accounts").fetchall()
    conn.close()
    needle = name_hint.lower()
    for row in rows:
        if needle in row["account_name"].lower() or needle in row["account_id"].lower():
            return row["account_id"]
    return None


def _parse_dt(value) -> datetime | None:
    if value is None or str(value).lower() in ("none", "nan", ""):
        return None
    return datetime.fromisoformat(str(value))


def calculate_delay_hours(order: dict) -> float | None:
    """Hours past the scheduled pickup WINDOW END. If the shipment hasn't
    been picked up yet, compares the dataset snapshot time against the
    window end instead of actual pickup time."""
    window_end = _parse_dt(order.get("pickup_window_end"))
    if window_end is None:
        return None

    actual = _parse_dt(order.get("pickup_actual_at"))
    if actual is not None:
        return round((actual - window_end).total_seconds() / 3600.0, 2)

    now = get_snapshot_time()
    if now > window_end:
        return round((now - window_end).total_seconds() / 3600.0, 2)
    return None  # window hasn't even closed yet as of the snapshot time


def calculate_service_credit(order: dict) -> dict:
    """
    Applies the SOP's failed-pickup service-credit rule, with the
    LumenWorks contract override (4hr threshold, fixed INR 300) applied
    when relevant. Never guesses fault: if carrier_fault/customer_fault
    are missing/null, returns eligible=None + escalate=True per the SOP's
    explicit instruction not to promise a credit when fault is unknown.
    """
    account_id = order.get("account_id")
    delay_hours = calculate_delay_hours(order)
    carrier_fault = order.get("carrier_fault")
    customer_fault = order.get("customer_fault")

    if delay_hours is None or delay_hours <= 0:
        return {"eligible": False, "reason": "No delay past the pickup window detected.",
                "delay_hours": delay_hours, "credit_inr": 0}

    if carrier_fault is None or customer_fault is None:
        return {"eligible": None, "escalate": True,
                "reason": "Carrier/customer fault is not recorded for this order — "
                          "per SOP section 3, a credit must not be promised when "
                          "fault is unknown.",
                "delay_hours": delay_hours, "credit_inr": None}

    if account_id == LUMENWORKS_ACCOUNT_ID:
        threshold = LUMENWORKS_CREDIT_DELAY_THRESHOLD_HOURS
        fixed_amount = LUMENWORKS_FIXED_CREDIT_INR
        rule_source = "LumenWorks Service Agreement (overrides default SOP threshold/amount)"
    else:
        threshold = DEFAULT_CREDIT_DELAY_THRESHOLD_HOURS
        fixed_amount = None
        rule_source = "Cancellation and Service Credit SOP v4 (default rule)"

    if delay_hours <= threshold:
        return {"eligible": False,
                "reason": f"Delay of {delay_hours}h does not exceed the {threshold}-hour "
                          f"threshold ({rule_source}).",
                "delay_hours": delay_hours, "credit_inr": 0}

    if not carrier_fault or customer_fault:
        return {"eligible": False,
                "reason": "Delay exceeds the threshold but the order is not recorded as "
                          "carrier-fault-only (carrier_fault must be true and "
                          "customer_fault false).",
                "delay_hours": delay_hours, "credit_inr": 0}

    if fixed_amount is not None:
        credit_inr = fixed_amount
    else:
        credit_inr = min(DEFAULT_CREDIT_CAP_INR,
                          round(order.get("shipment_fee_inr", 0) * DEFAULT_CREDIT_PERCENT_OF_FEE, 2))

    result = {
        "eligible": True,
        "reason": f"Delay of {delay_hours}h exceeds the {threshold}-hour threshold and is "
                  f"carrier fault with no customer fault, per {rule_source}.",
        "delay_hours": delay_hours,
        "credit_inr": credit_inr,
        "requires_manager_approval": credit_inr > MANAGER_APPROVAL_THRESHOLD_INR,
    }
    if account_id == NORTHSTAR_ACCOUNT_ID:
        result["note"] = ("Northstar's monthly aggregate service credits are capped at "
                           "INR 5,000 per their Enterprise Agreement — verify this order "
                           "doesn't push the account over that cap before confirming.")
    return result


def calculate_cancellation_fee(order: dict) -> dict:
    """
    Applies the SOP's cancellation rules, with the Northstar contract
    override (free cancellation any time before pickup) applied when
    relevant.
    """
    account_id = order.get("account_id")
    status = (order.get("status") or "").upper()

    if status in ("PICKED_UP", "DELIVERED"):
        return {"cancellable": False, "fee_inr": None,
                "reason": f"Order status is {status} — per the SOP, orders that have "
                          f"already been picked up cannot be cancelled; use the "
                          f"return-to-origin workflow instead."}

    if account_id == NORTHSTAR_ACCOUNT_ID:
        return {"cancellable": True, "fee_inr": 0,
                "reason": "Northstar's Enterprise Agreement waives the cancellation fee "
                          "entirely for any BOOKED shipment cancelled before pickup, "
                          "regardless of how long ago it was booked."}

    booked_at = _parse_dt(order.get("booked_at"))
    requested_at = _parse_dt(order.get("cancellation_requested_at")) or get_snapshot_time()

    if booked_at is None:
        return {"cancellable": True, "fee_inr": None,
                "reason": "Cannot determine time since booking — booked_at is missing."}

    minutes_since_booking = (requested_at - booked_at).total_seconds() / 60.0
    if minutes_since_booking <= DEFAULT_CANCELLATION_GRACE_MINUTES:
        return {"cancellable": True, "fee_inr": 0,
                "reason": f"Cancellation requested {minutes_since_booking:.0f} minutes after "
                          f"booking, within the {DEFAULT_CANCELLATION_GRACE_MINUTES}-minute "
                          f"grace period — no fee applies."}

    return {"cancellable": True, "fee_inr": DEFAULT_CANCELLATION_FEE_INR,
            "reason": f"Cancellation requested {minutes_since_booking:.0f} minutes after "
                      f"booking, past the {DEFAULT_CANCELLATION_GRACE_MINUTES}-minute grace "
                      f"period — the standard INR {DEFAULT_CANCELLATION_FEE_INR} fee applies "
                      f"per the SOP (no contract waiver on this account)."}
