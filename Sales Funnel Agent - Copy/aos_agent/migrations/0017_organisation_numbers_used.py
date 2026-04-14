"""
0017 — Add numbers_used to Organisation.

The MySQL table already has this column (added outside Django) but without a
DEFAULT value, causing OperationalError (1364) on every INSERT.
This migration syncs the model so Django includes the field in all queries.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('aos_agent', '0016_orgrequest_new_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='organisation',
            name='numbers_used',
            field=models.IntegerField(
                default=0,
                help_text='Cached count of active allowed phone numbers.',
            ),
        ),
    ]
