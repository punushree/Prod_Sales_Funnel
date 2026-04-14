"""
Migration 0010: Team model + Org controls + Call routing
- Team model: Organisation → Teams → Users
- Organisation: approval workflow, user/agent/team limits, separate Bolna IDs
- User: team FK
- CallLog: team FK, routing_type
- ManualAnalysis: routing_type
"""

import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('aos_agent', '0009_manualanalysis_call_type_extra_data'),
    ]

    operations = [
        # ── 1. Team model ──
        migrations.CreateModel(
            name='Team',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('team_type', models.CharField(
                    choices=[('sales', 'Sales Team'), ('support', 'Support Team'), ('mixed', 'Mixed / General')],
                    default='mixed', max_length=10,
                )),
                ('bolna_agent_id', models.CharField(blank=True, max_length=255)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('org', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='teams', to='aos_agent.organisation',
                )),
            ],
            options={'db_table': 'team'},
        ),

        # ── 2. Organisation: approval + limits ──
        migrations.AddField(
            model_name='organisation',
            name='max_users',
            field=models.IntegerField(default=10),
        ),
        migrations.AddField(
            model_name='organisation',
            name='max_agents',
            field=models.IntegerField(default=5),
        ),
        migrations.AddField(
            model_name='organisation',
            name='max_teams',
            field=models.IntegerField(default=3),
        ),
        migrations.AddField(
            model_name='organisation',
            name='is_approved',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='organisation',
            name='approved_by',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='organisation',
            name='approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='organisation',
            name='sales_bolna_agent_id',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='organisation',
            name='service_bolna_agent_id',
            field=models.CharField(blank=True, max_length=255),
        ),

        # ── 3. User: team FK ──
        migrations.AddField(
            model_name='user',
            name='team',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='members', to='aos_agent.team',
            ),
        ),

        # ── 4. CallLog: team + routing ──
        migrations.AddField(
            model_name='calllog',
            name='team',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='calls', to='aos_agent.team',
            ),
        ),
        migrations.AddField(
            model_name='calllog',
            name='routing_type',
            field=models.CharField(
                blank=True, default='sales_only', max_length=20,
                choices=[
                    ('sales_only', 'Sales Only'),
                    ('service_only', 'Service Only'),
                    ('service_then_sales', 'Service → Sales Transfer'),
                ],
            ),
        ),

        # ── 5. ManualAnalysis: routing_type ──
        migrations.AddField(
            model_name='manualanalysis',
            name='routing_type',
            field=models.CharField(
                blank=True, default='sales_only', max_length=20,
                choices=[
                    ('sales_only', 'Sales Only'),
                    ('service_only', 'Service Only'),
                    ('service_then_sales', 'Service → Sales Transfer'),
                ],
            ),
        ),
    ]
