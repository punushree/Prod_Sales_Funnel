"""
permissions.py — AOS Agent Platform
Role-based permission classes for all API endpoints.

Roles:
  super_admin  — full access, no org restriction
  org_admin    — full access within their own org only
  agent        — limited: update own leads, read calls, create tickets
"""

from rest_framework.permissions import BasePermission, SAFE_METHODS


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def is_super_admin(user):
    return user.is_authenticated and user.role == "super_admin"

def is_org_admin(user):
    return user.is_authenticated and user.role == "org_admin"

def is_agent(user):
    return user.is_authenticated and user.role == "agent"

def same_org(user, obj):
    """Check if object belongs to the user's org."""
    org = getattr(obj, "org", None)
    return org is not None and org == user.org


# ─────────────────────────────────────────────
# 1. SUPER ADMIN ONLY
#    Organisations CRUD, cross-org stats, Django admin
# ─────────────────────────────────────────────

class IsSuperAdmin(BasePermission):
    """
    Allow only super_admin users.
    Used on: /orgs/, /orgs/<id>/stats/ (global)
    """
    message = "Only super admins can perform this action."

    def has_permission(self, request, view):
        return is_super_admin(request.user)


# ─────────────────────────────────────────────
# 2. ORG ADMIN OR SUPER ADMIN
#    Manage users, customers, org settings within org
# ─────────────────────────────────────────────

class IsOrgAdminOrSuperAdmin(BasePermission):
    """
    Allow super_admin or org_admin.
    Used on: /users/, /customers/, /orgs/<id>/ (PATCH for org admin's own org)
    """
    message = "Only org admins or super admins can perform this action."

    def has_permission(self, request, view):
        return is_super_admin(request.user) or is_org_admin(request.user)

    def has_object_permission(self, request, view, obj):
        # Super admin can access anything
        if is_super_admin(request.user):
            return True
        # Org admin can only access objects within their own org
        return same_org(request.user, obj)


# ─────────────────────────────────────────────
# 3. SAME ORG
#    Authenticated users can only see/edit data in their own org
# ─────────────────────────────────────────────

class IsSameOrg(BasePermission):
    """
    Any authenticated user but restricted to their own org's objects.
    Super admin bypasses the org restriction.
    Used on: /leads/, /calls/, /tickets/
    """
    message = "You do not have access to this resource."

    def has_permission(self, request, view):
        return request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        if is_super_admin(request.user):
            return True
        return same_org(request.user, obj)


# ─────────────────────────────────────────────
# 4. LEAD PERMISSIONS
#    - super_admin: full CRUD all orgs
#    - org_admin: full CRUD own org
#    - agent: read own org leads + update status/notes only
# ─────────────────────────────────────────────

class LeadPermission(BasePermission):
    """
    Used on: /leads/, /leads/<id>/
    """
    message = "You do not have permission to perform this action on leads."

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        # Agents can only read (GET) leads — not create new ones
        if is_agent(request.user) and request.method not in SAFE_METHODS:
            # Allow PATCH for status/notes update but not POST (create)
            if request.method == "POST":
                return False
        return True

    def has_object_permission(self, request, view, obj):
        if is_super_admin(request.user):
            return True
        # Must be same org
        if not same_org(request.user, obj):
            return False
        # Agent can only update — not delete
        if is_agent(request.user) and request.method == "DELETE":
            return False
        return True


# ─────────────────────────────────────────────
# 5. CALL PERMISSIONS
#    - super_admin: full access all orgs
#    - org_admin: full access own org
#    - agent: read only own org
#    - bolna webhook: uses API key auth (separate)
# ─────────────────────────────────────────────

class CallPermission(BasePermission):
    """
    Used on: /calls/, /calls/<id>/
    """
    message = "You do not have permission to access calls."

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        # Agents: read only
        if is_agent(request.user) and request.method not in SAFE_METHODS:
            return False
        return True

    def has_object_permission(self, request, view, obj):
        if is_super_admin(request.user):
            return True
        return same_org(request.user, obj)


# ─────────────────────────────────────────────
# 6. TICKET PERMISSIONS
#    - super_admin: full access
#    - org_admin: full access own org
#    - agent: create + read own org (no delete)
# ─────────────────────────────────────────────

class TicketPermission(BasePermission):
    """
    Used on: /tickets/, /tickets/<id>/
    """
    message = "You do not have permission to perform this action on tickets."

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        # Agents cannot DELETE tickets
        if is_agent(request.user) and request.method == "DELETE":
            return False
        return True

    def has_object_permission(self, request, view, obj):
        if is_super_admin(request.user):
            return True
        if not same_org(request.user, obj):
            return False
        # Agent cannot delete
        if is_agent(request.user) and request.method == "DELETE":
            return False
        return True


# ─────────────────────────────────────────────
# 7. BOLNA WEBHOOK AUTH
#    /calls/start/ and /calls/<id>/end/ use API key
# ─────────────────────────────────────────────

class BolnaWebhookPermission(BasePermission):
    """
    Validates the X-Bolna-API-Key header for webhook endpoints.
    Set BOLNA_WEBHOOK_SECRET in settings/env.
    Used on: /calls/start/, /calls/<id>/end/
    """
    message = "Invalid or missing Bolna API key."

    def has_permission(self, request, view):
        from django.conf import settings
        secret = getattr(settings, "BOLNA_WEBHOOK_SECRET", None)
        if not secret:
            # If not configured, fall back to JWT auth
            return request.user.is_authenticated
        provided = request.headers.get("X-Bolna-API-Key", "")
        return provided == secret


# ─────────────────────────────────────────────
# 8. CUSTOMER LOOKUP (voice agent)
#    /customers/lookup/ — called by voice agent with API key
# ─────────────────────────────────────────────

class VoiceAgentPermission(BasePermission):
    """
    API key auth for voice agent phone-number lookup.
    Used on: /customers/lookup/
    """
    message = "Invalid or missing voice agent API key."

    def has_permission(self, request, view):
        from django.conf import settings
        secret = getattr(settings, "VOICE_AGENT_SECRET", None)
        if not secret:
            return request.user.is_authenticated
        provided = request.headers.get("X-Agent-Key", "")
        return provided == secret


# ─────────────────────────────────────────────
# QUICK REFERENCE — which permission to use where
# ─────────────────────────────────────────────
#
# Endpoint                        Permission class
# ─────────────────────────────── ─────────────────────────────────
# GET/POST /orgs/                 IsSuperAdmin
# PATCH    /orgs/<id>/            IsSuperAdmin
# GET      /orgs/<id>/stats/      IsSuperAdmin
# GET/POST /users/                IsOrgAdminOrSuperAdmin
# GET/POST /leads/                LeadPermission
# PATCH    /leads/<id>/           LeadPermission
# GET/POST /customers/            IsOrgAdminOrSuperAdmin
# GET      /customers/lookup/     VoiceAgentPermission
# POST     /calls/start/          BolnaWebhookPermission
# POST     /calls/<id>/end/       BolnaWebhookPermission
# GET      /calls/                CallPermission
# GET      /calls/<id>/           CallPermission
# GET/POST /tickets/              TicketPermission
# PATCH    /tickets/<id>/         TicketPermission