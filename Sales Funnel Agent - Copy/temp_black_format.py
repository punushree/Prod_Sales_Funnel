"""
views.py — AOS Agent Platform  (v2 — multi-tenant spec)

KEY CHANGES vs v1:
1. bolna_initiate: validates phone is in org's AllowedPhoneNumber whitelist
2. bolna_webhook: auto-captures transcript, runs LLM, saves analysis — no manual input
3. analyze_manual_transcript: REMOVED (raises 410 Gone with clear message)
4. customer_list_view: shows auto-analysed call history (ManualAnalysis from webhook)
5. New: allowed_numbers CRUD (add/remove/list phone numbers for an org)
6. New: org_request_form — public form for clients to request an org
7. New: org_request_review — super admin approves/rejects request
8. New: org_admin_create_user — org admin creates users inside own org
9. org_create_view: marks org as pending (is_approved=False) on creation
10. org_approve: activates the org when super admin approves
"""

import json
import logging
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Avg, Sum
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import (
    ManualAnalysis, Organisation, AllowedPhoneNumber, OrgRequest,
    User, Lead, Customer, CallLog, Transcript, CallAnalysis, Ticket, Team,
)
from .serializers import (
    OrganisationSerializer, LeadSerializer, CustomerSerializer,
    CallLogSerializer, TicketSerializer,
)
from .permissions import IsSuperAdmin, IsSameOrg
from .tasks import run_post_call_analysis, auto_hangup_call
from .bolna_service import initiate_call

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════
#  AUTH
# ════════════════════════════════════════════════════════════════════

def login_view(request):
    if request.user.is_authenticated:
        if request.user.role == "super_admin":
            return redirect("admin_dashboard")
        return redirect("dashboard")
    if request.method == "POST":
        email    = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        user     = authenticate(request, username=email, password=password)
        if user:
            login(request, user)
            if user.role == "super_admin":
                return redirect("admin_dashboard")
            return redirect("dashboard")
        return render(request, "login.html", {"error": "Invalid credentials"})
    return render(request, "login.html")


def logout_view(request):
    logout(request)
    return redirect("login")


# ════════════════════════════════════════════════════════════════════
#  DASHBOARD
# ════════════════════════════════════════════════════════════════════

from datetime import timedelta

@login_required
def dashboard_view(request):
    user = request.user
    if user.role == "super_admin":
        return redirect("admin_dashboard")

    # Safe org resolution — never crash if user.org is None
    org = getattr(user, "org", None)
    if org is None:
        org = Organisation.objects.filter(is_active=True, is_approved=True).first()

    if org is None:
        return render(request, "dashboard.html", {
            "stats": {
                "total_calls": 0, "connection_rate": 0,
                "avg_duration_str": "0m 0s", "conversion_rate": 0,
                "sent_pos_pct": 0, "sent_neu_pct": 0, "sent_neg_pct": 0,
                "avg_lead_score": 0, "hot_leads": 0, "warm_leads": 0, "cold_leads": 0,
                "allowed_numbers": 0, "number_quota": 0,
                "calls_used": 0, "call_quota": 0,
                "minutes_used": 0, "minutes_quota": 0,
            },
            "recent_calls": [],
        })

    calls = CallLog.objects.filter(org=org)
    leads = Lead.objects.filter(org=org)

    total_calls = calls.count()
    conn_calls  = calls.filter(status="completed").count()
    conn_rate   = round((conn_calls / total_calls * 100), 1) if total_calls > 0 else 0
    avg_dur     = calls.aggregate(Avg("duration_seconds"))["duration_seconds__avg"] or 0

    analyses = CallAnalysis.objects.filter(call__in=calls)
    total_an = analyses.count() or 1

    sent_pos = round((analyses.filter(sentiment="positive").count() / total_an) * 100)
    sent_neu = round((analyses.filter(sentiment="neutral").count()  / total_an) * 100)
    sent_neg = round((analyses.filter(sentiment="negative").count() / total_an) * 100)

    avg_score = round(analyses.aggregate(s=Avg("extra_kpi__lead_score"))["s"] or 0)
    conv_rate = round((leads.filter(status="converted").count() / (leads.count() or 1)) * 100, 1)

    allowed_count = AllowedPhoneNumber.objects.filter(org=org, is_active=True).count()

    stats = {
        "total_calls":      total_calls,
        "connection_rate":  conn_rate,
        "avg_duration_str": f"{int(avg_dur//60)}m {int(avg_dur%60)}s",
        "conversion_rate":  conv_rate,
        "sent_pos_pct":     sent_pos,
        "sent_neu_pct":     sent_neu,
        "sent_neg_pct":     sent_neg,
        "avg_lead_score":   avg_score,
        "hot_leads":        analyses.filter(extra_kpi__temperature="hot").count(),
        "warm_leads":       analyses.filter(extra_kpi__temperature="warm").count(),
        "cold_leads":       analyses.filter(extra_kpi__temperature="cold").count(),
        "allowed_numbers":  allowed_count,
        "number_quota":     org.number_quota,
        "calls_used":       org.calls_used,
        "call_quota":       org.call_quota,
        "minutes_used":     org.minutes_used,
        "minutes_quota":    org.minutes_quota,
    }

    recent_calls = calls.select_related("lead").order_by("-started_at")[:8]
    return render(request, "dashboard.html", {"stats": stats, "recent_calls": recent_calls})


# ════════════════════════════════════════════════════════════════════
#  CALL LIST / DETAIL
# ════════════════════════════════════════════════════════════════════

@login_required
def call_list_view(request):
    user = request.user
    qs = (
        CallLog.objects.all()
        if user.role == "super_admin"
        else CallLog.objects.filter(org=user.org)
    )
    qs = qs.select_related("lead", "customer", "analysis").order_by("-started_at")
    call_type = request.GET.get("call_type")
    direction = request.GET.get("direction")
    if call_type: qs = qs.filter(call_type=call_type)
    if direction: qs = qs.filter(direction=direction)
    return render(request, "call_list.html", {"calls": qs})


@login_required
def call_detail_view(request, call_id):
    user = request.user
    qs   = CallLog.objects.all() if user.role == "super_admin" else CallLog.objects.filter(org=user.org)
    call = get_object_or_404(
        qs.select_related("lead", "customer", "transcript", "analysis"), id=call_id
    )
    return render(request, "call_detail.html", {"call": call})


# ════════════════════════════════════════════════════════════════════
#  LEAD LIST / DETAIL / CREATE
# ════════════════════════════════════════════════════════════════════

@login_required
def lead_list_view(request):
    user = request.user
    leads = (
        Lead.objects.all() if user.role == "super_admin"
        else Lead.objects.filter(org=user.org)
    ).order_by("-created_at")
    return render(request, "lead_list.html", {"leads": leads})


@login_required
def lead_detail_view(request, lead_id):
    user = request.user
    qs   = Lead.objects.all() if user.role == "super_admin" else Lead.objects.filter(org=user.org)
    lead = get_object_or_404(qs, id=lead_id)
    calls = CallLog.objects.filter(lead=lead).select_related("analysis").order_by("-started_at")
    return render(request, "lead_detail.html", {"lead": lead, "calls": calls})


@login_required
def lead_create_view(request):
    if request.method == "POST":
        name    = request.POST.get("name", "").strip()
        phone   = request.POST.get("phone", "").strip()
        email   = request.POST.get("email", "").strip()
        company = request.POST.get("company", "").strip()
        notes   = request.POST.get("notes", "").strip()

        if not name or not phone:
            return render(request, "lead_create.html", {
                "error": "Name and phone are required.", "post": request.POST,
            })

        # Normalise phone
        clean_phone = phone.strip().replace(" ", "")
        if not clean_phone.startswith("+"):
            clean_phone = "+91" + clean_phone.lstrip("0")

        org = request.user.org if request.user.role != "super_admin" else None

        # ── NUMBER CONTROL: check whitelist ──────────────────────────
        if org:
            if not org.is_number_allowed(clean_phone):
                # Auto-add if quota allows, otherwise block
                if not org.number_quota_exceeded():
                    AllowedPhoneNumber.objects.get_or_create(
                        org=org, phone=clean_phone,
                        defaults={"label": name, "added_by": request.user.name}
                    )
                else:
                    return render(request, "lead_create.html", {
                        "error": (
                            f"Number quota reached ({org.number_quota}). "
                            "Remove an existing number before adding a new one."
                        ),
                        "post": request.POST,
                    })

        Lead.objects.create(
            org=org, created_by=request.user,
            name=name, phone=clean_phone,
            email=email, company=company, notes=notes,
        )
        return redirect("lead_list")

    return render(request, "lead_create.html")


# ════════════════════════════════════════════════════════════════════
#  BOLNA INITIATE  — number whitelist enforced
# ════════════════════════════════════════════════════════════════════

