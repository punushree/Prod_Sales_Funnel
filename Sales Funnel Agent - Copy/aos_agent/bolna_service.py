"""
aos_agent/bolna_service.py
──────────────────────────────────────────────────────────────────────────────
Thin wrapper around the Bolna REST API.

FIXES applied in this version:
  • stop_call()        — gracefully terminate a live call (for auto-hangup)
  • get_call_details() — fetch full call data incl. duration from Bolna
  • get_call_status()  — enhanced to extract duration_seconds from response
──────────────────────────────────────────────────────────────────────────────
"""

import os
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

BOLNA_BASE_URL = os.environ.get("BOLNA_BASE_URL", "https://api.bolna.dev").rstrip("/")


def _get_api_key() -> str:
    try:
        from django.conf import settings as dj
        key = getattr(dj, "BOLNA_API_KEY", "") or ""
        if key:
            return key.strip()
    except Exception:
        pass
    return os.environ.get("BOLNA_API_KEY", "").strip()


def _get_service_agent_id() -> str:
    try:
        from django.conf import settings as dj
        return getattr(dj, "BOLNA_SERVICE_AGENT_ID", "") or ""
    except Exception:
        return os.environ.get("BOLNA_SERVICE_AGENT_ID", "")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type":  "application/json",
    }


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def initiate_call(
    *,
    agent_id:    str,
    phone:       str,
    name:        Optional[str] = None,
    extra_vars:  Optional[dict] = None,
    retries:     int = 2,
) -> dict:
    """
    Fire an outbound call via Bolna.

    Returns { "success": bool, "call_id": str|None, "error": str|None, "raw": dict|None }
    """
    api_key = _get_api_key()
    if not api_key:
        logger.error("BOLNA_API_KEY is not set — cannot initiate call")
        return {
            "success": False, "call_id": None,
            "error": "BOLNA_API_KEY not configured in settings or .env", "raw": None,
        }

    phone = phone.strip().replace(" ", "")
    if not phone.startswith("+"):
        phone = "+91" + phone.lstrip("0")

    payload: dict = {
        "agent_id":               agent_id,
        "recipient_phone_number": phone,
    }

    variables: dict = {}
    if name:
        variables["recipient_name"] = name
    if extra_vars:
        variables.update(extra_vars)
    if variables:
        payload["variables"] = variables

    url = f"{BOLNA_BASE_URL}/call"
    logger.info("Bolna initiate → url=%s agent=%s phone=%s", url, agent_id, phone)

    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, json=payload, headers=_headers(), timeout=15)
            logger.info("Bolna response %d: %s", resp.status_code, resp.text[:300])

            body = {}
            try:
                body = resp.json()
            except Exception:
                body = {}

            if resp.status_code in (200, 201):
                call_id = (
                    body.get("call_id")
                    or body.get("id")
                    or body.get("execution_id")
                    or body.get("run_id")
                    or body.get("data", {}).get("call_id")
                )
                logger.info("Bolna call initiated → call_id=%s phone=%s", call_id, phone)
                return {"success": True, "call_id": call_id, "error": None, "raw": body}

            last_error = body.get("message") or body.get("error") or resp.text
            logger.warning(
                "Bolna attempt %d/%d failed: HTTP %s — %s",
                attempt, retries, resp.status_code, last_error
            )
            if resp.status_code < 500:
                break

        except requests.Timeout:
            last_error = "Request to Bolna timed out"
            logger.warning("Bolna attempt %d/%d timed out", attempt, retries)
        except Exception as exc:
            last_error = str(exc)
            logger.exception("Bolna attempt %d/%d raised: %s", attempt, retries, exc)

    return {"success": False, "call_id": None, "error": last_error, "raw": None}


def get_call_status(bolna_call_id: str) -> dict:
    """
    Fetch the current status of a Bolna call.

    FIX: Now extracts duration_seconds from multiple possible field names
    so callers can update CallLog.duration_seconds reliably.
    """
    try:
        resp = requests.get(
            f"{BOLNA_BASE_URL}/call/{bolna_call_id}",
            headers=_headers(),
            timeout=10,
        )
        data = resp.json()

        # Normalise duration from whatever field Bolna returns
        duration = (
            data.get("duration_seconds")
            or data.get("duration")
            or data.get("call_duration")
            or data.get("total_duration")
            or 0
        )
        data["_duration_seconds"] = int(duration)  # always available under this key

        return data
    except Exception as exc:
        logger.exception("get_call_status failed: %s", exc)
        return {"error": str(exc), "_duration_seconds": 0}


