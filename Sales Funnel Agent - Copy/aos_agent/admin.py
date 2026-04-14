from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import (
    Organisation, AllowedPhoneNumber, OrgRequest,
    Team, User, Lead, Customer,
    CallLog, Transcript, CallAnalysis, Ticket, ManualAnalysis,
)


@admin.register(Organisation)
class OrganisationAdmin(admin.ModelAdmin):
    list_display  = ['name', 'industry', 'plan_type', 'is_approved', 'is_active',
                     'calls_used', 'call_quota', 'minutes_used', 'minutes_quota',
                     'number_quota', 'created_at']
    list_filter   = ['industry', 'plan_type', 'is_approved', 'is_active']
    search_fields = ['name']
    readonly_fields = ['calls_used', 'minutes_used', 'created_at', 'updated_at']
    fieldsets = (
        ('Basic Info',     {'fields': ('name', 'industry', 'plan_type', 'is_active')}),
        ('Approval',       {'fields': ('is_approved', 'approved_by', 'approved_at')}),
        ('Quotas',         {'fields': ('call_quota', 'calls_used', 'minutes_quota',
                                       'minutes_used', 'number_quota')}),
        ('Limits',         {'fields': ('max_users', 'max_agents', 'max_teams')}),
        ('Bolna Config',   {'fields': ('bolna_agent_id', 'sales_bolna_agent_id',
                                       'service_bolna_agent_id', 'phone_number')}),
        ('Prompts',        {'fields': ('sales_agent_prompt', 'service_agent_prompt')}),
    )
    actions = ['approve_orgs', 'reject_orgs']

    def approve_orgs(self, request, queryset):
        from django.utils import timezone
        queryset.update(is_approved=True, is_active=True,
                        approved_by=request.user.name,
                        approved_at=timezone.now())
        self.message_user(request, f"{queryset.count()} organisation(s) approved.")
    approve_orgs.short_description = "✅ Approve selected organisations"

    def reject_orgs(self, request, queryset):
        queryset.update(is_approved=False, is_active=False)
        self.message_user(request, f"{queryset.count()} organisation(s) rejected.")
    reject_orgs.short_description = "❌ Reject selected organisations"


@admin.register(AllowedPhoneNumber)
class AllowedPhoneNumberAdmin(admin.ModelAdmin):
    list_display  = ['phone', 'label', 'org', 'is_active', 'added_by', 'added_at']
    list_filter   = ['org', 'is_active']
    search_fields = ['phone', 'label', 'org__name']


@admin.register(OrgRequest)
class OrgRequestAdmin(admin.ModelAdmin):
    list_display  = ['org_name', 'contact_name', 'contact_email', 'industry',
                     'plan_type', 'status', 'created_at']
    list_filter   = ['status', 'industry']
    search_fields = ['org_name', 'contact_email']
    readonly_fields = ['created_at']


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display  = ['name', 'org', 'team_type', 'is_active', 'created_at']
    list_filter   = ['team_type', 'is_active', 'org']
    search_fields = ['name', 'org__name']


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display   = ['email', 'name', 'role', 'org', 'team', 'is_active', 'created_at']
    list_filter    = ['role', 'is_active', 'org']
    search_fields  = ['email', 'name']
    ordering       = ['email']
    filter_horizontal = ()          # ← custom User has no groups/user_permissions
    fieldsets = (
        (None,        {'fields': ('email', 'password')}),
        ('Info',      {'fields': ('name', 'role', 'org', 'team')}),
        ('Flags',     {'fields': ('is_active', 'is_staff', 'is_superuser')}),
    )
    add_fieldsets = (
        (None, {'classes': ('wide',), 'fields': ('email', 'name', 'password1', 'password2', 'role', 'org')}),
    )


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display  = ['name', 'phone', 'org', 'status', 'created_at']
    list_filter   = ['status', 'org']
    search_fields = ['name', 'phone', 'email']


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display  = ['name', 'phone', 'org', 'onboarded_at']
    list_filter   = ['org']
    search_fields = ['name', 'phone']


@admin.register(CallLog)
class CallLogAdmin(admin.ModelAdmin):
    list_display  = ['id', 'org', 'call_type', 'direction', 'status',
                     'duration_seconds', 'started_at', 'bolna_call_id']
    list_filter   = ['call_type', 'direction', 'status', 'org']
    search_fields = ['bolna_call_id']
    readonly_fields = ['id', 'created_at']


@admin.register(Transcript)
class TranscriptAdmin(admin.ModelAdmin):
    list_display  = ['call', 'created_at']
    readonly_fields = ['id', 'created_at']


@admin.register(CallAnalysis)
class CallAnalysisAdmin(admin.ModelAdmin):
    list_display  = ['call', 'sentiment', 'conversion_probability', 'analysed_at']
    list_filter   = ['sentiment']
    readonly_fields = ['id', 'analysed_at']


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display  = ['issue_type', 'customer', 'org', 'status', 'assigned_to', 'created_at']
    list_filter   = ['status', 'org']
    search_fields = ['issue_type', 'customer__name']


@admin.register(ManualAnalysis)
class ManualAnalysisAdmin(admin.ModelAdmin):
    list_display  = ['lead_name', 'call_type', 'sentiment',
                     'lead_score', 'temperature', 'org', 'created_at']
    list_filter   = ['call_type', 'sentiment', 'org']
    search_fields = ['lead_name']
    readonly_fields = ['created_at']