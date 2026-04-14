"""
Run from your aos_agent folder:

    cd "C:\\Users\\Administrator\\Desktop\\after\\Org SFA - Copy\\Sales Funnel Agent - Copy\\aos_agent"
    python patch_views.py

Fixes:
  1. dashboard_view  — user.org.number_quota crash
  2. allowed_numbers_add — 400 on missing org
  3. allowed_numbers_list — quota crash
  4. call_service_agent  — removes hardcoded wrong agent ID, reads from Django Admin / .env
"""

import sys, pathlib

TARGET = pathlib.Path(__file__).parent / "views.py"
if not TARGET.exists():
    sys.exit(f"ERROR: {TARGET} not found — run from the aos_agent folder")

src = TARGET.read_text(encoding="utf-8")

patches = []

# ────────────────────────────────────────────────────────────────────
# 1. dashboard_view
# ────────────────────────────────────────────────────────────────────
patches.append((
    "dashboard_view",
    '''def dashboard_view(request):
    user = request.user
    if user.role == "super_admin":
        return redirect("admin_dashboard")

    calls = CallLog.objects.filter(org=user.org)
    leads = Lead.objects.filter(org=user.org)

    total_calls = calls.count()
    conn_calls  = calls.filter(status="completed").count()
    conn_rate   = round((conn_calls / total_calls * 100), 1) if total_calls > 0 else 0
    avg_dur     = calls.aggregate(Avg('duration_seconds'))['duration_seconds__avg'] or 0

    analyses = CallAnalysis.objects.filter(call__in=calls)
    total_an = analyses.count() or 1

    sent_pos = round((analyses.filter(sentiment="positive").count() / total_an) * 100)
    sent_neu = round((analyses.filter(sentiment="neutral").count()  / total_an) * 100)
    sent_neg = round((analyses.filter(sentiment="negative").count() / total_an) * 100)

    avg_score = round(analyses.aggregate(s=Avg("extra_kpi__lead_score"))["s"] or 0)
    conv_rate = round((leads.filter(status="converted").count() / (leads.count() or 1)) * 100, 1)

    # Number quota usage
    allowed_count = AllowedPhoneNumber.objects.filter(org=user.org, is_active=True).count()
    number_quota  = user.org.number_quota

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
        "number_quota":     number_quota,
        "calls_used":       user.org.calls_used,
        "call_quota":       user.org.call_quota,
        "minutes_used":     user.org.minutes_used,
        "minutes_quota":    user.org.minutes_quota,
    }

    recent_calls = calls.select_related("lead").order_by("-started_at")[:8]
    return render(request, "dashboard.html", {"stats": stats, "recent_calls": recent_calls})''',

    '''def dashboard_view(request):
    user = request.user
    if user.role == "super_admin":
        return redirect("admin_dashboard")

    org = getattr(user, "org", None)
    if org is None:
        org = Organisation.objects.filter(is_active=True, is_approved=True).first()

    if org is None:
        empty = {
            "total_calls": 0, "connection_rate": 0, "avg_duration_str": "0m 0s",
            "conversion_rate": 0, "sent_pos_pct": 0, "sent_neu_pct": 0, "sent_neg_pct": 0,
            "avg_lead_score": 0, "hot_leads": 0, "warm_leads": 0, "cold_leads": 0,
            "allowed_numbers": 0, "number_quota": 0,
            "calls_used": 0, "call_quota": 0, "minutes_used": 0, "minutes_quota": 0,
        }
        return render(request, "dashboard.html", {"stats": empty, "recent_calls": []})

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
    return render(request, "dashboard.html", {"stats": stats, "recent_calls": recent_calls})'''
))

# ────────────────────────────────────────────────────────────────────
# 2. allowed_numbers_add — org resolution
# ────────────────────────────────────────────────────────────────────
patches.append((
    "allowed_numbers_add",
    '''    user = request.user
    if user.role == "super_admin":
        org_id = body.get("org_id")
        if not org_id:
            return JsonResponse({"error": "org_id required for super_admin"}, status=400)
        try:
            org = Organisation.objects.get(id=org_id)
        except Organisation.DoesNotExist:
            return JsonResponse({"error": "Org not found"}, status=404)
    else:
        org = user.org

    if org is None:
        return JsonResponse({"error": "No org associated with user"}, status=400)''',

    '''    user = request.user
    org  = None
    if body.get("org_id"):
        try:
            org = Organisation.objects.get(id=body["org_id"])
        except Exception:
            pass
    if org is None:
        org = getattr(user, "org", None)
    if org is None:
        org = Organisation.objects.filter(is_active=True, is_approved=True).first()
    if org is None:
        return JsonResponse({"error": "No active organisation found."}, status=400)'''
))

