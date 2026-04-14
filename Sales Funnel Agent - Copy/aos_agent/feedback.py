"""
feedback.py — Transcript Analysis Engine
─────────────────────────────────────────
Two analysis paths:
  1. SALES  — new prospect → Lead Score, Engagement, Conversion, Objections
  2. SERVICE — existing customer → Satisfaction, Issue Severity, Resolution Urgency, Loyalty Risk

Step 1: Detect customer type from transcript
Step 2: Run the appropriate analysis prompt
Step 3: Return structured data for the correct dashboard
"""

import json
import os
from dotenv import load_dotenv
from groq import Groq

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

MODEL = "llama-3.3-70b-versatile"


def _clamp(val, fallback, lo=0, hi=100):
    """Safely parse an int and clamp to [lo, hi]."""
    try:
        v = int(float(val))
        return max(lo, min(hi, v))
    except (TypeError, ValueError):
        return fallback


# ════════════════════════════════════════════════════════════════════
#  STEP 1 — DETECT CUSTOMER TYPE
# ════════════════════════════════════════════════════════════════════

def _detect_customer_type(transcript_text: str) -> str:
    """
    Ask the LLM whether this transcript is a sales (new prospect) call
    or a service (existing customer) call.  Returns 'sales' or 'service'.
    """
    prompt = f"""You are a call classifier. Read the transcript below and decide:

Is this a SALES call (new prospect being pitched / exploring a product for the first time)?
Or a SERVICE call (existing customer with a complaint, issue, query about something they already own/use)?

Transcript:
\"\"\"
{transcript_text}
\"\"\"

Reply with ONLY one word: sales  OR  service
No other text."""

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "Reply with exactly one word: sales or service. Nothing else."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=10,
        )
        answer = response.choices[0].message.content.strip().lower()
        return "service" if "service" in answer else "sales"
    except Exception:
        return "sales"


# ════════════════════════════════════════════════════════════════════
#  STEP 2a — SALES ANALYSIS (new prospects)
# ════════════════════════════════════════════════════════════════════

def _analyze_sales(transcript_text: str) -> dict:
    """Full sales analysis — engagement, fit, conversion, sentiment, objections."""

    prompt = f"""You are a sales CRM analyst. Analyze the transcript below and return ONLY a raw JSON object — no markdown, no explanation, no code fences.

Transcript:
\"\"\"
{transcript_text}
\"\"\"

Return exactly this JSON structure with these exact keys:
{{
  "sentiment_score": <float between -1.0 and 1.0>,
  "interest_score": <integer 0-100>,
  "fit_score": <integer 0-100>,
  "closing_prob": <integer 0-100>,
  "lead_temperature": <exactly one of: "Hot", "Warm", "Cold">,
  "story": <string, 2-3 sentence summary of the prospect's situation, needs, and likelihood to buy>,
  "intents": <list of up to 3 short strings>,
  "buying_signals": <list of up to 4 short strings, empty if none>,
  "objections": <list of up to 3 short strings, empty if none>
}}

CRITICAL SCORING RULES — scores MUST be logically consistent:
- If sentiment_score is high (>0.5) AND interest_score is high (>60), then closing_prob should also be reasonably high (>45) UNLESS there are strong objections.
- If there are many objections (3 items), closing_prob should be lower (<50) and lead_temperature should NOT be "Hot".
- If interest_score < 30, lead_temperature MUST be "Cold".
- If closing_prob > 70, sentiment_score should be > 0.3 (you can't close someone who's negative).
- "Hot" = decision imminent AND few objections; "Warm" = interested but evaluating; "Cold" = low interest or many blockers.
- The scores should tell a coherent story — don't give contradictory signals.

Return ONLY the JSON object. No other text."""

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You are a JSON-only API. Output raw JSON and nothing else."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=600,
    )

    content = response.choices[0].message.content.strip()
    if "```" in content:
        content = content.replace("```json", "").replace("```", "").strip()
    start = content.find("{")
    end = content.rfind("}") + 1
    if start != -1 and end > start:
        content = content[start:end]

    try:
        raw = json.loads(content)
    except Exception:
        raw = {
            "sentiment_score": 0, "interest_score": 40, "fit_score": 40,
            "closing_prob": 20, "lead_temperature": "Cold",
            "story": "Unable to parse transcript. Please check the input.",
            "intents": [], "buying_signals": [], "objections": [],
        }

    temp_raw = str(raw.get("lead_temperature", "Cold")).strip().capitalize()
    if temp_raw not in ("Hot", "Warm", "Cold"):
        temp_raw = "Cold"

    interest = _clamp(raw.get("interest_score"), 40)
    fit      = _clamp(raw.get("fit_score"), 40)
    closing  = _clamp(raw.get("closing_prob"), 20)
    sent_raw = float(raw.get("sentiment_score", 0))
    sent_raw = max(-1.0, min(1.0, sent_raw))

    # ── COHERENCE ENFORCEMENT ──
    objections = raw.get("objections") or []
    if len(objections) >= 3 and closing > 45:
        closing = min(closing, 45)
    if len(objections) >= 3 and temp_raw == "Hot":
        temp_raw = "Warm"
    if sent_raw < -0.2 and temp_raw == "Hot":
        temp_raw = "Warm"
    if interest < 30:
        temp_raw = "Cold"
    if closing > 70 and sent_raw < 0.0:
        closing = min(closing, 50)

    pos_pct = int((sent_raw + 1) * 50)

    story = str(raw.get("story", "") or "").strip()
    if not story:
        temp_desc = {"Hot": "ready to make a decision", "Warm": "evaluating options", "Cold": "in early-stage exploration"}
        story = f"Prospect is {temp_desc.get(temp_raw, 'engaging')} with {'strong' if interest >= 70 else 'moderate' if interest >= 40 else 'low'} interest."

    return {
        "call_type": "sales",
        "pos": pos_pct,
        "f1": interest,
        "f2": fit,
        "f3": closing,
        "is_hot":  temp_raw == "Hot",
        "is_warm": temp_raw == "Warm",
        "is_cold": temp_raw == "Cold",
        "story": story,
        "intents": raw.get("intents") or [],
        "signals": raw.get("buying_signals") or [],
        "objections": objections,
    }