@csrf_exempt
@require_POST
def bolna_initiate(request):
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"success": False, "error": "Invalid JSON body"}, status=400)

    phone = data.get("phone", "").strip().replace(" ", "")
    if not phone:
        return JsonResponse({"success": False, "error": "phone is required"}, status=400)

    # Normalise to E.164
    if not phone.startswith("+"):
        phone = "+91" + phone.lstrip("0")

    org = None
    if request.user.is_authenticated:
        org = request.user.org
    elif data.get("org_id"):
        try:
            org = Organisation.objects.get(id=data["org_id"])
        except Organisation.DoesNotExist:
            return JsonResponse({"success": False, "error": "org not found"}, status=404)

    if org is None:
        return JsonResponse({"success": False, "error": "Cannot determine org — please log in"}, status=403)

    # ── APPROVAL CHECK ─────────────────────────────────────────────
    if not org.is_approved:
        return JsonResponse({"success": False, "error": "Organisation is pending approval"}, status=403)

    # ── CALL QUOTA ─────────────────────────────────────────────────
    if org.quota_exceeded():
        return JsonResponse({"success": False, "error": "Call quota exceeded for this organisation"}, status=403)

    # ── NUMBER WHITELIST ───────────────────────────────────────────
    if not org.is_number_allowed(phone):
        return JsonResponse({
            "success": False,
            "error": (
                f"Number {phone} is not in the allowed list for this organisation. "
                "Please add it first via the Numbers management page."
            )
        }, status=403)

    # Agent ID read exclusively from .env / Django settings (BOLNA_SERVICE_AGENT_ID)
    agent_id = (getattr(dj_settings, "BOLNA_SERVICE_AGENT_ID", "") or "").strip()
    if not agent_id:
        return JsonResponse({"success": False, "error": "No Bolna agent configured — set BOLNA_SERVICE_AGENT_ID in .env"}, status=400)

    agent_type = data.get("agent_type", "sales")
    prompt_var = (
        org.sales_agent_prompt if agent_type == "sales" else org.service_agent_prompt
    )

    extra_vars = data.get("extra_vars") or {}
    if prompt_var:
        extra_vars["system_prompt"] = prompt_var

    result = initiate_call(
        agent_id   = agent_id,
        phone      = phone,
        name       = data.get("name"),
        extra_vars = extra_vars or None,
    )

    if not result["success"]:
        logger.error("bolna_initiate failed: %s", result["error"])
        return JsonResponse({"success": False, "error": result["error"]}, status=502)

    bolna_call_id = result["call_id"] or ""
    lead_id       = data.get("lead_id")
    call = CallLog.objects.create(
        org           = org,
        lead_id       = lead_id if lead_id else None,
        call_type     = agent_type,
        direction     = "outbound",
        bolna_call_id = bolna_call_id,
        contact_phone = phone,
        contact_name  = (data.get("name") or "").strip(),
        started_at    = timezone.now(),
        status        = "in_progress",
    )

    # ── FIX: Schedule auto hang-up if org has a call time limit ───────
    call_limit = getattr(org, "call_limit_seconds", 120)
    if call_limit and call_limit > 0 and bolna_call_id:
        try:
            auto_hangup_call.apply_async(
                args=[bolna_call_id, str(call.id), call_limit],
                countdown=call_limit,
            )
            logger.info(
                "Auto-hangup scheduled for call %s in %ds (bolna_id=%s)",
                call.id, call_limit, bolna_call_id,
            )
        except Exception as _he:
            logger.warning("Could not schedule auto-hangup: %s", _he)

    logger.info("CallLog created: %s — Bolna call_id: %s", call.id, bolna_call_id)
    return JsonResponse({"success": True, "call_id": bolna_call_id, "log_id": str(call.id)})


# ════════════════════════════════════════════════════════════════════
#  BOLNA WEBHOOK — auto transcript + auto LLM analysis
# ════════════════════════════════════════════════════════════════════

@csrf_exempt
@require_POST
def bolna_webhook(request):
    """
    Active Bolna webhook — receives call completion data.
    Flow:
      1. Parse incoming data
      2. Find/create CallLog + save Transcript
      3. Pass transcript to analyze_feedback (Groq LLM)
      4. Save result to ManualAnalysis + CallAnalysis
      5. Dashboard updates automatically — NO manual input needed
    """
    payload = json.loads(request.body)
    
    # ADD THIS — logs every key Bolna sends
    import logging
    logger = logging.getLogger(__name__)
    logger.warning("BOLNA WEBHOOK FULL PAYLOAD: %s", json.dumps(payload, indent=2))
    
    try:
        data = json.loads(request.body)
        logger.info("🔥 WEBHOOK DATA keys: %s", list(data.keys()))

        status_val    = data.get("status", "")
        transcript    = (data.get("transcript") or "").strip()
        bolna_call_id = (data.get("call_id") or data.get("id") or data.get("execution_id") or data.get("run_id") or "")

        # Ignore intermediate events without transcript
        if status_val != "completed" and not transcript:
            logger.info("⏳ Skipping non-final status: %s", status_val)
            return JsonResponse({"status": "ignored"})

        # ── 1. Find or create CallLog ───────────────────────────────
        call = None
        if bolna_call_id:
            call = CallLog.objects.filter(bolna_call_id=bolna_call_id).first()

        if call is None:
            default_org = Organisation.objects.filter(is_active=True, is_approved=True).first()
            if not default_org:
                return JsonResponse({"error": "No active approved org found"}, status=500)
            call = CallLog.objects.create(
                org           = default_org,
                call_type     = "service",
                direction     = "outbound",
                bolna_call_id = bolna_call_id,
                started_at    = timezone.now(),
            )

        # ── FIX: Robust duration extraction (tries all known Bolna field names) ──
        duration = 0
        for _dur_key in ("duration_seconds", "duration", "call_duration",
                         "total_duration", "talk_time", "billable_duration"):
            _v = data.get(_dur_key)
            if _v:
                try:
                    duration = int(float(_v))
                    break
                except (ValueError, TypeError):
                    continue
        # Also try nested meta
        if not duration:
            _meta = data.get("meta") or {}
            for _dur_key in ("duration_seconds", "duration", "call_duration"):
                _v = _meta.get(_dur_key)
                if _v:
                    try:
                        duration = int(float(_v))
                        break
                    except (ValueError, TypeError):
                        continue
        # Bolna sometimes sends milliseconds — convert if > 10 hours in seconds
        if duration > 36000:
            duration = duration // 1000

        # ── REAL FIX: If Bolna sent 0 or nothing, compute from started_at ──
        # This is the most reliable source — we stamped started_at ourselves.
        if duration <= 0 and call.started_at:
            computed = int((timezone.now() - call.started_at).total_seconds())
            if computed > 0:
                duration = computed
                logger.info("Webhook: computed duration from timestamps = %ds (call %s)", duration, bolna_call_id)

        # ── FIX: Normalise status (Bolna uses 'ended', 'success', etc.) ──
        _COMPLETED = {"completed", "ended", "success", "done"}
        _FAILED    = {"failed", "error", "cancelled"}
        _NO_ANSWER = {"no-answer", "no_answer", "busy", "unanswered"}
        if status_val in _COMPLETED:
            resolved_status = "completed"
        elif status_val in _FAILED:
            resolved_status = "failed"
        elif status_val in _NO_ANSWER:
            resolved_status = "no_answer"
        else:
            resolved_status = "completed"  # treat unknown as completed when webhook fires

        call.status   = resolved_status
        call.ended_at = timezone.now()   # always stamp end time precisely
        if duration > 0:
            call.duration_seconds = duration

        # ── FIX: Re-classify call_type based on transcript keywords ──
        # A "service" call that mentions buying/new property → reclassify as "sales"
        _SALES_KEYWORDS = [
            "new property", "new flat", "buy", "purchase", "new home",
            "invest", "looking for", "new apartment", "interested in buying",
            "want to buy", "want to purchase", "new house", "new project",
            "book a flat", "book flat", "property inquiry", "property enquiry",
            "new villa", "new plot", "new office", "buy office",
        ]
        if call.call_type == "service" and transcript:
            _tl = transcript.lower()
            if any(kw in _tl for kw in _SALES_KEYWORDS):
                call.call_type = "sales"
                logger.info("Webhook: re-classified call %s service→sales (transcript keywords)", bolna_call_id)

        call.save(update_fields=["duration_seconds", "status", "ended_at", "call_type"])

        # ── 2. Save transcript turns ────────────────────────────────
        turns_raw = data.get("transcript_json", [])
        turns = [
            {
                "speaker":   "agent" if t.get("role") == "agent" else "customer",
                "text":      t.get("content", ""),
                "timestamp": t.get("timestamp", 0),
            }
            for t in turns_raw
        ]
        if transcript or turns:
            Transcript.objects.update_or_create(
                call     = call,
                defaults = {"full_text": transcript, "turns": turns},
            )

        # Update org quota counters
        org = call.org
        if org:
            org.calls_used   += 1
            org.minutes_used += duration // 60
            org.save(update_fields=["calls_used", "minutes_used"])

        if not transcript:
            logger.warning("⚠ No transcript text — skipping LLM analysis")
            try:
                run_post_call_analysis.delay(str(call.id))
            except Exception as celery_err:
                logger.warning("[WEBHOOK] Celery unavailable (no transcript path) — skipping async task: %s", celery_err)
            return JsonResponse({"status": "saved_no_transcript", "call_id": str(call.id)})

        # ── 3. LLM Analysis via feedback agent ─────────────────────
        from .feedback import analyze_feedback
        logger.info("🧠 Running analyze_feedback...")
        dashboard_data = analyze_feedback(transcript)
        routing_type   = dashboard_data.get("routing_type", "sales_only")
        lead_name = (
            data.get("customer_name", "")
            or data.get("variables", {}).get("customer_name", "")
            or getattr(call, "contact_name", "")   # ← stored when call was initiated
            or ""
        ).strip()
        if not lead_name or lead_name.lower() in ("customer", "unknown", "test", "n/a"):
            # Last resort: try lead/customer FK
            if getattr(call, "lead_id", None) and call.lead:
                lead_name = call.lead.name or lead_name
            elif getattr(call, "customer_id", None) and call.customer:
                lead_name = call.customer.name or lead_name
        if not lead_name or lead_name.lower() in ("customer", "unknown", ""):
            lead_name = call.contact_phone or "Customer"

        # ── 4. Save to ManualAnalysis (auto) ────────────────────────
        _save_analysis_record(org, call, lead_name, transcript, dashboard_data, routing_type)

        # ── 5. Also run Celery post-call pipeline (CallAnalysis table)
        try:
            run_post_call_analysis.delay(str(call.id))
        except Exception as celery_err:
            logger.warning("[WEBHOOK] Celery unavailable — skipping async task: %s", celery_err)

        logger.info("[WEBHOOK] Done call=%s routing=%s", call.id, routing_type)
        return JsonResponse({
            "status":   "saved",
            "call_id":  str(call.id),
            "analysis": dashboard_data,
        })

    except Exception as e:
        logger.exception("bolna_webhook error")
        return JsonResponse({"error": str(e)}, status=500)