# ────────────────────────────────────────────────────────────────────
# 3. allowed_numbers_list — quota crash
# ────────────────────────────────────────────────────────────────────
patches.append((
    "allowed_numbers_list quota",
    '        "quota":   user.org.number_quota if user.org else 0,',
    '        "quota":   user.org.number_quota if getattr(user, "org", None) else 0,'
))

# ────────────────────────────────────────────────────────────────────
# 4. call_service_agent — remove hardcoded wrong agent ID
# ────────────────────────────────────────────────────────────────────
patches.append((
    "call_service_agent hardcoded agent_id",
    '''        api_key  = getattr(settings, "BOLNA_API_KEY", "") or "bn-7b3556ed156449b096e6973ca6bfb1b6"
        agent_id = getattr(settings, "BOLNA_SERVICE_AGENT_ID", "") or "27281bc6-7fe5-4be6-8d7c-24ce196c29c6"

        if not agent_id or not api_key:
            return JsonResponse({"success": False, "error": "Bolna config missing"}, status=500)

        payload = {
            "agent_id":               agent_id,
            "recipient_phone_number": phone,
            "variables":              {"customer_name": name}
        }

        response = http_requests.post(
            "https://api.bolna.ai/call",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=15
        )

        if response.status_code not in [200, 201]:
            return JsonResponse({"success": False, "error": "Bolna API failed", "details": response.text}, status=500)

        res_data      = response.json()
        bolna_call_id = res_data.get("call_id", "")

        CallLog.objects.create(
            org=org, call_type="service", direction="outbound",
            bolna_call_id=bolna_call_id, started_at=timezone.now(), status="in_progress",
        )

        return JsonResponse({"success": True, "call_id": bolna_call_id, "message": "Service agent call initiated"})

    except http_requests.exceptions.Timeout:
        return JsonResponse({"success": False, "error": "Request timeout"}, status=500)
    except Exception as e:
        logger.error("[ERROR] %s", str(e))
        return JsonResponse({"success": False, "error": str(e)}, status=500)''',

    '''        # Read agent ID from org → settings — NO hardcoded fallback
        agent_id = (
            (org.service_bolna_agent_id or "").strip()
            or (org.bolna_agent_id or "").strip()
            or (getattr(settings, "BOLNA_SERVICE_AGENT_ID", "") or "").strip()
        )

        if not agent_id:
            return JsonResponse({
                "success": False,
                "error": (
                    "Bolna Service Agent ID not set. "
                    "Go to Django Admin → Organisations → your org → "
                    "set the 'Service bolna agent id' field to your Bolna agent ID."
                )
            }, status=500)

        logger.info("[SERVICE CALL] org=%s agent_id=%s phone=%s", org.name, agent_id, phone)

        result = initiate_call(
            agent_id=agent_id, phone=phone, name=name,
            extra_vars={"customer_name": name},
        )

        if not result["success"]:
            logger.error("[SERVICE CALL] Bolna failed: %s", result["error"])
            return JsonResponse({"success": False, "error": result["error"]}, status=502)

        bolna_call_id = result["call_id"] or ""

        try:
            CallLog.objects.create(
                org=org, call_type="service", direction="outbound",
                routing_type="service_only", bolna_call_id=bolna_call_id,
                started_at=timezone.now(), status="in_progress",
            )
        except Exception:
            CallLog.objects.create(
                org=org, call_type="service", direction="outbound",
                bolna_call_id=bolna_call_id, started_at=timezone.now(), status="in_progress",
            )

        logger.info("[SERVICE CALL] started bolna_call_id=%s", bolna_call_id)
        return JsonResponse({"success": True, "call_id": bolna_call_id, "message": "Service agent call initiated"})

    except Exception as e:
        logger.error("[SERVICE CALL ERROR] %s", str(e))
        return JsonResponse({"success": False, "error": str(e)}, status=500)'''
))

# ── Apply ─────────────────────────────────────────────────────────
changed = 0
for name, old, new in patches:
    if old in src:
        src = src.replace(old, new, 1)
        print(f"  PATCHED   {name}")
        changed += 1
    else:
        print(f"  SKIPPED   {name}  (already patched or not found)")

if changed:
    TARGET.write_text(src, encoding="utf-8")
    print(f"\n{changed} patch(es) written to {TARGET}")
else:
    print("\nNothing to patch.")

print("""
─────────────────────────────────────────────────────────
NEXT STEP — set your real Bolna Agent ID:

  1. Log in to https://app.bolna.dev
  2. Go to Agents → open your service agent
  3. Copy the agent ID  (looks like: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)
  4. Go to http://localhost:8000/admin/
     → Organisations → your org
     → Paste into "Service bolna agent id" field → Save

  Then restart:  python manage.py runserver
─────────────────────────────────────────────────────────
""")
