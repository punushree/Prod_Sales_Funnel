"""
template_views.py — AOS Agent Platform
HTML views for the dashboard UI (Django server-side rendering)
"""

import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator

from .models import (
    Organisation, Lead, Customer, CallLog, Ticket
)


# ─────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────

def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    error = None
    if request.method == "POST":
        email    = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=email, password=password)
        if user:
            login(request, user)
            return redirect("dashboard")
        error = "Invalid email or password."

    return render(request, "login.html", {"error": error})


def logout_view(request):
    logout(request)
    return redirect("login")


# ─────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────

@login_required
def dashboard_view(request):
    user = request.user

    if user.role == "super_admin":
        calls   = CallLog.objects.all()
        leads   = Lead.objects.all()
        tickets = Ticket.objects.filter(status="open")
        stats = {
            "total_calls":     calls.count(),
            "total_leads":     leads.count(),
            "open_tickets":    tickets.count(),
            "total_customers": Customer.objects.count(),
            "total_orgs":      Organisation.objects.count(),
        }
        recent_calls = calls.select_related("lead", "customer", "analysis").order_by("-started_at")[:5]
        recent_leads = leads.order_by("-created_at")[:5]
    else:
        # inside the else branch (non-super_admin)
        org = user.org
        quota_data = {
            "call_quota":    org.call_quota,
            "calls_used":    org.calls_used,
            "calls_left":    org.call_quota - org.calls_used,
            "minutes_quota": org.minutes_quota,
            "minutes_used":  org.minutes_used,
            "minutes_left":  org.minutes_quota - org.minutes_used,
            "number_quota":  org.number_quota,
            "numbers_used":  org.allowed_numbers.filter(is_active=True).count(),
            "max_users":     org.max_users,
            "users_count":   org.user_count(),
    }
# pass quota_data into context

    return render(request, "dashboard.html", {
        "stats":        stats,
        "recent_calls": recent_calls,
        "recent_leads": recent_leads,
    })


# ─────────────────────────────────────────────
# ORGANISATIONS  (super admin only)
# ─────────────────────────────────────────────

@login_required
def org_list_view(request):
    if request.user.role != "super_admin":
        return redirect("dashboard")
    orgs = Organisation.objects.all().order_by("-created_at")
    return render(request, "org_list.html", {"orgs": orgs})


@login_required
def org_create_view(request):
    if request.user.role != "super_admin":
        return redirect("dashboard")

    if request.method == "POST":
        p = request.POST
        Organisation.objects.create(
            name                 = p.get("name", "").strip(),
            industry             = p.get("industry", "generic"),
            plan_type            = p.get("plan_type", "trial"),
            call_quota           = int(p.get("call_quota", 100)),
            minutes_quota        = int(p.get("minutes_quota", 300)),
            sales_agent_prompt   = p.get("sales_agent_prompt", ""),
            service_agent_prompt = p.get("service_agent_prompt", ""),
            bolna_agent_id       = p.get("bolna_agent_id", ""),
            phone_number         = p.get("phone_number", ""),
        )
        messages.success(request, "Organisation created successfully.")
    return redirect("org_list")


# ─────────────────────────────────────────────
# LEADS
# ─────────────────────────────────────────────

@login_required
def lead_list_view(request):
    if request.user.role == "super_admin":
        qs = Lead.objects.select_related("org").order_by("-created_at")
    else:
        qs = Lead.objects.filter(org=request.user.org).order_by("-created_at")

    paginator = Paginator(qs, 20)
    leads = paginator.get_page(request.GET.get("page"))
    return render(request, "lead_list.html", {"leads": leads})


@login_required
def lead_create_view(request):
    if request.method == "POST":
        p = request.POST

        # Parse extra_data JSON safely
        extra_raw = p.get("extra_data", "").strip()
        try:
            extra_data = json.loads(extra_raw) if extra_raw else {}
        except json.JSONDecodeError:
            extra_data = {}

        org = request.user.org if request.user.role != "super_admin" else None

        Lead.objects.create(
            org        = org,
            created_by = request.user,
            name       = p.get("name", "").strip(),
            phone      = p.get("phone", "").strip(),
            email      = p.get("email", "").strip(),
            company    = p.get("company", "").strip(),
            notes      = p.get("notes", "").strip(),
            extra_data = extra_data,
        )
        messages.success(request, f"Lead created successfully.")
    return redirect("lead_list")


@login_required
def lead_detail_view(request, lead_id):
    lead = get_object_or_404(Lead, id=lead_id)
    return render(request, "lead_detail.html", {"lead": lead})


@login_required
def lead_update_status_view(request, lead_id):
    lead = get_object_or_404(Lead, id=lead_id)
    if request.method == "POST":
        lead.status = request.POST.get("status", lead.status)
        lead.notes  = request.POST.get("notes", lead.notes)
        lead.save()
        messages.success(request, "Lead updated.")
    return redirect("lead_detail", lead_id=lead_id)


# ─────────────────────────────────────────────
# CUSTOMERS
# ─────────────────────────────────────────────

@login_required
def customer_list_view(request):
    if request.user.role == "super_admin":
        qs = Customer.objects.select_related("org").order_by("-onboarded_at")
    else:
        qs = Customer.objects.filter(org=request.user.org).order_by("-onboarded_at")

    paginator = Paginator(qs, 20)
    customers = paginator.get_page(request.GET.get("page"))
    return render(request, "customer_list.html", {"customers": customers})


# ─────────────────────────────────────────────
# CALLS
# ─────────────────────────────────────────────

@login_required
def call_list_view(request):
    if request.user.role == "super_admin":
        qs = CallLog.objects.select_related("lead", "customer", "analysis").order_by("-started_at")
    else:
        qs = CallLog.objects.filter(org=request.user.org).select_related("lead", "customer", "analysis").order_by("-started_at")

    call_type = request.GET.get("call_type", "").strip().lower()
    if call_type in ("sales", "service"):
        qs = qs.filter(call_type=call_type)

    paginator = Paginator(qs, 20)
    calls = paginator.get_page(request.GET.get("page"))
    return render(request, "call_list.html", {"calls": calls, "active_filter": call_type})


@login_required
def call_detail_view(request, call_id):
    call = get_object_or_404(
        CallLog.objects.select_related("lead", "customer", "analysis", "transcript"),
        id=call_id
    )
    return render(request, "call_detail.html", {"call": call})


# ─────────────────────────────────────────────
# TICKETS
# ─────────────────────────────────────────────

@login_required
def ticket_list_view(request):
    if request.user.role == "super_admin":
        qs = Ticket.objects.select_related("customer", "org").order_by("-created_at")
    else:
        qs = Ticket.objects.filter(org=request.user.org).select_related("customer").order_by("-created_at")

    paginator = Paginator(qs, 20)
    tickets = paginator.get_page(request.GET.get("page"))
    return render(request, "ticket_list.html", {"tickets": tickets})


@login_required
def ticket_update_view(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    if request.method == "POST":
        ticket.status      = request.POST.get("status", ticket.status)
        ticket.assigned_to = request.POST.get("assigned_to", ticket.assigned_to)
        ticket.save()
        messages.success(request, "Ticket updated.")
    return redirect("ticket_list")

@login_required
def call_analysis_view(request, call_id):
    """
    Full KPI analysis dashboard for a single call.
    Shows: 3 KPI scores, lead temperature, transcript tab, ticket tab.
    """
    call = get_object_or_404(
        CallLog.objects.select_related(
            "lead", "customer", "analysis", "transcript", "org"
        ).prefetch_related("tickets__customer"),
        id=call_id
    )
    return render(request, "call_analysis.html", {"call": call})
 