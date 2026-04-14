"""
Migration 0022: Add call_limit_seconds to Organisation model

This enables per-organisation configurable call time limits.
Default: 120 seconds (2 minutes).

Apply with: python manage.py migrate
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        # Update this to match your latest migration number
        ("aos_agent", "0021_calllog_contact_phone"),
    ]

    operations = [
        migrations.AddField(
            model_name="organisation",
            name="call_limit_seconds",
            field=models.IntegerField(
                default=120,
                help_text=(
                    "Maximum call duration in seconds. "
                    "Call is gracefully stopped after this time. "
                    "Default: 120 (2 minutes). Set 0 to disable."
                ),
            ),
        ),
    ]
