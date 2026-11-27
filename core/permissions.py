"""
Single source of truth for role-based permissions.
"""

PERMISSIONS: dict[str, set[str]] = {
    "submit_complaint":      {"user"},
    "view_own":              {"user"},
    "add_images":            {"user"},
    "submit_feedback":       {"user"},
    "view_all":              {"employee", "admin", "super_admin"},
    "update_status_remark":  {"employee", "admin", "super_admin"},
    "claim_complaint":       {"employee"},
    "edit_complaint_fields": {"admin", "super_admin"},
    "assign_complaint":      {"admin", "super_admin"},
    "remove_image":          {"admin", "super_admin"},
    "delete_complaint":      {"super_admin"},
    "manage_users":          {"super_admin"},
    "manage_departments":    {"super_admin"},
    "manage_sla":            {"super_admin"},
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