def _safe_ma_create(**kwargs):
    """Create ManualAnalysis — drops routing_type/call if migration 0020 not applied."""
    rt = kwargs.get("routing_type", "")
    if rt and isinstance(kwargs.get("extra_data"), dict):
        kwargs["extra_data"].setdefault("routing_type", rt)
    try:
        return ManualAnalysis.objects.create(**kwargs)
    except Exception as exc:
        err = str(exc).lower()
        if "routing_type" in err or "unexpected keyword" in err or "call" in err:
            safe = {k: v for k, v in kwargs.items() if k not in ("routing_type", "call")}
            try:
                return ManualAnalysis.objects.create(**safe)
            except Exception:
                core = {"org", "lead_name", "transcript", "sentiment", "lead_score", "temperature", "call_type", "extra_data"}
                return ManualAnalysis.objects.create(**{k: v for k, v in safe.items() if k in core})
        raise


def _save_analysis_record(org, call, lead_name, transcript, dashboard_data, routing_type):
    """Helper: save structured LLM result to ManualAnalysis."""
    if routing_type == "service_then_sales":
        pos        = dashboard_data.get("pos", 50)
        sentiment  = "positive" if pos >= 60 else "neutral" if pos >= 35 else "negative"
        f1 = dashboard_data.get("f1", 0)
        f2 = dashboard_data.get("f2", 0)
        f3 = dashboard_data.get("f3", 0)
        lead_score = round((f1 * 0.40) + (f2 * 0.35) + (f3 * 0.25))
        _safe_ma_create(
            org=org, call=call, lead_name=lead_name, transcript=transcript,
            sentiment=sentiment, lead_score=lead_score,
            temperature=dashboard_data.get("issue_severity", "Medium"),
            call_type="service", routing_type="service_then_sales",
            extra_data={
                "routing_type": "service_then_sales",
                "satisfaction":       dashboard_data.get("satisfaction", 50),
                "issue_severity":     dashboard_data.get("issue_severity", "Medium"),
                "resolution_urgency": dashboard_data.get("resolution_urgency", "Medium"),
                "loyalty_risk":       dashboard_data.get("loyalty_risk", "Medium"),
                "issue_category":     dashboard_data.get("issue_category", "query"),
                "engagement": f1, "fit": f2, "conversion": f3, "lead_score": lead_score,
            },
        )
    elif routing_type == "service_only":
        satisfaction = dashboard_data.get("satisfaction", 50)
        pos          = dashboard_data.get("pos", 50)
        sentiment    = "positive" if pos >= 60 else "neutral" if pos >= 35 else "negative"
        _safe_ma_create(
            org=org, call=call, lead_name=lead_name, transcript=transcript,
            sentiment=sentiment, lead_score=satisfaction,
            temperature=dashboard_data.get("issue_severity", "Medium"),
            call_type="service", routing_type="service_only",
            extra_data={
                "routing_type": "service_only",
                "satisfaction":       satisfaction,
                "issue_severity":     dashboard_data.get("issue_severity", "Medium"),
                "resolution_urgency": dashboard_data.get("resolution_urgency", "Medium"),
                "loyalty_risk":       dashboard_data.get("loyalty_risk", "Medium"),
                "issue_category":     dashboard_data.get("issue_category", "query"),
            },
        )
    else:
        f1 = dashboard_data.get("f1", 0)
        f2 = dashboard_data.get("f2", 0)
        f3 = dashboard_data.get("f3", 0)
        lead_score = round((f1 * 0.40) + (f2 * 0.35) + (f3 * 0.25))
        pos        = dashboard_data.get("pos", 50)
        sentiment  = "positive" if pos >= 60 else "neutral" if pos >= 35 else "negative"
        temp_str   = "Hot" if dashboard_data.get("is_hot") else "Warm" if dashboard_data.get("is_warm") else "Cold"
        _safe_ma_create(
            org=org, call=call, lead_name=lead_name, transcript=transcript,
            sentiment=sentiment, lead_score=lead_score,
            temperature=temp_str, call_type="sales", routing_type="sales_only",
            extra_data={"routing_type": "sales_only", "engagement": f1, "fit": f2, "conversion": f3},
        )


# ════════════════════════════════════════════════════════════════════
#  MANUAL TRANSCRIPT — REMOVED (spec says no manual input)
# ════════════════════════════════════════════════════════════════════

@csrf_exempt
def analyze_manual_transcript(request):
    """
    DEPRECATED — Manual transcript input has been removed per system spec.
    All transcripts are now captured automatically via the Bolna webhook.
    """
    return JsonResponse({
        "error": (
            "Manual transcript input has been removed. "
            "Transcripts are now captured automatically when a call ends via Bolna webhook. "
            "Trigger a call via the Call button — analysis will appear automatically."
        ),
        "removed": True,
    }, status=410)


# ════════════════════════════════════════════════════════════════════
#  CUSTOMER / TRANSCRIPT ANALYSIS PAGE
# ════════════════════════════════════════════════════════════════════

@login_required
def customer_list_view(request):
    """Renders the Transcript Analysis / Call Insights page.
    Shows history from ManualAnalysis (auto-populated by webhook)."""
    org = request.user.org

    base_qs = ManualAnalysis.objects.all() if request.user.role == "super_admin" \
              else ManualAnalysis.objects.filter(org=org)

    sales_qs = base_qs.filter(call_type="sales").order_by("-created_at")[:20]
    sales_history = []
    for m in sales_qs:
        sales_history.append({
            "name":        m.lead_name or "Customer",
            "lead_score":  m.lead_score,
            "temperature": m.temperature,
            "sentiment":   m.sentiment,
            "extra_data":  m.extra_data or {},
        })
    leads_history = [h for h in sales_history if h["lead_score"] >= 60]

    service_qs = base_qs.filter(call_type="service").order_by("-created_at")[:20]
    service_history = []
    for m in service_qs:
        ed = m.extra_data or {}
        service_history.append({
            "name":               m.lead_name or "Customer",
            "satisfaction":       ed.get("satisfaction", m.lead_score),
            "issue_severity":     ed.get("issue_severity", "Medium"),
            "resolution_urgency": ed.get("resolution_urgency", "Medium"),
            "loyalty_risk":       ed.get("loyalty_risk", "Medium"),
            "issue_category":     ed.get("issue_category", "query"),
            "sentiment":          m.sentiment,
        })

    try:
        mixed_qs = list(base_qs.filter(routing_type="service_then_sales").order_by("-created_at")[:20])
    except Exception:
        _all = list(base_qs.filter(call_type="service").order_by("-created_at")[:60])
        mixed_qs = [m for m in _all if (m.extra_data or {}).get("routing_type") == "service_then_sales"][:20]
    mixed_history = []
    for m in mixed_qs:
        ed = m.extra_data or {}
        mixed_history.append({
            "name":          m.lead_name or "Customer",
            "satisfaction":  ed.get("satisfaction", 50),
            "issue_severity":ed.get("issue_severity", "Medium"),
            "lead_score":    ed.get("lead_score", m.lead_score),
            "sentiment":     m.sentiment,
            "routing_type":  "service_then_sales",
        })

    customers_qs = Customer.objects.filter(org=org).order_by("name") if org else Customer.objects.all().order_by("name")
    service_customers = [
        {"id": str(c.id), "name": c.name, "phone": c.phone,
         "tag": (c.extra_data or {}).get("tag", "untagged")}
        for c in customers_qs
    ]

    # Allowed numbers for the org
    allowed_numbers = []
    if org:
        allowed_numbers = list(
            AllowedPhoneNumber.objects.filter(org=org, is_active=True)
            .values("id", "phone", "label", "added_at")
            .order_by("-added_at")
        )

    return render(request, "customer.html", {
        "call_history":      sales_history,
        "leads_history":     leads_history,
        "service_history":   service_history,
        "mixed_history":     mixed_history,
        "service_customers": service_customers,
        "allowed_numbers":   allowed_numbers,
        "number_quota":      org.number_quota if org else 0,
        "manual_input_removed": True,  # tells template not to show manual form
    })