# ════════════════════════════════════════════════════════════════════
#  STEP 2b — SERVICE ANALYSIS (existing customers)
# ════════════════════════════════════════════════════════════════════

def _analyze_service(transcript_text: str) -> dict:
    """Service analysis — satisfaction, issue severity, resolution urgency, loyalty risk."""

    prompt = f"""You are a customer service analyst. Analyze the transcript below. This is an EXISTING customer — they already own/use the product or service. They are NOT a new buyer.

Transcript:
\"\"\"
{transcript_text}
\"\"\"

Return ONLY a raw JSON object — no markdown, no explanation, no code fences:
{{
  "satisfaction_score": <integer 0-100, how satisfied is the customer overall>,
  "issue_severity": <exactly one of: "Critical", "High", "Medium", "Low">,
  "resolution_urgency": <exactly one of: "Immediate", "High", "Medium", "Low">,
  "loyalty_risk": <exactly one of: "High", "Medium", "Low">,
  "sentiment_score": <float between -1.0 and 1.0>,
  "issue_category": <exactly one of: "maintenance", "billing", "complaint", "query", "feedback", "escalation", "other">,
  "story": <string, 2-3 sentence summary of customer's situation, issue, and emotional state>,
  "issues_raised": <list of up to 4 short strings describing specific issues mentioned>,
  "positive_notes": <list of up to 3 short strings, any positive feedback or appreciation, empty if none>,
  "action_items": <list of up to 3 short strings, specific follow-up actions needed>
}}

SCORING RULES — scores MUST be logically consistent:
- satisfaction_score < 30 = very unhappy customer; 30-60 = mixed/frustrated; 60-80 = generally okay; 80+ = happy
- If satisfaction_score < 30, loyalty_risk should be "High" and sentiment should be negative
- If issue_severity is "Critical", resolution_urgency should be "Immediate" or "High"
- If customer is threatening to leave/cancel, loyalty_risk MUST be "High"
- If customer is just asking a simple question, issue_severity should be "Low"

Return ONLY the JSON object."""

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You are a JSON-only API. Output raw JSON and nothing else."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=600,
    )

    content = response.choices[0].message.content.strip()
    if "```" in content:
        content = content.replace("```json", "").replace("```", "").strip()
    start = content.find("{")
    end = content.rfind("}") + 1
    if start != -1 and end > start:
        content = content[start:end]

    try:
        raw = json.loads(content)
    except Exception:
        raw = {
            "satisfaction_score": 50, "issue_severity": "Medium",
            "resolution_urgency": "Medium", "loyalty_risk": "Medium",
            "sentiment_score": 0, "issue_category": "query",
            "story": "Unable to parse transcript. Please check the input.",
            "issues_raised": [], "positive_notes": [], "action_items": [],
        }

    satisfaction = _clamp(raw.get("satisfaction_score"), 50)

    severity = str(raw.get("issue_severity", "Medium")).strip().capitalize()
    if severity not in ("Critical", "High", "Medium", "Low"):
        severity = "Medium"

    urgency = str(raw.get("resolution_urgency", "Medium")).strip().capitalize()
    if urgency not in ("Immediate", "High", "Medium", "Low"):
        urgency = "Medium"

    loyalty = str(raw.get("loyalty_risk", "Medium")).strip().capitalize()
    if loyalty not in ("High", "Medium", "Low"):
        loyalty = "Medium"

    category = str(raw.get("issue_category", "query")).strip().lower()
    valid_cats = ("maintenance", "billing", "complaint", "query", "feedback", "escalation", "other")
    if category not in valid_cats:
        category = "other"

    sent_raw = float(raw.get("sentiment_score", 0))
    sent_raw = max(-1.0, min(1.0, sent_raw))
    pos_pct = int((sent_raw + 1) * 50)

    # ── COHERENCE ENFORCEMENT ──
    if satisfaction < 30 and loyalty == "Low":
        loyalty = "Medium"
    if satisfaction < 20:
        loyalty = "High"
    if severity == "Critical" and urgency == "Low":
        urgency = "High"

    story = str(raw.get("story", "") or "").strip()
    if not story:
        story = f"Existing customer raised a {category} issue with {severity.lower()} severity."

    return {
        "call_type": "service",
        "satisfaction": satisfaction,
        "issue_severity": severity,
        "resolution_urgency": urgency,
        "loyalty_risk": loyalty,
        "issue_category": category,
        "pos": pos_pct,
        "story": story,
        "issues_raised": raw.get("issues_raised") or [],
        "positive_notes": raw.get("positive_notes") or [],
        "action_items": raw.get("action_items") or [],
    }


