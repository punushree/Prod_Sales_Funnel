from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("aos_agent", "0022_organisation_call_limit_seconds"),
    ]

    operations = [
        migrations.AddField(
            model_name="calllog",
            name="contact_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Customer/lead name at time of call",
                max_length=255,
            ),
            preserve_default=False,
        ),
    ]