# ════════════════════════════════════════════════════════════════════
#  ALLOWED PHONE NUMBERS (per-org whitelist management)
# ════════════════════════════════════════════════════════════════════

@csrf_exempt
@login_required
def allowed_numbers_list(request):
    """GET — list allowed numbers for current org (or all if super_admin)."""
    user = request.user
    org  = getattr(user, "org", None)

    if getattr(user, "role", "") == "super_admin":
        org_id = request.GET.get("org_id")
        if org_id:
            qs = AllowedPhoneNumber.objects.filter(org_id=org_id, is_active=True).order_by("-added_at")
        else:
            qs = AllowedPhoneNumber.objects.filter(is_active=True).select_related("org").order_by("-added_at")
    elif org:
        qs = AllowedPhoneNumber.objects.filter(org=org, is_active=True).order_by("-added_at")
    else:
        qs = AllowedPhoneNumber.objects.none()

    quota = org.number_quota if org else (
        Organisation.objects.filter(is_active=True, is_approved=True).first()
    )
    quota = quota.number_quota if hasattr(quota, "number_quota") else (quota or 0)

    data = [
        {
            "id":       str(n.id),
            "phone":    n.phone,
            "label":    n.label,
            "added_by": n.added_by,
            "added_at": n.added_at.isoformat(),
            "org":      n.org.name if getattr(user, "role", "") == "super_admin" else None,
        }
        for n in qs
    ]
    return JsonResponse({"numbers": data, "count": len(data), "quota": quota})


@csrf_exempt
@login_required
def allowed_numbers_add(request):
    """POST — add a phone number to org's allowed list."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    phone = ""; label = ""; org_id_from_body = ""
    try:
        body             = json.loads(request.body)
        phone            = body.get("phone", "").strip().replace(" ", "")
        label            = body.get("label", "").strip()
        org_id_from_body = body.get("org_id", "").strip()
    except Exception:
        phone            = request.POST.get("phone", "").strip().replace(" ", "")
        label            = request.POST.get("label", "").strip()
        org_id_from_body = request.POST.get("org_id", "").strip()

    if not phone:
        return JsonResponse({"error": "phone is required"}, status=400)

    phone = phone.replace("-", "").replace(" ", "")
    if not phone.startswith("+"):
        phone = "+91" + phone.lstrip("0")

    user = request.user
    org  = None

    if org_id_from_body:
        try:
            org = Organisation.objects.get(id=org_id_from_body)
        except Exception:
            org = None

    if org is None:
        org = getattr(user, "org", None)

    if org is None:
        org = Organisation.objects.filter(is_active=True, is_approved=True).first()

    if org is None:
        return JsonResponse({"error": "No active organisation found. Create and approve one first."}, status=400)

    if org.number_quota_exceeded():
        return JsonResponse({
            "error": f"Number quota reached ({org.number_quota}). Remove a number to add a new one."
        }, status=403)

    try:
        added_by = user.name
    except Exception:
        added_by = str(user)

    entry, created = AllowedPhoneNumber.objects.get_or_create(
        org=org, phone=phone,
        defaults={"label": label, "added_by": added_by, "is_active": True}
    )
    if not created:
        entry.is_active = True
        entry.label     = label or entry.label
        entry.save(update_fields=["is_active", "label"])

    return JsonResponse({
        "success": True,
        "id":      str(entry.id),
        "phone":   entry.phone,
        "label":   entry.label,
        "org":     org.name,
        "message": f"Number {phone} added to {org.name}",
    })


@csrf_exempt
@login_required
def allowed_numbers_remove(request, number_id):
    """POST/DELETE — deactivate a phone number from org's allowed list."""
    user = request.user
    try:
        entry = AllowedPhoneNumber.objects.get(id=number_id)
    except AllowedPhoneNumber.DoesNotExist:
        return JsonResponse({"error": "Number not found"}, status=404)

    # Permission check
    if user.role != "super_admin" and entry.org != user.org:
        return JsonResponse({"error": "Forbidden"}, status=403)

    entry.is_active = False
    entry.save(update_fields=["is_active"])
    return JsonResponse({"success": True, "message": f"Number {entry.phone} removed"})


# ════════════════════════════════════════════════════════════════════
#  ORG REQUEST FORM (public — for clients to request org creation)
# ════════════════════════════════════════════════════════════════════

def org_request_form(request):
    """Public form: client fills details → super admin reviews."""
    if request.method == "POST":
        p = request.POST
        OrgRequest.objects.create(
            contact_name  = p.get("contact_name", "").strip(),
            contact_email = p.get("contact_email", "").strip(),
            contact_phone = p.get("contact_phone", "").strip(),
            admin_email   = p.get("contact_email", "").strip(),
            org_name      = p.get("org_name", "").strip(),
            industry      = p.get("industry", "generic"),
            plan_type     = p.get("plan_type", "trial"),
            call_quota    = int(p.get("call_quota", 100) or 100),
            minutes_quota = int(p.get("minutes_quota", 300) or 300),
            number_quota  = int(p.get("number_quota", 4) or 4),
            notes         = p.get("notes", "").strip(),
        )
        return render(request, "org_request_success.html", {})

    return render(request, "org_request_form.html", {
        "industry_choices": [
            ("real_estate", "Real Estate"),
            ("healthcare",  "Healthcare"),
            ("cosmetics",   "Cosmetics / Retail"),
            ("apartment",   "Apartment / Property Management"),
            ("generic",     "Generic"),
        ]
    })


@csrf_exempt
@login_required
def org_request_review(request, request_id):
    """POST — super admin approves or rejects an org request."""
    if request.user.role != "super_admin":
        return JsonResponse({"error": "Forbidden"}, status=403)

    try:
        org_req = OrgRequest.objects.get(id=request_id)
    except OrgRequest.DoesNotExist:
        return JsonResponse({"error": "Request not found"}, status=404)

    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    data   = json.loads(request.body) if request.body else {}
    action = data.get("action", "approve")

    if action == "approve":
        AUTO_PASSWORD = "Test@123"

        # Create the Organisation
        org = Organisation.objects.create(
            name          = org_req.org_name,
            industry      = org_req.industry,
            plan_type     = org_req.plan_type,
            call_quota    = org_req.call_quota,
            minutes_quota = org_req.minutes_quota,
            number_quota  = org_req.number_quota,
            is_approved   = True,
            is_active     = True,
            approved_by   = request.user.name,
            approved_at   = timezone.now(),
        )

        # Create org admin user with fixed password
        # admin_email field is optional on the form — contact_email is always filled
        # so use contact_email as the login email when admin_email is blank
        admin_email = (org_req.admin_email or org_req.contact_email or "").strip()
        if admin_email and not User.objects.filter(email=admin_email).exists():
            User.objects.create_user(
                email    = admin_email,
                password = AUTO_PASSWORD,
                name     = org_req.contact_name,
                role     = "org_admin",
                org      = org,
            )

        # Save password on OrgRequest for reference
        org_req.generated_password = AUTO_PASSWORD
        org_req.status      = "approved"
        org_req.reviewed_by = request.user.name
        org_req.reviewed_at = timezone.now()
        org_req.org         = org
        org_req.save()

        return JsonResponse({
            "success":            True,
            "org_id":             str(org.id),
            "message":            f"Org '{org.name}' created and approved",
            "admin_email":        admin_email,
            "generated_password": AUTO_PASSWORD,
        })
    elif action == "reject":
        org_req.status       = "rejected"
        org_req.reviewed_by  = request.user.name
        org_req.reviewed_at  = timezone.now()
        org_req.review_notes = data.get("notes", "")
        org_req.save()
        return JsonResponse({"success": True, "message": "Request rejected"})
    else:
        return JsonResponse({"error": "Invalid action"}, status=400)


# ════════════════════════════════════════════════════════════════════
#  UTILITY
# ════════════════════════════════════════════════════════════════════

def test_view(request):
    return JsonResponse({"message": "API working"})


# ════════════════════════════════════════════════════════════════════
#  REST API — Organisations
# ════════════════════════════════════════════════════════════════════

