from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("aos_agent", "0020_remove_manualanalysis_call_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="calllog",
            name="contact_phone",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Phone number dialled for this call",
                max_length=30,
            ),
            preserve_default=False,
        ),
    ]
