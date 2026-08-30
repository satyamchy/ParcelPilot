"""
Access control lives here, and only here. Every tool that touches customer
data calls check_account_access() before returning anything. This is a hard
Python exception, not a prompt instruction — it holds even if the agent's
reasoning goes off the rails.
"""
from dataclasses import dataclass
from enum import Enum


class Role(str, Enum):
    CUSTOMER = "customer"
    INTERNAL_SUPPORT = "internal_support"
    INTERNAL_ADMIN = "internal_admin"


@dataclass(frozen=True)
class UserContext:
    user_id: str
    role: Role
    account_id: str | None  # required for customer role


class AccessDeniedError(Exception):
    pass


def check_account_access(user_ctx: UserContext, target_account_id: str | None) -> None:
    """Raise if user_ctx may not see data belonging to target_account_id."""
    if user_ctx.role in (Role.INTERNAL_SUPPORT, Role.INTERNAL_ADMIN):
        return  # internal staff — full account access

    if target_account_id is None:
        raise AccessDeniedError("Customer role requires a resolvable account_id.")
    if target_account_id != user_ctx.account_id:
        raise AccessDeniedError(
            f"User {user_ctx.user_id} (account={user_ctx.account_id}) is not "
            f"authorized to access account '{target_account_id}'."
        )


def check_action_permission(user_ctx: UserContext, action: str) -> None:
    """Gate for state-changing actions. Extend this as more action types are added."""
    if user_ctx.role == Role.CUSTOMER and action not in ("create_escalation",):
        raise AccessDeniedError(f"Customer role cannot perform action '{action}'.")