@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def org_list(request):
    if request.method == "GET":
        orgs = Organisation.objects.all().order_by("-created_at")
        return Response(OrganisationSerializer(orgs, many=True).data)
    serializer = OrganisationSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=400)


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def org_detail(request, org_id):
    try:
        org = Organisation.objects.get(id=org_id)
    except Organisation.DoesNotExist:
        return Response({"error": "Not found"}, status=404)
    if request.method == "GET":
        return Response(OrganisationSerializer(org).data)
    serializer = OrganisationSerializer(org, data=request.data, partial=True)
    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data)
    return Response(serializer.errors, status=400)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def org_stats(request, org_id):
    try:
        org = Organisation.objects.get(id=org_id)
    except Organisation.DoesNotExist:
        return Response({"error": "Not found"}, status=404)
    return Response({
        "calls_used":        org.calls_used,
        "call_quota":        org.call_quota,
        "calls_remaining":   org.call_quota - org.calls_used,
        "minutes_used":      org.minutes_used,
        "minutes_quota":     org.minutes_quota,
        "minutes_remaining": org.minutes_quota - org.minutes_used,
        "numbers_used":      org.allowed_numbers.filter(is_active=True).count(),
        "number_quota":      org.number_quota,
        "quota_exceeded":    org.quota_exceeded(),
    })


# ════════════════════════════════════════════════════════════════════
#  REST API — Leads
# ════════════════════════════════════════════════════════════════════

@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, IsSameOrg])
def lead_list(request):
    user     = request.user
    is_super = user.role == "super_admin"
    if request.method == "GET":
        leads = (
            Lead.objects.all() if is_super
            else Lead.objects.filter(org=user.org)
        ).order_by("-created_at")
        return Response(LeadSerializer(leads, many=True).data)
    data = request.data.copy()
    if not is_super:
        data["org"] = str(user.org.id)
    data["created_by"] = str(user.id)
    serializer = LeadSerializer(data=data)
    if serializer.is_valid():
        return Response(LeadSerializer(serializer.save()).data, status=201)
    return Response(serializer.errors, status=400)


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated, IsSameOrg])
def lead_detail(request, lead_id):
    try:
        lead = Lead.objects.get(id=lead_id) if request.user.role == "super_admin" \
               else Lead.objects.get(id=lead_id, org=request.user.org)
    except Lead.DoesNotExist:
        return Response({"error": "Not found"}, status=404)
    if request.method == "GET":
        data = LeadSerializer(lead).data
        data["call_history"] = CallLogSerializer(
            CallLog.objects.filter(lead=lead).order_by("-started_at"), many=True
        ).data
        return Response(data)
    serializer = LeadSerializer(lead, data=request.data, partial=True)
    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data)
    return Response(serializer.errors, status=400)


# ════════════════════════════════════════════════════════════════════
#  REST API — Customers
# ════════════════════════════════════════════════════════════════════

@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, IsSameOrg])
def customer_list(request):
    user     = request.user
    is_super = user.role == "super_admin"
    if request.method == "GET":
        customers = (
            Customer.objects.all() if is_super
            else Customer.objects.filter(org=user.org)
        ).order_by("-onboarded_at")
        return Response(CustomerSerializer(customers, many=True).data)
    data = request.data.copy()
    if not is_super:
        data["org"] = str(user.org.id)
    serializer = CustomerSerializer(data=data)
    if serializer.is_valid():
        return Response(CustomerSerializer(serializer.save()).data, status=201)
    return Response(serializer.errors, status=400)


@api_view(["GET"])
def customer_lookup(request):
    phone = request.GET.get("phone", "").strip()
    if not phone:
        return Response({"error": "phone required"}, status=400)
    customer = Customer.objects.filter(phone=phone).first()
    if customer:
        return Response({
            "found": True,
            "customer": {
                "id": str(customer.id), "name": customer.name,
                "phone": customer.phone, "extra_data": customer.extra_data,
                "org_id": str(customer.org.id)
            }
        })
    return Response({"found": False, "message": "new customer"})


# ════════════════════════════════════════════════════════════════════
#  REST API — Calls
# ════════════════════════════════════════════════════════════════════

@api_view(["POST"])
def call_start(request):
    d = request.data
    try:
        org = Organisation.objects.get(id=d["org_id"])
    except Organisation.DoesNotExist:
        return Response({"error": "Org not found"}, status=404)
    if org.quota_exceeded():
        return Response({"error": "Call quota exceeded"}, status=403)
    call = CallLog.objects.create(
        org_id=d["org_id"], lead_id=d.get("lead_id"),
        customer_id=d.get("customer_id"),
        call_type=d.get("call_type", "sales"),
        direction=d.get("direction", "outbound"),
        bolna_call_id=d.get("bolna_call_id", ""),
        started_at=d.get("started_at", timezone.now()),
        status="in_progress",
    )
    return Response({"call_id": str(call.id)}, status=201)


@api_view(["POST"])
def call_end(request, call_id):
    try:
        call = CallLog.objects.get(id=call_id)
    except CallLog.DoesNotExist:
        return Response({"error": "Call not found"}, status=404)
    d = request.data
    call.duration_seconds = d.get("duration_seconds", 0)
    call.status           = d.get("status", "completed")
    call.ended_at         = d.get("ended_at", timezone.now())
    call.save()
    transcript_data = d.get("transcript", {})
    if transcript_data:
        Transcript.objects.create(
            call=call,
            full_text=transcript_data.get("full_text", ""),
            turns=transcript_data.get("turns", []),
        )
    org = call.org
    org.calls_used   += 1
    org.minutes_used += call.duration_seconds // 60
    org.save(update_fields=["calls_used", "minutes_used"])
    try:
        run_post_call_analysis.delay(str(call.id))
    except Exception as _ce:
        logger.warning("[ANALYSIS] Celery unavailable — skipping: %s", _ce)
    return Response({"status": "saved", "call_id": str(call.id)})


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsSameOrg])
def call_list(request):
    user = request.user
    qs = (
        CallLog.objects.all() if user.role == "super_admin"
        else CallLog.objects.filter(org=user.org)
    ).order_by("-started_at")
    if ct := request.query_params.get("call_type"): qs = qs.filter(call_type=ct)
    if d  := request.query_params.get("direction"):  qs = qs.filter(direction=d)
    return Response(CallLogSerializer(qs, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsSameOrg])
def call_detail(request, call_id):
    try:
        call = CallLog.objects.get(id=call_id) if request.user.role == "super_admin" \
               else CallLog.objects.get(id=call_id, org=request.user.org)
    except CallLog.DoesNotExist:
        return Response({"error": "Not found"}, status=404)
    return Response(CallLogSerializer(call).data)


# ════════════════════════════════════════════════════════════════════
#  REST API — Tickets
# ════════════════════════════════════════════════════════════════════

@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, IsSameOrg])
def ticket_list(request):
    user     = request.user
    is_super = user.role == "super_admin"
    if request.method == "GET":
        qs = (
            Ticket.objects.all() if is_super
            else Ticket.objects.filter(org=user.org)
        ).order_by("-created_at")
        if sf := request.query_params.get("status"): qs = qs.filter(status=sf)
        return Response(TicketSerializer(qs, many=True).data)
    data = request.data.copy()
    if not is_super:
        data["org"] = str(user.org.id)
    serializer = TicketSerializer(data=data)
    if serializer.is_valid():
        return Response(TicketSerializer(serializer.save()).data, status=201)
    return Response(serializer.errors, status=400)


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated, IsSameOrg])
def ticket_detail(request, ticket_id):
    try:
        ticket = Ticket.objects.get(id=ticket_id) if request.user.role == "super_admin" \
                 else Ticket.objects.get(id=ticket_id, org=request.user.org)
    except Ticket.DoesNotExist:
        return Response({"error": "Not found"}, status=404)
    if request.method == "GET":
        return Response(TicketSerializer(ticket).data)
    serializer = TicketSerializer(ticket, data=request.data, partial=True)
    if serializer.is_valid():
        if request.data.get("status") == "resolved" and not ticket.resolved_at:
            serializer.save(resolved_at=timezone.now())
        else:
            serializer.save()
        return Response(serializer.data)
    return Response(serializer.errors, status=400)


# ════════════════════════════════════════════════════════════════════
#  CALL MODAL DATA
# ════════════════════════════════════════════════════════════════════

@login_required
@require_GET
def call_modal_data(request, call_id):
    user = request.user
    qs   = (
        CallLog.objects.all() if user.role == "super_admin"
        else CallLog.objects.filter(org=user.org)
    )
    call = get_object_or_404(
        qs.select_related("lead", "customer", "transcript", "analysis"), id=call_id
    )
    contact_name = call.lead.name if call.lead else call.customer.name if call.customer else "Unknown"

    transcript_data = {"full_text": "", "turns": []}
    try:
        t = call.transcript
        transcript_data = {"full_text": t.full_text, "turns": t.turns or []}
    except Exception:
        pass

    analysis_data = {}
    analysed = False
    try:
        a = call.analysis
        analysis_data = {
            "sentiment": a.sentiment, "keywords": a.keywords or [],
            "summary": a.summary, "conversion_probability": a.conversion_probability,
            "extra_kpi": a.extra_kpi or {},
        }
        analysed = True
    except Exception:
        pass

    return JsonResponse({
        "call_id":          str(call.id),
        "contact_name":     contact_name,
        "call_type":        call.call_type,
        "duration_seconds": call.duration_seconds,
        "status":           call.status,
        "started_at":       call.started_at.isoformat() if call.started_at else None,
        "transcript":       transcript_data,
        "analysis":         analysis_data,
        "analysed":         analysed,
    })


