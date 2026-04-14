"""
Migration 0009: Add call_type and extra_data to ManualAnalysis
- call_type: 'sales' (new prospect) or 'service' (existing customer)
- extra_data: JSON field for storing type-specific KPI data
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('aos_agent', '0008_seed_service_customers'),
    ]

    operations = [
        migrations.AddField(
            model_name='manualanalysis',
            name='call_type',
            field=models.CharField(
                max_length=10,
                choices=[('sales', 'Sales / New Prospect'), ('service', 'Service / Existing Customer')],
                default='sales',
            ),
        ),
        migrations.AddField(
            model_name='manualanalysis',
            name='extra_data',
            field=models.JSONField(default=dict, blank=True),
        ),
    ]