# ════════════════════════════════════════════════════════════════════
#  DISENGAGEMENT DETECTOR — overrides LLM when prospect clearly exits
# ════════════════════════════════════════════════════════════════════

_DISENGAGE_PHRASES = [
    "not interested", "no interest", "don't want", "don't need",
    "leave it", "forget it", "never mind", "nevermind",
    "not looking", "not right now", "maybe later", "some other time",
    "cancel", "drop it", "stop", "i'll pass",
    # Hindi / Hinglish
    "nahi chahiye", "nahi chahie", "nahin chahiye",
    "interest nahi", "zaroorat nahi", "rehne do",
    "chhod do", "mat karo", "abhi nahi",
    "baad mein", "baad me dekh", "phir kabhi",
]

def _is_disengaged(transcript_text: str) -> bool:
    """Return True if the user clearly expressed disinterest in the transcript."""
    lower = transcript_text.lower()
    return any(phrase in lower for phrase in _DISENGAGE_PHRASES)


# ════════════════════════════════════════════════════════════════════
#  PUBLIC ENTRY POINT
# ════════════════════════════════════════════════════════════════════

def analyze_feedback(transcript_text: str) -> dict:
    """
    Main entry point — detects customer type, runs appropriate analysis.
    Returns dict with 'call_type' = 'sales' | 'service' plus type-specific KPIs.
    """
    call_type = _detect_customer_type(transcript_text)

    if call_type == "service":
        return _analyze_service(transcript_text)

    result = _analyze_sales(transcript_text)

    # ── POST-PROCESS: if prospect explicitly disengaged, override sentiment ──
    # The LLM often misreads polite closing words ("sorry", "thank you") as
    # positive sentiment even when the prospect clearly said "not interested".
    if _is_disengaged(transcript_text):
        result["is_hot"]  = False
        result["is_warm"] = False
        result["is_cold"] = True
        result["f3"]      = min(result.get("f3", 0), 15)   # closing prob ≤ 15
        result["f1"]      = min(result.get("f1", 0), 30)   # engagement ≤ 30
        result["pos"]     = min(result.get("pos", 50), 38) # sentiment → negative zone
        if not result.get("objections"):
            result["objections"] = ["Prospect explicitly stated disinterest"]

    return result