"""
aos_agent/tasks.py
──────────────────────────────────────────────────────────────────────────────
Celery tasks for:
  1. auto_hangup_call     — gracefully stop a call that exceeds the time limit
  2. fix_stale_in_progress — resolve calls stuck in 'in_progress'
  3. run_call_analysis    — trigger AI analysis after webhook fires

SETUP:
  Make sure Celery + Redis (or RabbitMQ) is configured in settings.py:

      CELERY_BROKER_URL = 'redis://localhost:6379/0'
      CELERY_RESULT_BACKEND = 'redis://localhost:6379/0'

  In aos_project/celery.py (create if not present):

      import os
      from celery import Celery
      os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aos_project.settings')
      app = Celery('aos_project')
      app.config_from_object('django.conf:settings', namespace='CELERY')
      app.autodiscover_tasks()

  Start worker:  celery -A aos_project worker --loglevel=info
  Start beat:    celery -A aos_project beat --loglevel=info   (for periodic tasks)
──────────────────────────────────────────────────────────────────────────────
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.utils.timezone import now

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# TASK 1: Auto Hang-up
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# TASK 1a: Graceful Warning — fires 30 s before the hard limit
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=2, default_retry_delay=5)
def warn_call_limit(self, bolna_call_id: str, call_log_id: str, seconds_remaining: int = 30):
    """
    Inject a graceful "call ending soon" message into a live call.

    Scheduled via apply_async(countdown=limit_seconds - 30) immediately
    after the call is initiated.  If the call already ended naturally
    this is a harmless no-op.

    Args:
        bolna_call_id:    Bolna call identifier
        call_log_id:      Our CallLog UUID (string)
        seconds_remaining: How many seconds until hard hangup (default 30)
    """
    from aos_agent.models import CallLog
    from aos_agent.bolna_service import warn_call_ending

    try:
        call = CallLog.objects.get(id=call_log_id)
    except CallLog.DoesNotExist:
        logger.warning("warn_call_limit: CallLog %s not found", call_log_id)
        return

    # No-op if already finished
    if call.status != "in_progress":
        logger.info(
            "warn_call_limit: call %s already ended (status=%s) — skipping warning",
            call_log_id, call.status,
        )
        return

    logger.info(
        "warn_call_limit: injecting %ds warning into call %s (bolna_id=%s)",
        seconds_remaining, call_log_id, bolna_call_id,
    )

    result = warn_call_ending(bolna_call_id, seconds_remaining)
    logger.info(
        "warn_call_limit: result method=%s success=%s error=%s",
        result.get("method"), result.get("success"), result.get("error"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# TASK 1b: Auto Hang-up — fires at the hard limit
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=2, default_retry_delay=5)
def auto_hangup_call(self, bolna_call_id: str, call_log_id: str, limit_seconds: int):
    """
    Gracefully stop a call that exceeds its time limit.

    Phase 2 of the two-phase graceful shutdown:
      Phase 1 (warn_call_limit) — fires at T = limit − 30 s, speaks a warning
      Phase 2 (this task)       — fires at T = limit, terminates the call

    Scheduled via apply_async(countdown=limit_seconds) right after the call
    is initiated.  If the call already ended naturally, this is a no-op.

    Args:
        bolna_call_id:  Bolna's call identifier
        call_log_id:    Our CallLog UUID (string)
        limit_seconds:  The configured limit (used as duration fallback)
    """
    from aos_agent.models import CallLog
    from aos_agent.bolna_service import stop_call, get_call_status

    try:
        call = CallLog.objects.get(id=call_log_id)
    except CallLog.DoesNotExist:
        logger.warning("auto_hangup_call: CallLog %s not found", call_log_id)
        return

    # No-op if call already ended
    if call.status != "in_progress":
        logger.info(
            "auto_hangup_call: call %s already has status=%s — skipping",
            call_log_id, call.status,
        )
        return

    logger.info(
        "auto_hangup_call: stopping call %s (bolna_id=%s) after %ds limit",
        call_log_id, bolna_call_id, limit_seconds,
    )

    # Stop via Bolna API
    result = stop_call(bolna_call_id)
    if not result.get("success"):
        logger.warning(
            "auto_hangup_call: stop_call failed for %s: %s — retrying",
            bolna_call_id, result.get("error"),
        )
        try:
            raise self.retry(countdown=3)
        except Exception:
            pass  # max retries hit — fall through to force-complete

    # Give Bolna 3 s to finalise the call, then poll for actual duration
    import time
    time.sleep(3)

    try:
        details       = get_call_status(bolna_call_id)
        actual_duration = details.get("_duration_seconds", 0)
    except Exception:
        actual_duration = 0

    # Use actual duration if available, else the limit is our best estimate
    final_duration = actual_duration if actual_duration > 0 else limit_seconds

    # Mark the call as completed in our DB
    call.status           = "completed"
    call.ended_at         = now()
    call.duration_seconds = final_duration
    call.save(update_fields=["status", "ended_at", "duration_seconds"])

    logger.info(
        "auto_hangup_call: call %s stopped & saved (duration=%ds, limit=%ds)",
        call_log_id, final_duration, limit_seconds,
    )

    # Trigger post-call analysis
    try:
        run_call_analysis.delay(call_log_id)
    except Exception as exc:
        logger.warning("auto_hangup_call: could not trigger analysis: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# TASK 2: Fix Stale In-Progress Calls (run every 10 minutes via Celery Beat)
# ─────────────────────────────────────────────────────────────────────────────

@shared_task
def fix_stale_in_progress_calls():
    """
    FIX: Resolve calls stuck in 'in_progress' when Bolna webhook never arrived.

    Add to Celery Beat schedule in settings.py:

        from celery.schedules import crontab
        CELERY_BEAT_SCHEDULE = {
            'fix-stale-calls': {
                'task': 'aos_agent.tasks.fix_stale_in_progress_calls',
                'schedule': crontab(minute='*/10'),   # every 10 minutes
            },
        }
    """
    from aos_agent.models import CallLog
    from aos_agent.bolna_service import get_call_status

    # Calls in_progress for more than 15 minutes are definitely stale
    cutoff = now() - timedelta(minutes=15)
    stale_calls = CallLog.objects.filter(
        status="in_progress",
        started_at__lt=cutoff,
    ).select_related("customer", "lead")

    resolved_count = 0

    for call in stale_calls:
        logger.info("fix_stale: checking stale call %s (bolna_id=%s)", call.id, call.bolna_call_id)

        duration = 0
        new_status = "completed"  # assume completed unless Bolna says failed

        if call.bolna_call_id:
            try:
                result = get_call_status(call.bolna_call_id)
                bolna_status = result.get("status", "").lower()
                duration     = result.get("_duration_seconds", 0)

                if bolna_status in ("failed", "error"):
                    new_status = "failed"
                elif bolna_status in ("no-answer", "no_answer", "busy"):
                    new_status = "no_answer"
                else:
                    new_status = "completed"
            except Exception as exc:
                logger.warning("fix_stale: Bolna poll failed for %s: %s", call.bolna_call_id, exc)

        update_fields = ["status", "ended_at"]
        call.status   = new_status
        call.ended_at = call.ended_at or now()

        if duration > 0:
            call.duration_seconds = duration
            update_fields.append("duration_seconds")

        call.save(update_fields=update_fields)
        resolved_count += 1
        logger.info(
            "fix_stale: resolved call %s → status=%s duration=%ds",
            call.id, new_status, duration
        )

    logger.info("fix_stale_in_progress_calls: resolved %d stale calls", resolved_count)
    return resolved_count


# ─────────────────────────────────────────────────────────────────────────────
# TASK 3: Run AI Analysis on Completed Call
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def run_call_analysis(self, call_log_id: str):
    """
    Triggered after webhook fires (call completed + transcript saved).
    Runs AI analysis and updates CallAnalysis + extra_kpi records.

    Adapt the body of this task to call your existing analysis pipeline.
    """
    from aos_agent.models import CallLog

    try:
        call = CallLog.objects.select_related(
            "org", "customer", "lead", "analysis"
        ).get(id=call_log_id)
    except CallLog.DoesNotExist:
        logger.warning("run_call_analysis: CallLog %s not found", call_log_id)
        return

    # Check transcript exists
    try:
        transcript = call.transcript
    except Exception:
        logger.info("run_call_analysis: no transcript for call %s — skipping", call_log_id)
        return

    if not transcript.full_text:
        logger.info("run_call_analysis: empty transcript for call %s — skipping", call_log_id)
        return

    logger.info("run_call_analysis: analysing call %s (type=%s)", call_log_id, call.call_type)

    # ── YOUR EXISTING ANALYSIS LOGIC GOES HERE ────────────────────────
    # Example: call your Groq/OpenAI analysis function
    # from aos_agent.feedback import analyse_transcript
    # result = analyse_transcript(transcript.full_text, call.call_type)
    # ... update CallAnalysis, extra_kpi, etc.
    # ─────────────────────────────────────────────────────────────────
    pass


# Alias so existing views.py import keeps working
run_post_call_analysis = run_call_analysis