"""
models.py — AOS Agent Platform
Multi-tenant AI Sales + Service Calling System

CHANGES (v2 — per project spec):
- Added AllowedPhoneNumber: org-scoped phone whitelist
- Added OrgRequest: client self-service org request form
- Organisation.is_approved defaults to False → pending approval
- ManualAnalysis now AUTO-only (no manual input, written by webhook)
"""

import uuid
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager


INDUSTRY_CHOICES = [
    ("real_estate", "Real Estate"),
    ("healthcare",  "Healthcare"),
    ("cosmetics",   "Cosmetics / Retail"),
    ("apartment",   "Apartment / Property Management"),
    ("generic",     "Generic"),
]
CALL_TYPE_CHOICES   = [("sales","Sales"),("service","Service")]
DIRECTION_CHOICES   = [("inbound","Inbound"),("outbound","Outbound")]
SENTIMENT_CHOICES   = [("positive","Positive"),("neutral","Neutral"),("negative","Negative")]
TICKET_STATUS_CHOICES = [("open","Open"),("in_progress","In Progress"),("resolved","Resolved"),("closed","Closed")]
LEAD_STATUS_CHOICES = [("new","New"),("called","Called"),("interested","Interested"),("not_interested","Not Interested"),("converted","Converted")]
ROLE_CHOICES = [("super_admin","Super Admin"),("org_admin","Org Admin"),("team_lead","Team Lead"),("agent","Agent")]
TEAM_TYPE_CHOICES = [("sales","Sales Team"),("support","Support Team"),("mixed","Mixed / General")]
ORG_REQUEST_STATUS  = [("pending","Pending"),("approved","Approved"),("rejected","Rejected")]
ROUTING_CHOICES = [("sales_only","Sales Only"),("service_only","Service Only"),("service_then_sales","Service → Sales Transfer")]


# ─────────────────────────────────────────────
# ORGANISATION
# ─────────────────────────────────────────────
class Organisation(models.Model):
    id                   = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name                 = models.CharField(max_length=255)
    industry             = models.CharField(max_length=50, choices=INDUSTRY_CHOICES, default="generic")
    plan_type            = models.CharField(max_length=50, default="trial")
    call_quota           = models.IntegerField(default=100)
    minutes_quota        = models.IntegerField(default=300)
    number_quota         = models.PositiveIntegerField(default=4, help_text="Max unique phone numbers this org can call.")
    calls_used           = models.IntegerField(default=0)
    minutes_used         = models.IntegerField(default=0)
    numbers_used         = models.IntegerField(default=0, help_text="Cached count of active allowed phone numbers.")
    max_users            = models.IntegerField(default=10)
    max_agents           = models.IntegerField(default=5)
    max_teams            = models.IntegerField(default=3)

    # Approval workflow
    is_approved          = models.BooleanField(default=False)
    approved_by          = models.CharField(max_length=255, blank=True)
    approved_at          = models.DateTimeField(null=True, blank=True)

    sales_agent_prompt   = models.TextField(blank=True)
    service_agent_prompt = models.TextField(blank=True)
    bolna_agent_id       = models.CharField(max_length=255, blank=True)
    phone_number         = models.CharField(max_length=20, blank=True)
    sales_bolna_agent_id   = models.CharField(max_length=255, blank=True)
    service_bolna_agent_id = models.CharField(max_length=255, blank=True)

    # ── Auto hang-up: max call duration per org ──────────────────────
    call_limit_seconds   = models.IntegerField(
        default=120,
        help_text="Maximum call duration in seconds (0 = no limit). "
                  "Call is gracefully stopped after this duration.",
    )

    is_active            = models.BooleanField(default=True)
    created_at           = models.DateTimeField(auto_now_add=True)
    updated_at           = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "organisation"

    def __str__(self):
        return f"{self.name} ({self.industry})"

    def quota_exceeded(self):
        return self.calls_used >= self.call_quota or self.minutes_used >= self.minutes_quota

    def number_quota_exceeded(self):
        return self.allowed_numbers.filter(is_active=True).count() >= self.number_quota

    def is_number_allowed(self, phone: str) -> bool:
        """
        Bug 4 fix: fuzzy match so +919876543210 matches 9876543210 and vice versa.
        Checks: exact match OR last-10-digit suffix match.
        """
        phone = phone.strip().replace(" ", "")
        last10 = phone[-10:] if len(phone) >= 10 else phone
        return self.allowed_numbers.filter(is_active=True).filter(
            models.Q(phone=phone) | models.Q(phone__endswith=last10)
        ).exists()

    def user_count(self):
        return self.users.count()

    def team_count(self):
        return self.teams.count()


