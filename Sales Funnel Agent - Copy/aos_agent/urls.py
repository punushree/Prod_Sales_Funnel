"""
aos_agent/urls.py — App URL config  (v2 — multi-tenant spec)

NEW URLs:
  - /numbers/                     — list allowed phone numbers (org-scoped)
  - /numbers/add/                 — add a number to whitelist
  - /numbers/<id>/remove/         — remove a number from whitelist
  - /request-org/                 — public form: client requests an org
  - /request-org/success/         — confirmation page
  - /api/org-requests/<id>/review/ — super admin approves/rejects request
  - /org/users/                   — org admin manages own users
  - /api/org/users/create/        — org admin creates users in own org
"""
from django.urls import path
from . import views

urlpatterns = [

    # ── Auth ─────────────────────────────────────────────────────────
    path("login/",  views.login_view,  name="login"),
    path("logout/", views.logout_view, name="logout"),

    # ── HTML pages ───────────────────────────────────────────────────
    path("",                               views.dashboard_view,   name="dashboard"),
    path("orgs/",                          views.org_manage_view,  name="org_list"),
    path("calls/",                         views.call_list_view,   name="call_list"),
    path("calls/<uuid:call_id>/",          views.call_detail_view,    name="call_detail"),
    path("calls/<uuid:call_id>/analysis/", views.call_analysis_view,  name="call_analysis"),
    path("leads/",                         views.lead_list_view,   name="lead_list"),
    path("leads/create/",                  views.lead_create_view, name="lead_create"),
    path("leads/<uuid:lead_id>/",          views.lead_detail_view, name="lead_detail"),
    path("tickets/",                       views.ticket_list_view, name="ticket_list"),
    path("customers/",                     views.customer_list_view, name="customer_list"),

    # ── ★ BOLNA ──────────────────────────────────────────────────────
    path("api/bolna/initiate/",  views.bolna_initiate, name="bolna_initiate"),
    path("api/bolna/webhook/",   views.bolna_webhook,  name="bolna_webhook"),
    path("webhook/bolna/",       views.bolna_webhook),  # alias

    # ── ★ NUMBER WHITELIST (per-org) ─────────────────────────────────
    path("numbers/",                      views.allowed_numbers_list,   name="allowed_numbers_list"),
    path("numbers/add/",                  views.allowed_numbers_add,    name="allowed_numbers_add"),
    path("numbers/<uuid:number_id>/remove/", views.allowed_numbers_remove, name="allowed_numbers_remove"),

    # ── ★ ORG REQUEST (public form) ──────────────────────────────────
    path("request-org/",         views.org_request_form,    name="org_request_form"),
    path("request-org/success/", lambda r: __import__('django.shortcuts', fromlist=['render']).render(r, "org_request_success.html"), name="org_request_success"),
    path("api/org-requests/<uuid:request_id>/review/", views.org_request_review, name="org_request_review"),

    # ── ★ ORG ADMIN — User Management ───────────────────────────────
    path("org/users/",             views.org_users_view,         name="org_users"),
    path("api/org/users/create/",  views.org_admin_create_user,  name="org_admin_create_user"),

    # ── DEPRECATED — returns 410 ─────────────────────────────────────
    path("analyze-manual-transcript/", views.analyze_manual_transcript, name="analyze_manual_transcript"),

    # ── REST API v1 ──────────────────────────────────────────────────
    path("api/v1/orgs/",                      views.org_list,         name="api_org_list"),
    path("api/v1/orgs/<uuid:org_id>/",        views.org_detail,       name="api_org_detail"),
    path("api/v1/orgs/<uuid:org_id>/stats/",  views.org_stats,        name="api_org_stats"),

    path("api/v1/leads/",                     views.lead_list,        name="api_lead_list"),
    path("api/v1/leads/<uuid:lead_id>/",      views.lead_detail,      name="api_lead_detail"),

    path("api/v1/customers/lookup/",          views.customer_lookup,  name="api_customer_lookup"),

    path("api/v1/calls/start/",               views.call_start,       name="api_call_start"),
    path("api/v1/calls/<uuid:call_id>/end/",  views.call_end,         name="api_call_end"),
    path("api/v1/calls/",                     views.call_list,        name="api_call_list"),
    path("api/v1/calls/<uuid:call_id>/",      views.call_detail,      name="api_call_detail"),

    path("api/v1/tickets/",                   views.ticket_list,      name="api_ticket_list"),
    path("api/v1/tickets/<uuid:ticket_id>/",  views.ticket_detail,    name="api_ticket_detail"),

    # ── Modal / analyse helpers ───────────────────────────────────────
    path("api/call/<uuid:call_id>/modal/",    views.call_modal_data,  name="call_modal_data"),
    path("api/call/<uuid:call_id>/analyse/",  views.analyse_call_now, name="analyse_call_now"),

    # ── Service agent ─────────────────────────────────────────────────
    path("call-service-agent/",   views.call_service_agent,     name="call_service_agent"),
    path("api/service-customers/", views.api_service_customers,  name="api_service_customers"),
    path("api/service-call/<str:bolna_call_id>/result/", views.service_call_result, name="service_call_result"),
    path("api/call/<str:bolna_call_id>/analysis-status/", views.api_call_analysis_status, name="api_call_analysis_status"),

    # ── Super Admin — Org Management ─────────────────────────────────
    path("api/org/<uuid:org_id>/approve/",     views.org_approve,    name="org_approve"),
    path("api/org/<uuid:org_id>/set-limits/",  views.org_set_limits, name="org_set_limits"),
    path("org-manage/",                        views.org_manage_view, name="org_manage"),
    path("orgs/create/",                       views.org_create_view, name="org_create"),
    path("tickets/<uuid:ticket_id>/update/",   views.ticket_update_view, name="ticket_update"),

    # ── Admin — User & Team Management ──────────────────────────────
    path("manage/users/",     views.admin_users_view,  name="admin_users"),
    path("manage/teams/",     views.admin_teams_view,  name="admin_teams"),
    path("manage/dashboard/", views.admin_dashboard_view, name="admin_dashboard"),
    path("api/admin/user/create/",                  views.admin_user_create,  name="admin_user_create"),
    path("api/admin/user/<uuid:user_id>/update/",   views.admin_user_update,  name="admin_user_update"),

    # ── Team Management ──────────────────────────────────────────────
    path("api/teams/create/",      views.team_create,      name="team_create"),
    path("api/teams/assign-user/", views.team_assign_user, name="team_assign_user"),
    path("api/teams/",             views.team_list_view,   name="team_list"),

    # ── Numbers management page ──────────────────────────────────────
    path("manage/numbers/", views.numbers_page, name="numbers_page"),

    # ── Utility ──────────────────────────────────────────────────────
    path("test/", views.test_view),
]