@csrf_exempt
@require_POST
@login_required
def analyse_call_now(request, call_id):
    user = request.user
    qs = (
        CallLog.objects.all() if user.role == "super_admin"
        else CallLog.objects.filter(org=user.org)
    )
    call = get_object_or_404(qs, id=call_id)
    try:
        run_post_call_analysis.delay(str(call.id))
    except Exception as _ce:
        logger.warning("[ANALYSIS] Celery unavailable — skipping: %s", _ce)
    return JsonResponse({"status": "ok", "call_id": str(call.id)})


# ════════════════════════════════════════════════════════════════════
#  SERVICE CUSTOMERS HELPERS
# ════════════════════════════════════════════════════════════════════

@login_required
def api_service_customers(request):
    org = request.user.org
    qs  = Customer.objects.filter(org=org).order_by("name") if org else Customer.objects.all().order_by("name")
    data = [
        {"id": str(c.id), "name": c.name, "phone": c.phone,
         "tag": (c.extra_data or {}).get("tag", "untagged")}
        for c in qs
    ]
    return JsonResponse({"customers": data})


@csrf_exempt
def service_call_result(request, bolna_call_id):
    call = CallLog.objects.filter(bolna_call_id=bolna_call_id).first()
    if not call:
        return JsonResponse({"ready": False, "status": "call_not_found"})
    if call.status not in ("completed", "failed"):
        return JsonResponse({"ready": False, "status": call.status or "in_progress"})

    transcript_text = ""
    try:
        transcript_text = call.transcript.full_text or ""
    except Exception:
        pass

    if not transcript_text:
        return JsonResponse({"ready": False, "status": "transcript_pending"})

    analysis = ManualAnalysis.objects.filter(
        transcript=transcript_text
    ).order_by("-created_at").first()

    if not analysis:
        return JsonResponse({"ready": False, "status": "analysis_pending"})

    ed = analysis.extra_data or {}
    _rt = (getattr(analysis, "routing_type", None) or ed.get("routing_type") or analysis.call_type or "service_only")
    dashboard_data = {
        "routing_type":       _rt,
        "call_type":          analysis.call_type,
        "f1":                 ed.get("engagement", 0),
        "f2":                 ed.get("fit", 0),
        "f3":                 ed.get("conversion", 0),
        "pos":                60 if analysis.sentiment == "positive" else 35 if analysis.sentiment == "neutral" else 10,
        "is_hot":             analysis.temperature == "Hot",
        "is_warm":            analysis.temperature == "Warm",
        "story": "", "intents": [], "signals": [],
        "satisfaction":       ed.get("satisfaction", analysis.lead_score),
        "issue_severity":     ed.get("issue_severity", "Medium"),
        "resolution_urgency": ed.get("resolution_urgency", "Medium"),
        "loyalty_risk":       ed.get("loyalty_risk", "Medium"),
        "issue_category":     ed.get("issue_category", "query"),
    }
    return JsonResponse({"ready": True, "name": analysis.lead_name or "Customer", "data": dashboard_data})


# ════════════════════════════════════════════════════════════════════
#  ANALYSIS STATUS POLLING
#  Stage 1 → call in progress
#  Stage 2 → call ended, saving transcript
#  Stage 3 → transcript saved, Feedback Agent running
#  Stage 4 → analysis done, full KPI payload returned
# ════════════════════════════════════════════════════════════════════

@csrf_exempt
def api_call_analysis_status(request, bolna_call_id):
    call = CallLog.objects.filter(bolna_call_id=bolna_call_id).select_related("transcript").first()
    if not call:
        return JsonResponse({"ready": False, "stage": 1, "stage_label": "Locating call record…"})
    if call.status not in ("completed", "failed"):
        # ── FIX: return started_at + current duration so the UI can show a live timer ──
        elapsed = 0
        if call.started_at:
            elapsed = max(0, int((timezone.now() - call.started_at).total_seconds()))
        return JsonResponse({
            "ready": False,
            "stage": 1,
            "stage_label": "Conversation in progress…",
            "duration_seconds": elapsed,
            "duration_display": (
                f"{elapsed // 60}m {elapsed % 60}s" if elapsed >= 60 else f"{elapsed}s"
            ) if elapsed > 0 else "Live ⏱",
            "started_at": call.started_at.isoformat() if call.started_at else None,
        })

    transcript_text = ""
    try:
        transcript_text = call.transcript.full_text or ""
        turns = call.transcript.turns or []
    except Exception:
        turns = []
    if not transcript_text and turns:
        transcript_text = "\n".join(f"{t.get('speaker','?').upper()}: {t.get('text','')}" for t in turns)
    if not transcript_text:
        return JsonResponse({"ready": False, "stage": 2, "stage_label": "Saving transcript to database…"})

    snippet = transcript_text[:300] + ("…" if len(transcript_text) > 300 else "")

    analysis = None
    try:
        analysis = ManualAnalysis.objects.filter(call=call).order_by("-created_at").first()
    except Exception:
        pass
    if analysis is None:
        try:
            analysis = ManualAnalysis.objects.filter(transcript=transcript_text).order_by("-created_at").first()
        except Exception:
            pass
    if not analysis:
        return JsonResponse({"ready": False, "stage": 3,
                             "stage_label": "Feedback Agent analysing transcript…",
                             "transcript_snippet": snippet})

    ed  = analysis.extra_data or {}
    pos = 65 if analysis.sentiment == "positive" else 35 if analysis.sentiment == "neutral" else 12
    _rt = (getattr(analysis, "routing_type", None) or ed.get("routing_type") or analysis.call_type or "service_only")
    f1  = ed.get("engagement", 0); f2 = ed.get("fit", 0); f3 = ed.get("conversion", 0)
    ls  = ed.get("lead_score", analysis.lead_score or 0)
    if not ls and (f1 or f2 or f3):
        ls = round(f1 * 0.4 + f2 * 0.35 + f3 * 0.25)

    return JsonResponse({
        "ready": True, "stage": 4, "stage_label": "Analysis complete ✓",
        "name": analysis.lead_name or "Customer",
        "routing_type": _rt, "call_type": analysis.call_type, "sentiment": analysis.sentiment,
        "f1": f1, "f2": f2, "f3": f3, "pos": pos,
        "is_hot": analysis.temperature == "Hot", "is_warm": analysis.temperature == "Warm",
        "lead_score": ls, "story": "", "intents": [], "signals": ed.get("buying_signals", []),
        "objections": ed.get("objections", []),
        "satisfaction": ed.get("satisfaction", analysis.lead_score or 50),
        "issue_severity": ed.get("issue_severity", "Medium"),
        "resolution_urgency": ed.get("resolution_urgency", "Medium"),
        "loyalty_risk": ed.get("loyalty_risk", "Medium"),
        "issue_category": ed.get("issue_category", "query"),
        "issues_raised": ed.get("issues_raised", []),
        "action_items": ed.get("action_items", []),
        "positive_notes": ed.get("positive_notes", []),
        "transcript_saved": True, "transcript_snippet": snippet, "call_id": str(call.id),
        "duration_seconds": call.duration_seconds,
        "duration_display": call.duration_display,
    })


from django.conf import settings as dj_settings

@csrf_exempt
@login_required
def call_service_agent(request):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=405)

    try:
        data  = json.loads(request.body)
        phone = (data.get("phone") or "").strip().replace(" ", "")
        name  = data.get("name", "Customer")

        if not phone:
            return JsonResponse({"error": "Phone required"}, status=400)

        if not phone.startswith("+"):
            phone = "+91" + phone.lstrip("0")

        org = getattr(request.user, "org", None)
        if not org:
            org = Organisation.objects.filter(is_active=True, is_approved=True).first()

        if not org or not org.is_approved:
            return JsonResponse({"success": False, "error": "Organisation not approved"}, status=403)

        if not org.is_number_allowed(phone):
            return JsonResponse({
                "success": False,
                "error": f"Number {phone} is not in the allowed list. Add it via Manage → Numbers."
            }, status=403)

        # Agent ID read exclusively from .env / Django settings (BOLNA_SERVICE_AGENT_ID)
        agent_id = (getattr(dj_settings, "BOLNA_SERVICE_AGENT_ID", "") or "").strip()

        if not agent_id:
            return JsonResponse({
                "success": False,
                "error": "Service Bolna Agent ID not configured. Add BOLNA_SERVICE_AGENT_ID to your .env file."
            }, status=500)

        logger.info("[SERVICE CALL] org=%s agent_id=%s phone=%s", org.name, agent_id, phone)

        result = initiate_call(agent_id=agent_id, phone=phone, name=name, extra_vars={"customer_name": name})

        if not result["success"]:
            logger.error("[SERVICE CALL] Bolna failed: %s", result["error"])
            return JsonResponse({"success": False, "error": result["error"]}, status=502)

        bolna_call_id = result["call_id"] or ""

        svc_call = None
        try:
            svc_call = CallLog.objects.create(
                org=org, call_type="service", direction="outbound", routing_type="service_only",
                bolna_call_id=bolna_call_id, contact_phone=phone, contact_name=name,
                started_at=timezone.now(), status="in_progress",
            )
        except Exception:
            svc_call = CallLog.objects.create(
                org=org, call_type="service", direction="outbound",
                bolna_call_id=bolna_call_id, contact_phone=phone, contact_name=name,
                started_at=timezone.now(), status="in_progress",
            )

        # ── FIX: Schedule auto hang-up for service calls ──────────────
        call_limit = getattr(org, "call_limit_seconds", 120)
        if svc_call and call_limit and call_limit > 0 and bolna_call_id:
            try:
                auto_hangup_call.apply_async(
                    args=[bolna_call_id, str(svc_call.id), call_limit],
                    countdown=call_limit,
                )
                logger.info(
                    "[SERVICE CALL] Auto-hangup scheduled for call %s in %ds",
                    svc_call.id, call_limit,
                )
            except Exception as _he:
                logger.warning("[SERVICE CALL] Could not schedule auto-hangup: %s", _he)

        logger.info("[SERVICE CALL] started bolna_call_id=%s phone=%s", bolna_call_id, phone)
        return JsonResponse({"success": True, "call_id": bolna_call_id, "message": "Service agent call initiated"})

    except Exception as e:
        logger.error("[SERVICE CALL ERROR] %s", str(e))
        return JsonResponse({"success": False, "error": str(e)}, status=500)


