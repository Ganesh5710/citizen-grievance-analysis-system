"""
Single source of truth for role-based permissions.
"""

PERMISSIONS: dict[str, set[str]] = {
    "submit_complaint":      {"user"},
    "view_own":              {"user"},
    "add_images":            {"user"},
    "submit_feedback":       {"user"},
    "support_complaint":     {"user"},
    "view_all":              {"employee", "admin", "super_admin"},
    "update_status_remark":  {"employee", "admin", "super_admin"},
    "update_task_status":    {"employee", "admin", "super_admin"},
    "accept_task":           {"employee"},
    "claim_complaint":       {"employee"},
    "edit_complaint_fields": {"admin", "super_admin"},
    "assign_complaint":      {"admin", "super_admin"},
    "assign_task":           {"admin", "super_admin"},
    "remove_image":          {"admin", "super_admin"},
    "view_review_queue":     {"admin", "super_admin"},
    "resolve_review_item":   {"admin", "super_admin"},
    "view_analytics":        {"employee", "admin", "super_admin"},
    "delete_complaint":      {"super_admin"},
    "manage_users":          {"super_admin"},
    "manage_admins":         {"super_admin"},
    "view_admin_oversight":  {"super_admin"},
    "manage_locations":      {"super_admin"},
    "manage_departments":    {"super_admin"},
    "manage_sla":            {"super_admin"},
    "manage_priority_rules": {"super_admin"},
    "manage_ml":             {"super_admin"},
    "view_public_dashboard": {"*"},
}

VALID_ROLES = {"user", "employee", "admin", "super_admin"}


def can(user: dict | None, permission: str) -> bool:
    """Return True if the user dict holds a role allowed to perform `permission`."""
    allowed = PERMISSIONS.get(permission, set())
    if "*" in allowed:
        return True
    if not isinstance(user, dict):
        return False
    return user.get("role") in allowed


def require(user: dict | None, permission: str) -> None:
    """Raise PermissionError unless `user` holds `permission`."""
    if not can(user, permission):
        role = user.get("role") if isinstance(user, dict) else None
        raise PermissionError(
            f"Role '{role}' lacks permission: '{permission}'"
        )