# ─────────────────────────────────────────────
# ALLOWED PHONE NUMBER  ★ NEW
# Whitelist: calls can ONLY go to numbers here
# ─────────────────────────────────────────────
class AllowedPhoneNumber(models.Model):
    id        = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org       = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="allowed_numbers")
    phone     = models.CharField(max_length=20, help_text="E.164 format, e.g. +919876543210")
    label     = models.CharField(max_length=255, blank=True, help_text="Friendly name / lead name")
    added_by  = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    added_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "allowed_phone_number"
        unique_together = [("org", "phone")]

    def __str__(self):
        return f"{self.phone} [{self.org.name}]"


# ─────────────────────────────────────────────
# ORG REQUEST  ★ NEW
# Client fills form → super admin reviews → approves → org created
# ─────────────────────────────────────────────
class OrgRequest(models.Model):
    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # ── Step 1: Organisation details ──────────────────────────────────
    org_name       = models.CharField(max_length=255)
    org_email      = models.EmailField(blank=True)
    org_phone      = models.CharField(max_length=20, blank=True)
    org_website    = models.CharField(max_length=255, blank=True)
    org_address    = models.TextField(blank=True)
    industry       = models.CharField(max_length=50, choices=INDUSTRY_CHOICES, default="generic")
    plan_type      = models.CharField(max_length=50, default="trial")
    call_quota     = models.IntegerField(default=100)
    minutes_quota  = models.IntegerField(default=300)
    number_quota   = models.PositiveIntegerField(default=4)
    max_users      = models.IntegerField(default=10)
    # ── Step 2: Admin / contact details ──────────────────────────────
    contact_name   = models.CharField(max_length=255)
    contact_email  = models.EmailField()
    contact_phone  = models.CharField(max_length=20, blank=True)
    admin_username = models.CharField(max_length=150, blank=True)
    admin_email    = models.EmailField(blank=True)   # login email (may differ from contact)
    notes          = models.TextField(blank=True)
    # ── Status / review ──────────────────────────────────────────────
    status         = models.CharField(max_length=10, choices=ORG_REQUEST_STATUS, default="pending")
    reviewed_by    = models.CharField(max_length=255, blank=True)
    reviewed_at    = models.DateTimeField(null=True, blank=True)
    review_notes   = models.TextField(blank=True)
    org            = models.OneToOneField(Organisation, on_delete=models.SET_NULL, null=True, blank=True, related_name="request")
    # Stores generated password hash so we can show plain text once after creation
    generated_password = models.CharField(max_length=255, blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "org_request"

    def __str__(self):
        return f"{self.org_name} — {self.status}"


# ─────────────────────────────────────────────
# TEAM
# ─────────────────────────────────────────────
class Team(models.Model):
    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org            = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="teams")
    name           = models.CharField(max_length=255)
    team_type      = models.CharField(max_length=10, choices=TEAM_TYPE_CHOICES, default="mixed")
    bolna_agent_id = models.CharField(max_length=255, blank=True)
    is_active      = models.BooleanField(default=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "team"

    def __str__(self):
        return f"{self.name} ({self.get_team_type_display()}) — {self.org.name}"

    def member_count(self):
        return self.members.count()


# ─────────────────────────────────────────────
# USER
# ─────────────────────────────────────────────
class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address")
        email = self.normalize_email(email)
        role  = extra_fields.get("role", "agent")
        if role == "super_admin":
            extra_fields.setdefault("is_staff", True)
            extra_fields.setdefault("is_superuser", True)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        extra_fields.setdefault('role', 'super_admin') 
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser):
    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org          = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="users", null=True, blank=True)
    team         = models.ForeignKey('Team', on_delete=models.SET_NULL, related_name="members", null=True, blank=True)
    name         = models.CharField(max_length=255)
    email        = models.EmailField(unique=True)
    role         = models.CharField(max_length=20, choices=ROLE_CHOICES, default="agent")
    is_active    = models.BooleanField(default=True)
    is_staff     = models.BooleanField(default=False)
    is_superuser = models.BooleanField(default=False)
    created_at   = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD  = "email"
    REQUIRED_FIELDS = ["name"]
    objects = UserManager()

    class Meta:
        db_table = "user"

    def __str__(self):
        return f"{self.name} <{self.email}> [{self.role}]"

    def has_perm(self, perm, obj=None): return self.is_superuser
    def has_module_perms(self, app_label): return self.is_superuser