def get_call_details(bolna_call_id: str) -> dict:
    """
    Fetch full call details from Bolna including transcript and duration.
    Returns a normalised dict with keys:
        status, duration_seconds, transcript_text, transcript_turns
    """
    raw = get_call_status(bolna_call_id)
    if "error" in raw and "_duration_seconds" not in raw:
        return {"error": raw["error"], "duration_seconds": 0}

    # Bolna may nest transcript under different keys
    transcript_text = (
        raw.get("transcript")
        or raw.get("transcript_text")
        or raw.get("full_transcript")
        or ""
    )
    transcript_turns = (
        raw.get("transcript_turns")
        or raw.get("turns")
        or []
    )

    return {
        "status":           raw.get("status", ""),
        "duration_seconds": raw.get("_duration_seconds", 0),
        "transcript_text":  transcript_text,
        "transcript_turns": transcript_turns,
        "raw":              raw,
    }


# ─────────────────────────────────────────────────────────────────────────────
# FIX: Auto Hang-up Support
# ─────────────────────────────────────────────────────────────────────────────

def stop_call(bolna_call_id: str) -> dict:
    """
    Gracefully terminate a live Bolna call.

    Called by the auto-hangup Celery task when a call exceeds its time limit.
    Bolna will play any configured end-of-call message before disconnecting.

    Returns { "success": bool, "error": str|None }
    """
    if not bolna_call_id:
        return {"success": False, "error": "No bolna_call_id provided"}

    try:
        resp = requests.post(
            f"{BOLNA_BASE_URL}/call/{bolna_call_id}/stop",
            headers=_headers(),
            timeout=10,
        )
        logger.info(
            "stop_call bolna_id=%s → HTTP %d %s",
            bolna_call_id, resp.status_code, resp.text[:200]
        )
        if resp.status_code in (200, 201, 204):
            return {"success": True, "error": None}

        body = {}
        try:
            body = resp.json()
        except Exception:
            pass
        err = body.get("message") or body.get("error") or resp.text
        return {"success": False, "error": err}

    except requests.Timeout:
        logger.warning("stop_call timed out for bolna_id=%s", bolna_call_id)
        return {"success": False, "error": "Request timed out"}
    except Exception as exc:
        logger.exception("stop_call failed for bolna_id=%s: %s", bolna_call_id, exc)
        return {"success": False, "error": str(exc)}


def warn_call_ending(bolna_call_id: str, seconds_remaining: int = 30) -> dict:
    """
    Inject a graceful time-limit warning into an active Bolna call.

    Tries Bolna's message-injection endpoint first.  If that returns a
    non-2xx (or the endpoint doesn't exist on this Bolna version), it
    falls back to updating the call variables so the agent picks up the
    cue on its next turn.

    Returns { "success": bool, "method": str, "error": str|None }
    """
    if not bolna_call_id:
        return {"success": False, "method": "none", "error": "No bolna_call_id provided"}

    warning_text = (
        f"Just a heads-up — this call will automatically end in "
        f"{seconds_remaining} seconds due to your organisation's call time limit. "
        f"Please wrap up your conversation."
    )

    # ── Attempt 1: direct message injection ──────────────────────────────────
    # Bolna exposes  POST /call/{id}/message  on some deployments.
    # We try it first; a 404 just means this deployment doesn't have it.
    try:
        resp = requests.post(
            f"{BOLNA_BASE_URL}/call/{bolna_call_id}/message",
            headers=_headers(),
            json={"message": warning_text, "role": "agent"},
            timeout=8,
        )
        logger.info(
            "warn_call_ending (inject) bolna_id=%s → HTTP %d",
            bolna_call_id, resp.status_code,
        )
        if resp.status_code in (200, 201, 202, 204):
            return {"success": True, "method": "inject", "error": None}
    except Exception as exc:
        logger.warning("warn_call_ending inject failed: %s", exc)

    # ── Attempt 2: update call variables ─────────────────────────────────────
    # Bolna supports  PATCH /call/{id}  with a variables dict.
    # Setting a well-known variable lets a properly-prompted agent speak it.
    try:
        resp = requests.patch(
            f"{BOLNA_BASE_URL}/call/{bolna_call_id}",
            headers=_headers(),
            json={
                "variables": {
                    "time_limit_warning": warning_text,
                    "seconds_remaining":  str(seconds_remaining),
                }
            },
            timeout=8,
        )
        logger.info(
            "warn_call_ending (vars) bolna_id=%s → HTTP %d",
            bolna_call_id, resp.status_code,
        )
        if resp.status_code in (200, 201, 202, 204):
            return {"success": True, "method": "variables", "error": None}
    except Exception as exc:
        logger.warning("warn_call_ending vars update failed: %s", exc)

    # ── Both attempts failed — warning won't be spoken but hangup will still ──
    # fire.  Log clearly so it can be debugged.
    logger.warning(
        "warn_call_ending: could not inject warning for bolna_id=%s "
        "(both inject and vars endpoints failed). Call will still be stopped at limit.",
        bolna_call_id,
    )
    return {
        "success": False,
        "method":  "none",
        "error":   "Both inject and variable-update endpoints failed",
    }