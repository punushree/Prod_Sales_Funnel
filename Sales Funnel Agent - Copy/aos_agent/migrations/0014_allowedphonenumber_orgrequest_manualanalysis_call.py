"""
Migration 0014: 
- Creates AllowedPhoneNumber table (org phone whitelist)
- Creates OrgRequest table (client org request form)  
- Adds call FK to ManualAnalysis
"""
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('aos_agent', '0013_alter_organisation_number_quota'),
    ]

    operations = [
        # ── AllowedPhoneNumber ────────────────────────────────────────
        migrations.CreateModel(
            name='AllowedPhoneNumber',
            fields=[
                ('id',        models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('org',       models.ForeignKey('aos_agent.Organisation', on_delete=django.db.models.deletion.CASCADE, related_name='allowed_numbers')),
                ('phone',     models.CharField(max_length=20, help_text='E.164 format, e.g. +919876543210')),
                ('label',     models.CharField(blank=True, max_length=255, help_text='Friendly name / lead name')),
                ('added_by',  models.CharField(blank=True, max_length=255)),
                ('is_active', models.BooleanField(default=True)),
                ('added_at',  models.DateTimeField(auto_now_add=True)),
            ],
            options={'db_table': 'allowed_phone_number'},
        ),
        migrations.AddConstraint(
            model_name='allowedphonenumber',
            constraint=models.UniqueConstraint(fields=['org', 'phone'], name='unique_org_phone'),
        ),

        # ── OrgRequest ────────────────────────────────────────────────
        migrations.CreateModel(
            name='OrgRequest',
            fields=[
                ('id',            models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('contact_name',  models.CharField(max_length=255)),
                ('contact_email', models.EmailField()),
                ('contact_phone', models.CharField(blank=True, max_length=20)),
                ('org_name',      models.CharField(max_length=255)),
                ('industry',      models.CharField(max_length=50, default='generic')),
                ('plan_type',     models.CharField(max_length=50, default='trial')),
                ('call_quota',    models.IntegerField(default=100)),
                ('minutes_quota', models.IntegerField(default=300)),
                ('number_quota',  models.PositiveIntegerField(default=4)),
                ('notes',         models.TextField(blank=True)),
                ('status',        models.CharField(max_length=10, default='pending')),
                ('reviewed_by',   models.CharField(blank=True, max_length=255)),
                ('reviewed_at',   models.DateTimeField(blank=True, null=True)),
                ('review_notes',  models.TextField(blank=True)),
                ('org',           models.OneToOneField(
                                      blank=True, null=True,
                                      on_delete=django.db.models.deletion.SET_NULL,
                                      related_name='request',
                                      to='aos_agent.organisation'
                                  )),
                ('created_at',    models.DateTimeField(auto_now_add=True)),
            ],
            options={'db_table': 'org_request'},
        ),

        # ── ManualAnalysis: add call FK ───────────────────────────────
        migrations.AddField(
            model_name='manualanalysis',
            name='call',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='manual_analyses',
                to='aos_agent.calllog',
            ),
        ),
    ]