# ─────────────────────────────────────────────
# LEAD
# ─────────────────────────────────────────────
class Lead(models.Model):
    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org        = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="leads")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="created_leads")
    name       = models.CharField(max_length=255)
    phone      = models.CharField(max_length=20)
    email      = models.EmailField(blank=True)
    company    = models.CharField(max_length=255, blank=True)
    status     = models.CharField(max_length=20, choices=LEAD_STATUS_CHOICES, default="new")
    notes      = models.TextField(blank=True)
    extra_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "lead"

    def __str__(self):
        return f"{self.name} | {self.phone} [{self.org.industry}]"


# ─────────────────────────────────────────────
# CUSTOMER
# ─────────────────────────────────────────────
class Customer(models.Model):
    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org          = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="customers")
    name         = models.CharField(max_length=255)
    phone        = models.CharField(max_length=20, db_index=True)
    email        = models.EmailField(blank=True)
    extra_data   = models.JSONField(default=dict, blank=True)
    onboarded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "customer"

    def __str__(self):
        return f"{self.name} | {self.phone}"


# ─────────────────────────────────────────────
# CALL LOG
# ─────────────────────────────────────────────
class CallLog(models.Model):
    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org              = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="calls")
    lead             = models.ForeignKey(Lead, on_delete=models.SET_NULL, null=True, blank=True, related_name="calls")
    customer         = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name="calls")
    team             = models.ForeignKey('Team', on_delete=models.SET_NULL, null=True, blank=True, related_name="calls")
    routing_type     = models.CharField(max_length=20, choices=ROUTING_CHOICES, default="sales_only", blank=True)
    call_type        = models.CharField(max_length=10, choices=CALL_TYPE_CHOICES)
    direction        = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
    contact_phone    = models.CharField(max_length=30, blank=True, help_text="Phone number dialled for this call")
    contact_name     = models.CharField(max_length=255, blank=True, help_text="Customer/lead name at time of call")
    duration_seconds = models.IntegerField(default=0)
    status           = models.CharField(max_length=30, default="completed")
    bolna_call_id    = models.CharField(max_length=255, blank=True)
    started_at       = models.DateTimeField()
    ended_at         = models.DateTimeField(null=True, blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "call_log"

    def __str__(self):
        party = self.lead or self.customer
        return f"{self.call_type} | {self.direction} | {party} | {self.duration_seconds}s"

    @property
    def duration_display(self) -> str:
        """
        Human-readable duration.
        - in_progress with 0s  → 'Live ⏱'
        - completed  with 0s  → '—'  (webhook missed / call didn't connect)
        - otherwise            → '2m 34s' / '45s'
        """
        s = self.duration_seconds or 0
        if s <= 0:
            return "Live ⏱" if self.status == "in_progress" else "—"
        m, sec = divmod(s, 60)
        return f"{m}m {sec}s" if m else f"{sec}s"

    @property
    def contact_label(self) -> str:
        """
        Best available display name for the contact.
        Priority:
          1. contact_name   (name stored at call creation time — most reliable)
          2. Customer name  — only if not a placeholder ('Customer', 'Unknown', etc.)
          3. Customer phone
          4. Lead name      — same placeholder check
          5. Lead phone
          6. contact_phone  (the dialled number, always stored since migration 0021)
          7. '—'
        """
        _PLACEHOLDERS = {"customer", "unknown", "n/a", "test", ""}

        # contact_name is set directly from the call button — most reliable
        if self.contact_name and self.contact_name.strip().lower() not in _PLACEHOLDERS:
            return self.contact_name.strip()

        if self.customer_id:
            name = (self.customer.name or "").strip()
            if name.lower() not in _PLACEHOLDERS:
                return name
            if self.customer.phone:
                return self.customer.phone

        if self.lead_id:
            name = (self.lead.name or "").strip()
            if name.lower() not in _PLACEHOLDERS:
                return name
            if self.lead.phone:
                return self.lead.phone

        if self.contact_phone:
            return self.contact_phone

        return "—"


# ─────────────────────────────────────────────
# TRANSCRIPT (auto-captured from Bolna webhook)
# ─────────────────────────────────────────────
class Transcript(models.Model):
    id        = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    call      = models.OneToOneField(CallLog, on_delete=models.CASCADE, related_name="transcript")
    full_text = models.TextField()
    turns     = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "transcript"


# ─────────────────────────────────────────────
# CALL ANALYSIS (auto-generated by LLM post-call)
# ─────────────────────────────────────────────
class CallAnalysis(models.Model):
    id                     = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    call                   = models.OneToOneField(CallLog, on_delete=models.CASCADE, related_name="analysis")
    sentiment              = models.CharField(max_length=10, choices=SENTIMENT_CHOICES, blank=True)
    keywords               = models.JSONField(default=list)
    summary                = models.TextField(blank=True)
    conversion_probability = models.FloatField(null=True, blank=True)
    extra_kpi              = models.JSONField(default=dict, blank=True)
    analysed_at            = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "call_analysis"

    def __str__(self):
        return f"Analysis for call {self.call_id} | {self.sentiment}"


# ─────────────────────────────────────────────
# TICKET
# ─────────────────────────────────────────────
class Ticket(models.Model):
    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org         = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="tickets")
    call        = models.ForeignKey(CallLog, on_delete=models.SET_NULL, null=True, blank=True, related_name="tickets")
    customer    = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="tickets")
    issue_type  = models.CharField(max_length=100)
    status      = models.CharField(max_length=20, choices=TICKET_STATUS_CHOICES, default="open")
    description = models.TextField()
    assigned_to = models.CharField(max_length=255, blank=True)
    extra_data  = models.JSONField(default=dict, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ticket"

    def __str__(self):
        return f"[{self.issue_type}] {self.customer} — {self.status}"


# ─────────────────────────────────────────────
# MANUAL ANALYSIS (now auto — written by webhook)
# ─────────────────────────────────────────────
# class ManualAnalysis(models.Model):
#     """
#     Stores structured LLM analysis results per call.
#     Despite the legacy name, this is now ONLY written automatically
#     by the Bolna webhook handler. Manual transcript input is REMOVED.
#     """
#     CALL_TYPE_CHOICES = [('sales','Sales / New Prospect'),('service','Service / Existing Customer')]
#     ROUTING_CHOICES   = [('sales_only','Sales Only'),('service_only','Service Only'),('service_then_sales','Service → Sales Transfer')]

#     org          = models.ForeignKey('aos_agent.Organisation', on_delete=models.CASCADE, null=True, blank=True)
#     call         = models.ForeignKey('CallLog', on_delete=models.SET_NULL, null=True, blank=True, related_name="manual_analyses")
#     lead_name    = models.CharField(max_length=255, blank=True)
#     transcript   = models.TextField()
#     sentiment    = models.CharField(max_length=50)
#     lead_score   = models.IntegerField(default=0)
#     temperature  = models.CharField(max_length=20, default='Cold')
#     call_type    = models.CharField(max_length=10, choices=CALL_TYPE_CHOICES, default='sales')
#     routing_type = models.CharField(max_length=20, choices=ROUTING_CHOICES, default='sales_only', blank=True)
#     extra_data   = models.JSONField(default=dict, blank=True)
#     created_at   = models.DateTimeField(auto_now_add=True)

#     class Meta:
#         db_table = "manual_analysis"

#     def __str__(self):
#         return f"{self.lead_name} | {self.call_type} | {self.routing_type} | {self.created_at:%Y-%m-%d}"
    

"""
══════════════════════════════════════════════════════════════════
ADD THIS CLASS TO YOUR EXISTING aos_agent/models.py
══════════════════════════════════════════════════════════════════
Paste at the BOTTOM of models.py (after the Ticket model).
If ManualAnalysis already exists in your models.py, REPLACE it
with this version which adds call_type + extra_data fields.
"""

class ManualAnalysis(models.Model):
    CALL_TYPE_CHOICES = [
        ('sales',   'Sales / New Prospect'),
        ('service', 'Service / Existing Customer'),
    ]

    org         = models.ForeignKey(
                      'aos_agent.Organisation',
                      on_delete=models.CASCADE,
                      null=True, blank=True,
                  )
    lead_name   = models.CharField(max_length=255, blank=True)
    transcript  = models.TextField()
    sentiment   = models.CharField(max_length=50)
    lead_score  = models.IntegerField(default=0)
    temperature = models.CharField(max_length=20, default='Cold')
    call_type   = models.CharField(
                      max_length=10,
                      choices=CALL_TYPE_CHOICES,
                      default='sales',
                  )

    # Sales:   {"engagement": 70, "fit": 65, "conversion": 55}
    # Service: {"satisfaction": 40, "issue_severity": "High",
    #           "resolution_urgency": "High", "loyalty_risk": "Medium",
    #           "issue_category": "maintenance"}
    extra_data  = models.JSONField(default=dict, blank=True)

    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'aos_agent_manualanalysis'

    def __str__(self):
        return f"[{self.call_type}] {self.lead_name} — {self.temperature}"