# ════════════════════════════════════════════════════════════════════
#  SUPER ADMIN — ORG MANAGEMENT
# ════════════════════════════════════════════════════════════════════

@csrf_exempt
@login_required
def org_approve(request, org_id):
    if request.user.role != "super_admin":
        return JsonResponse({"error": "Forbidden"}, status=403)
    try:
        org = Organisation.objects.get(id=org_id)
    except Organisation.DoesNotExist:
        return JsonResponse({"error": "Org not found"}, status=404)
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    data   = json.loads(request.body) if request.body else {}
    action = data.get("action", "approve")

    if action == "approve":
        org.is_approved = True
        org.is_active   = True
        org.approved_by = request.user.name
        org.approved_at = timezone.now()
        org.save(update_fields=["is_approved", "is_active", "approved_by", "approved_at"])
        return JsonResponse({"success": True, "message": f"'{org.name}' approved"})
    elif action == "reject":
        org.is_approved = False
        org.is_active   = False
        org.save(update_fields=["is_approved", "is_active"])
        return JsonResponse({"success": True, "message": f"'{org.name}' rejected"})
    else:
        return JsonResponse({"error": "Invalid action"}, status=400)


@csrf_exempt
@login_required
def org_set_limits(request, org_id):
    if request.user.role != "super_admin":
        return JsonResponse({"error": "Forbidden"}, status=403)
    try:
        org = Organisation.objects.get(id=org_id)
    except Organisation.DoesNotExist:
        return JsonResponse({"error": "Org not found"}, status=404)
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    data = json.loads(request.body) if request.body else {}
    update_fields = []
    for field in ["max_users", "max_agents", "max_teams", "call_quota", "minutes_quota", "number_quota"]:
        if field in data:
            setattr(org, field, int(data[field]))
            update_fields.append(field)
    if data.get("plan_type"):
        org.plan_type = data["plan_type"]
        update_fields.append("plan_type")
    if update_fields:
        org.save(update_fields=update_fields)

    return JsonResponse({
        "success": True,
        "org_id":  str(org.id),
        "limits": {
            "max_users": org.max_users, "max_agents": org.max_agents, "max_teams": org.max_teams,
            "call_quota": org.call_quota, "minutes_quota": org.minutes_quota,
            "number_quota": org.number_quota, "plan_type": org.plan_type,
        },
    })


@login_required
def org_manage_view(request):
    if request.user.role != "super_admin":
        return redirect("dashboard")
    orgs = Organisation.objects.all().order_by("-created_at")
    org_data = []
    for o in orgs:
        org_data.append({
            "org":        o,
            "user_count": o.user_count(),
            "team_count": o.team_count(),
            "teams":      list(o.teams.all().order_by("name")),
            "numbers_used": o.allowed_numbers.filter(is_active=True).count(),
        })
    pending_requests = OrgRequest.objects.filter(status="pending").order_by("-created_at")
    return render(request, "org_list.html", {
        "org_data":        org_data,
        "orgs":            orgs,
        "pending_requests": pending_requests,
    })


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
            call_quota           = int(p.get("call_quota", 100) or 100),
            minutes_quota        = int(p.get("minutes_quota", 300) or 300),
            number_quota         = int(p.get("number_quota", 4) or 4),
            sales_agent_prompt   = p.get("sales_agent_prompt", ""),
            service_agent_prompt = p.get("service_agent_prompt", ""),
            bolna_agent_id       = p.get("bolna_agent_id", ""),
            phone_number         = p.get("phone_number", ""),
            is_approved          = False,   # starts pending; super_admin approves separately
            is_active            = True,
        )
        from django.contrib import messages as _msg
        _msg.success(request, "Organisation created — pending approval.")
    return redirect("org_list")


