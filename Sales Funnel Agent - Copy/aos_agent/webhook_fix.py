"""
aos_agent/webhook_fix.py
──────────────────────────────────────────────────────────────────────────────
PATCH FILE — paste/replace your existing bolna_webhook view in views.py
with the function below.

FIXES applied:
  1. duration_seconds is now extracted from the webhook payload and saved
  2. ended_at is set from the webhook (not left NULL)
  3. call_type is re-classified as 'sales' if transcript shows sales intent
     (e.g. an existing customer asking about a new property)
  4. Auto hang-up Celery task is scheduled when call enters 'in_progress'
──────────────────────────────────────────────────────────────────────────────
"""

import json
import logging

from django.http import JsonResponse
from django.utils.timezone import now
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)

# ── Keywords that re-classify a service call → sales ─────────────────────────
SALES_INTENT_KEYWORDS = [
    "new property", "new flat", "buy", "purchase", "new home",
    "invest", "looking for", "new apartment", "interested in buying",
    "want to buy", "want to purchase", "new house", "new project",
    "book a flat", "book flat", "property inquiry", "property enquiry",
    "new villa", "new plot", "new office", "buy office",
]


def _resolve_call_type(transcript_text: str, original_type: str) -> str:
    """
    FIX: Re-classify call_type AFTER transcript is available.
    If an existing-customer (service) call contains sales-intent language,
    return 'sales' so it appears in the right dashboard column.
    """
    if original_type == "service" and transcript_text:
        text_lower = transcript_text.lower()
        if any(kw in text_lower for kw in SALES_INTENT_KEYWORDS):
            logger.info("re-classifying service call → sales based on transcript keywords")
            return "sales"
    return original_type


def _extract_duration(payload: dict) -> int:
    """
    FIX: Bolna uses several different field names for duration across
    versions — try them all and return the first non-zero value.
    """
    for key in ("duration_seconds", "duration", "call_duration",
                "total_duration", "talk_time", "billable_duration"):
        val = payload.get(key)
        if val:
            try:
                return int(float(val))
            except (ValueError, TypeError):
                continue
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# PASTE THIS VIEW into views.py (replace existing bolna_webhook)
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def bolna_webhook(request):
    """
    Receives POST from Bolna when a call ends (or status changes).

    Expected payload keys (Bolna may vary by version):
        call_id / id / execution_id
        status          — 'completed' | 'failed' | 'no-answer' | 'busy'
        duration / duration_seconds
        transcript / transcript_text / full_transcript
    """
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        logger.warning("bolna_webhook: invalid JSON body")
        return JsonResponse({"ok": False, "error": "invalid JSON"}, status=400)

    logger.info("bolna_webhook payload: %s", str(payload)[:500])

    # ── 1. Identify the call ──────────────────────────────────────────────
    bolna_call_id = (
        payload.get("call_id")
        or payload.get("id")
        or payload.get("execution_id")
        or payload.get("run_id")
    )
    if not bolna_call_id:
        logger.warning("bolna_webhook: no call_id in payload")
        return JsonResponse({"ok": False, "error": "missing call_id"}, status=400)

    # ── 2. Find CallLog ───────────────────────────────────────────────────
    try:
        from aos_agent.models import CallLog, Transcript
    except ImportError:
        return JsonResponse({"ok": False, "error": "model import failed"}, status=500)

    try:
        call = CallLog.objects.get(bolna_call_id=bolna_call_id)
    except CallLog.DoesNotExist:
        logger.warning("bolna_webhook: no CallLog for bolna_id=%s", bolna_call_id)
        return JsonResponse({"ok": True, "note": "call not found — ignored"})

    # ── 3. FIX: Extract and save duration ────────────────────────────────
    duration_seconds = _extract_duration(payload)

    # ── 4. Determine final status ─────────────────────────────────────────
    bolna_status = payload.get("status", "").lower()
    if bolna_status in ("completed", "ended", "success"):
        new_status = "completed"
    elif bolna_status in ("failed", "error"):
        new_status = "failed"
    elif bolna_status in ("no-answer", "no_answer", "busy"):
        new_status = "no_answer"
    else:
        # Unknown status — keep current unless it's in_progress (resolve it)
        new_status = "completed" if call.status == "in_progress" else call.status

    # ── 5. Save transcript ────────────────────────────────────────────────
    transcript_text = (
        payload.get("transcript")
        or payload.get("transcript_text")
        or payload.get("full_transcript")
        or ""
    )
    transcript_turns = payload.get("transcript_turns") or payload.get("turns") or []

    if transcript_text:
        Transcript.objects.update_or_create(
            call=call,
            defaults={
                "full_text": transcript_text,
                "turns":     transcript_turns,
            },
        )

    # ── 6. FIX: Re-classify call_type based on transcript ─────────────────
    resolved_type = _resolve_call_type(transcript_text, call.call_type)

    # ── 7. FIX: Update CallLog with duration + ended_at ──────────────────
    update_fields = ["status", "call_type"]

    call.status    = new_status
    call.call_type = resolved_type

    if duration_seconds > 0:
        call.duration_seconds = duration_seconds
        update_fields.append("duration_seconds")

    if not call.ended_at and new_status in ("completed", "failed", "no_answer"):
        call.ended_at = now()
        update_fields.append("ended_at")

    call.save(update_fields=update_fields)
    logger.info(
        "bolna_webhook: call %s → status=%s duration=%ds type=%s",
        bolna_call_id, new_status, duration_seconds, resolved_type
    )

    # ── 8. Trigger AI analysis if completed and has transcript ────────────
    if new_status == "completed" and transcript_text:
        try:
            # Replace with your actual Celery task name
            from aos_agent.tasks import run_call_analysis
            run_call_analysis.delay(str(call.id))
        except Exception as exc:
            logger.warning("Could not schedule analysis task: %s", exc)

    return JsonResponse({"ok": True, "status": new_status, "duration": duration_seconds})


# ─────────────────────────────────────────────────────────────────────────────
# ALSO ADD THIS VIEW — called when a call is first initiated
# to schedule the auto-hangup task
# ─────────────────────────────────────────────────────────────────────────────

def _schedule_auto_hangup(call, org):
    """
    Call this immediately after creating a CallLog + initiating via Bolna.
    Schedules Celery to auto-stop the call at org.call_limit_seconds.
    """
    limit = getattr(org, "call_limit_seconds", 120)
    if limit and limit > 0:
        try:
            from aos_agent.tasks import auto_hangup_call
            auto_hangup_call.apply_async(
                args=[call.bolna_call_id, str(call.id), limit],
                countdown=limit,   # fires after `limit` seconds
            )
            logger.info(
                "Scheduled auto-hangup for call %s in %ds", call.id, limit
            )
        except Exception as exc:
            logger.warning("Could not schedule auto-hangup: %s", exc)
