"""
aos_agent/models_fixes.py
──────────────────────────────────────────────────────────────────────────────
PATCH FILE — copy the properties/methods below into your CallLog and
Organisation models in models.py.

FIXES:
  1. CallLog.contact_label  — shows phone number when name is unavailable
  2. CallLog.duration_display — shows "Xm Ys" instead of 0s
  3. Organisation.call_limit_seconds — per-org configurable call time limit
──────────────────────────────────────────────────────────────────────────────

HOW TO APPLY:
  1. Open aos_agent/models.py
  2. Add the properties below inside the CallLog class
  3. Add call_limit_seconds field to Organisation model
  4. Run: python manage.py makemigrations && python manage.py migrate
"""


# ═══════════════════════════════════════════════════
# PASTE INSIDE class CallLog(models.Model):
# ═══════════════════════════════════════════════════

"""
    # ── FIX 1: contact_label — shows phone number when name unavailable ──
    @property
    def contact_label(self) -> str:
        \"\"\"
        Priority order:
          1. Customer name  (if linked customer has a name)
          2. Lead name      (if linked lead has a name)
          3. contact_phone  (the dialled number — always available from migration 0021)
          4. Customer phone (fallback from customer record)
          5. Lead phone
          6. '—'
        \"\"\"
        if self.customer_id:
            name = (self.customer.name or "").strip()
            if name:
                return name
            if self.customer.phone:
                return self.customer.phone

        if self.lead_id:
            name = (self.lead.name or "").strip()
            if name:
                return name
            if self.lead.phone:
                return self.lead.phone

        # contact_phone added in migration 0021
        if self.contact_phone:
            return self.contact_phone

        return "—"

    # ── FIX 2: duration_display — human-readable duration ──────────────
    @property
    def duration_display(self) -> str:
        \"\"\"
        Returns '2m 34s', '45s', or '—' instead of '0s' when unknown.
        \"\"\"
        s = self.duration_seconds or 0
        if s <= 0:
            # If the call is completed but duration is 0, show a dash
            # (webhook likely failed to fire — see fix_stale_calls command)
            if self.status == "completed":
                return "—"
            return "Live"
        m, sec = divmod(s, 60)
        if m:
            return f"{m}m {sec}s"
        return f"{sec}s"
"""


# ═══════════════════════════════════════════════════
# PASTE INSIDE class Organisation(models.Model):
# ═══════════════════════════════════════════════════

"""
    # ── FIX 3: per-org call time limit (seconds) ────────────────────────
    # Add this FIELD to the Organisation model:
    call_limit_seconds = models.IntegerField(
        default=120,   # 2 minutes default
        help_text="Maximum call duration in seconds. Call is auto-stopped after this.",
    )
"""


# ═══════════════════════════════════════════════════
# FULL EXAMPLE — what your CallLog should look like
# after applying the fixes (abbreviated):
# ═══════════════════════════════════════════════════

FULL_CALLLOG_EXAMPLE = """
class CallLog(models.Model):
    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    call_type        = models.CharField(max_length=10, choices=[('sales','Sales'),('service','Service')])
    direction        = models.CharField(max_length=10, choices=[('inbound','Inbound'),('outbound','Outbound')])
    duration_seconds = models.IntegerField(default=0)
    status           = models.CharField(default='in_progress', max_length=30)
    bolna_call_id    = models.CharField(blank=True, max_length=255)
    contact_phone    = models.CharField(blank=True, default='', max_length=30,
                                        help_text='Phone number dialled for this call')
    started_at       = models.DateTimeField()
    ended_at         = models.DateTimeField(blank=True, null=True)
    created_at       = models.DateTimeField(auto_now_add=True)
    org              = models.ForeignKey('Organisation', ...)
    customer         = models.ForeignKey('Customer', null=True, blank=True, ...)
    lead             = models.ForeignKey('Lead', null=True, blank=True, ...)

    class Meta:
        db_table = 'call_log'

    # ── FIX 1 ──────────────────────────────────────────────────────────
    @property
    def contact_label(self) -> str:
        if self.customer_id:
            name = (self.customer.name or '').strip()
            if name:
                return name
            if self.customer.phone:
                return self.customer.phone
        if self.lead_id:
            name = (self.lead.name or '').strip()
            if name:
                return name
            if self.lead.phone:
                return self.lead.phone
        if self.contact_phone:
            return self.contact_phone
        return '—'

    # ── FIX 2 ──────────────────────────────────────────────────────────
    @property
    def duration_display(self) -> str:
        s = self.duration_seconds or 0
        if s <= 0:
            return 'Live' if self.status == 'in_progress' else '—'
        m, sec = divmod(s, 60)
        return f'{m}m {sec}s' if m else f'{sec}s'
"""
