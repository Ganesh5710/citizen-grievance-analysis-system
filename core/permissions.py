"""
Single source of truth for role-based permissions.
"""

PERMISSIONS: dict[str, set[str]] = {
    "submit_complaint":       {"user"},
    "view_own_complaints":    {"user"},
    "view_all_complaints":    {"employee", "admin", "super_admin"},
    "update_complaint":       {"employee"},
    "view_complaint_history": {"user", "employee", "admin", "super_admin"},
    "create_staff_account":   {"super_admin"},
    "view_users":             {"super_admin"},
}

VALID_ROLES = {"user", "employee", "admin", "super_admin"}


def can(user: dict | None, permission: str) -> bool:
    """Return True if the user dict holds a role allowed to perform `permission`."""
    if not isinstance(user, dict):
        return False
    return user.get("role") in PERMISSIONS.get(permission, set())


def require(user: dict | None, permission: str) -> None:
    """Raise PermissionError unless `user` holds `permission`."""
    if not can(user, permission):
        role = user.get("role") if isinstance(user, dict) else None
        raise PermissionError(
            f"Role '{role}' lacks permission: '{permission}'"
        )