@login_required
def ticket_update_view(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    if request.method == "POST":
        ticket.status      = request.POST.get("status", ticket.status)
        ticket.assigned_to = request.POST.get("assigned_to", ticket.assigned_to)
        ticket.save()
        from django.contrib import messages as _msg
        _msg.success(request, "Ticket updated.")
    return redirect("ticket_list")


@login_required
def call_analysis_view(request, call_id):
    user = request.user
    qs   = (
        CallLog.objects.all() if user.role == "super_admin"
        else CallLog.objects.filter(org=user.org)
    )
    call = get_object_or_404(
        qs.select_related("lead", "customer", "analysis", "transcript", "org")
          .prefetch_related("tickets__customer"),
        id=call_id,
    )
    return render(request, "call_analysis.html", {"call": call})


# ════════════════════════════════════════════════════════════════════
#  TEAM MANAGEMENT
# ════════════════════════════════════════════════════════════════════

@csrf_exempt
@login_required
def team_create(request):
    if request.user.role not in ("super_admin", "org_admin"):
        return JsonResponse({"error": "Forbidden"}, status=403)
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    data      = json.loads(request.body) if request.body else {}
    org_id    = data.get("org_id")
    team_name = data.get("name", "").strip()
    team_type = data.get("team_type", "mixed")

    if not org_id or not team_name:
        return JsonResponse({"error": "org_id and name required"}, status=400)

    try:
        org = Organisation.objects.get(id=org_id)
    except Organisation.DoesNotExist:
        return JsonResponse({"error": "Org not found"}, status=404)

    if org.team_count() >= org.max_teams:
        return JsonResponse({"error": f"Team limit reached ({org.max_teams})"}, status=403)

    team = Team.objects.create(
        org=org, name=team_name,
        team_type=team_type if team_type in ("sales", "support", "mixed") else "mixed",
        bolna_agent_id=data.get("bolna_agent_id", ""),
    )
    return JsonResponse({"success": True, "team_id": str(team.id), "message": f"Team '{team_name}' created"})


@csrf_exempt
@login_required
def team_assign_user(request):
    if request.user.role not in ("super_admin", "org_admin"):
        return JsonResponse({"error": "Forbidden"}, status=403)
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    data    = json.loads(request.body) if request.body else {}
    user_id = data.get("user_id")
    team_id = data.get("team_id")
    if not user_id or not team_id:
        return JsonResponse({"error": "user_id and team_id required"}, status=400)

    try:
        user = User.objects.get(id=user_id)
        team = Team.objects.get(id=team_id)
    except (User.DoesNotExist, Team.DoesNotExist):
        return JsonResponse({"error": "User or team not found"}, status=404)

    user.team = team
    user.save(update_fields=["team"])
    return JsonResponse({"success": True, "message": f"User '{user.name}' assigned to team '{team.name}'"})


@login_required
def team_list_view(request):
    user = request.user
    if user.role == "super_admin":
        teams = Team.objects.select_related("org").order_by("org__name", "name")
    else:
        teams = Team.objects.filter(org=user.org).order_by("name")

    return JsonResponse({"teams": [
        {
            "id": str(t.id), "name": t.name,
            "team_type": t.get_team_type_display(),
            "org_name": t.org.name, "member_count": t.member_count(),
            "is_active": t.is_active,
        }
        for t in teams
    ]})


# ════════════════════════════════════════════════════════════════════
#  TICKET LIST VIEW
# ════════════════════════════════════════════════════════════════════

@login_required
def ticket_list_view(request):
    user = request.user
    if user.role == "super_admin":
        qs = Ticket.objects.select_related("customer", "org", "call").order_by("-created_at")
    else:
        qs = Ticket.objects.filter(org=user.org).select_related("customer", "call").order_by("-created_at")
    status_filter = request.GET.get("status")
    if status_filter:
        qs = qs.filter(status=status_filter)
    return render(request, "ticket_list.html", {"tickets": qs})


# ════════════════════════════════════════════════════════════════════
#  ADMIN — USER MANAGEMENT
# ════════════════════════════════════════════════════════════════════

@login_required
def admin_users_view(request):
    if request.user.role != "super_admin":
        return redirect("dashboard")
    users = User.objects.select_related("org", "team").order_by("role", "name")
    orgs  = Organisation.objects.filter(is_active=True).order_by("name")
    teams = Team.objects.select_related("org").order_by("org__name", "name")
    context = {
        "users":             users,
        "orgs":              orgs,
        "teams":             teams,
        "super_admin_count": users.filter(role="super_admin").count(),
        "org_admin_count":   users.filter(role="org_admin").count(),
        "team_lead_count":   users.filter(role="team_lead").count(),
        "agent_count":       users.filter(role="agent").count(),
        "total_number_quota": Organisation.objects.aggregate(t=Sum("number_quota"))["t"] or 0,
    }
    return render(request, "admin_users.html", context)


@csrf_exempt
@login_required
def admin_user_create(request):
    """POST — create a new user (super admin only)."""
    if request.user.role != "super_admin":
        return JsonResponse({"error": "Forbidden"}, status=403)
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    name     = data.get("name", "").strip()
    email    = data.get("email", "").strip()
    password = data.get("password", "")
    role     = data.get("role", "agent")
    org_id   = data.get("org_id")
    team_id  = data.get("team_id")

    if not name or not email or not password:
        return JsonResponse({"error": "name, email and password are required"}, status=400)

    if User.objects.filter(email=email).exists():
        return JsonResponse({"error": "A user with this email already exists"}, status=400)

    org  = None
    team = None
    if org_id:
        try:
            org = Organisation.objects.get(id=org_id)
        except Organisation.DoesNotExist:
            return JsonResponse({"error": "Organisation not found"}, status=404)

        # Check user limit
        if org.user_count() >= org.max_users and role not in ("super_admin",):
            return JsonResponse({"error": f"User limit reached ({org.max_users}) for this org"}, status=403)

    if team_id:
        try:
            team = Team.objects.get(id=team_id)
        except Team.DoesNotExist:
            return JsonResponse({"error": "Team not found"}, status=404)

    user = User.objects.create_user(
        email=email, password=password, name=name, role=role,
        org=org, team=team,
        is_staff=(role == "super_admin"),
        is_superuser=(role == "super_admin"),
    )
    return JsonResponse({"success": True, "user_id": str(user.id), "message": f"User '{name}' created"})


@csrf_exempt
@login_required
def admin_user_update(request, user_id):
    if request.user.role != "super_admin":
        return JsonResponse({"error": "Forbidden"}, status=403)
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        target = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({"error": "User not found"}, status=404)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    update_fields = []
    if "name" in data and data["name"].strip():
        target.name = data["name"].strip()
        update_fields.append("name")
    if "role" in data and data["role"] in dict(User._meta.get_field("role").choices):
        target.role         = data["role"]
        target.is_staff     = data["role"] == "super_admin"
        target.is_superuser = data["role"] == "super_admin"
        update_fields.extend(["role", "is_staff", "is_superuser"])
    if "is_active" in data:
        target.is_active = bool(data["is_active"])
        update_fields.append("is_active")
    if "org_id" in data:
        if data["org_id"]:
            try:
                target.org = Organisation.objects.get(id=data["org_id"])
            except Organisation.DoesNotExist:
                return JsonResponse({"error": "Organisation not found"}, status=404)
        else:
            target.org = None
        update_fields.append("org")
    if "team_id" in data:
        if data["team_id"]:
            try:
                target.team = Team.objects.get(id=data["team_id"])
            except Team.DoesNotExist:
                return JsonResponse({"error": "Team not found"}, status=404)
        else:
            target.team = None
        update_fields.append("team")

    if update_fields:
        target.save(update_fields=update_fields)
    return JsonResponse({"success": True, "message": f"User '{target.name}' updated"})


# ════════════════════════════════════════════════════════════════════
#  ORG ADMIN — User Management (org admin creates users in own org)
# ════════════════════════════════════════════════════════════════════

@csrf_exempt
@login_required
def org_admin_create_user(request):
    """
    POST — Org Admin creates a user inside their own organisation.
    Only org_admin and super_admin can access this.
    """
    if request.user.role not in ("org_admin", "super_admin"):
        return JsonResponse({"error": "Forbidden — org_admin or super_admin only"}, status=403)
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    name     = data.get("name", "").strip()
    email    = data.get("email", "").strip()
    password = data.get("password", "")
    role     = data.get("role", "agent")
    team_id  = data.get("team_id")

    if not name or not email or not password:
        return JsonResponse({"error": "name, email and password are required"}, status=400)

    # Org admins can only create agents and team_leads in their own org
    if request.user.role == "org_admin" and role not in ("agent", "team_lead"):
        return JsonResponse({"error": "Org admin can only create agent or team_lead users"}, status=403)

    org = request.user.org
    if org is None:
        return JsonResponse({"error": "No org associated with your account"}, status=400)

    # Check user limit
    if org.user_count() >= org.max_users:
        return JsonResponse({"error": f"User limit reached ({org.max_users}) for your org"}, status=403)

    if User.objects.filter(email=email).exists():
        return JsonResponse({"error": "A user with this email already exists"}, status=400)

    team = None
    if team_id:
        try:
            team = Team.objects.get(id=team_id, org=org)
        except Team.DoesNotExist:
            return JsonResponse({"error": "Team not found in your org"}, status=404)

    user = User.objects.create_user(
        email=email, password=password, name=name, role=role,
        org=org, team=team,
    )
    return JsonResponse({"success": True, "user_id": str(user.id), "message": f"User '{name}' created in {org.name}"})


@login_required
def org_users_view(request):
    """HTML page — org admin manages their own org's users."""
    user = request.user
    if user.role not in ("org_admin", "super_admin"):
        return redirect("dashboard")

    if user.role == "super_admin":
        users = User.objects.select_related("org", "team").order_by("org__name", "name")
        teams = Team.objects.select_related("org").order_by("org__name", "name")
    else:
        users = User.objects.filter(org=user.org).select_related("team").order_by("name")
        teams = Team.objects.filter(org=user.org).order_by("name")

    return render(request, "org_users.html", {
        "users": users,
        "teams": teams,
        "org":   user.org,
    })


# ════════════════════════════════════════════════════════════════════
#  ADMIN — TEAM MANAGEMENT HTML VIEW
# ════════════════════════════════════════════════════════════════════

@login_required
def admin_teams_view(request):
    if request.user.role != "super_admin":
        return redirect("dashboard")

    teams = Team.objects.select_related("org").prefetch_related("members").order_by("org__name", "name")
    orgs  = Organisation.objects.filter(is_active=True).order_by("name")
    unassigned_users = User.objects.filter(team__isnull=True, is_active=True).select_related("org").order_by("name")

    for team in teams:
        team.call_count = CallLog.objects.filter(team=team).count()

    context = {
        "teams":            teams,
        "orgs":             orgs,
        "unassigned_users": unassigned_users,
        "sales_count":      teams.filter(team_type="sales").count(),
        "support_count":    teams.filter(team_type="support").count(),
    }
    return render(request, "admin_teams.html", context)


# ════════════════════════════════════════════════════════════════════
#  SUPER ADMIN DASHBOARD
# ════════════════════════════════════════════════════════════════════

@login_required
def admin_dashboard_view(request):
    if request.user.role != "super_admin":
        return redirect("dashboard")

    all_orgs    = Organisation.objects.all().order_by("-created_at")
    all_users   = User.objects.all()
    total_calls = CallLog.objects.count()

    pending_list = all_orgs.filter(is_active=True, is_approved=False).order_by("-created_at")[:8]
    pending_requests = OrgRequest.objects.filter(status="pending").order_by("-created_at")[:5]

    orgs_annotated = []
    for o in all_orgs:
        o.user_count  = o.users.count()
        o.numbers_used = o.allowed_numbers.filter(is_active=True).count()
        orgs_annotated.append(o)

    context = {
        "total_orgs":            all_orgs.count(),
        "approved_orgs":         all_orgs.filter(is_approved=True).count(),
        "pending_orgs":          all_orgs.filter(is_active=True, is_approved=False).count(),
        "total_users":           all_users.count(),
        "active_users":          all_users.filter(is_active=True).count(),
        "total_calls":           total_calls,
        "pending_approval_list": pending_list,
        "pending_requests":      pending_requests,
        "recent_users":          all_users.select_related("org").order_by("-created_at")[:6],
        "all_orgs":              orgs_annotated,
    }
    return render(request, "admin_dashboard.html", context)


# ════════════════════════════════════════════════════════════════════
#  NUMBERS MANAGEMENT PAGE (HTML)
# ════════════════════════════════════════════════════════════════════

@login_required
def numbers_page(request):
    """HTML page to manage org's allowed phone numbers."""
    user = request.user
    org  = user.org

    if user.role == "super_admin":
        # Super admin sees all numbers grouped by org
        numbers = AllowedPhoneNumber.objects.filter(is_active=True).select_related("org").order_by("org__name", "-added_at")
    else:
        numbers = AllowedPhoneNumber.objects.filter(org=org, is_active=True).order_by("-added_at")

    return render(request, "numbers.html", {
        "numbers":      numbers,
        "number_quota": org.number_quota if org else 0,
        "numbers_used": numbers.count(),
        "org_id":       str(org.id) if org else "",
    })