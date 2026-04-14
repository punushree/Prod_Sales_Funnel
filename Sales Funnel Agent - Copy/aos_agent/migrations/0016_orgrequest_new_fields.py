"""
0016 — Extend OrgRequest with:
  - org_email, org_phone, org_website, org_address  (Step 1 org details)
  - admin_username, admin_email                       (Step 2 admin details)
  - max_users                                         (quota field)
  - generated_password                                (shown once after submit)
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('aos_agent', '0015_remove_allowedphonenumber_unique_org_phone_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='orgrequest',
            name='org_email',
            field=models.EmailField(blank=True, default=''),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='orgrequest',
            name='org_phone',
            field=models.CharField(blank=True, max_length=20, default=''),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='orgrequest',
            name='org_website',
            field=models.CharField(blank=True, max_length=255, default=''),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='orgrequest',
            name='org_address',
            field=models.TextField(blank=True, default=''),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='orgrequest',
            name='admin_username',
            field=models.CharField(blank=True, max_length=150, default=''),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='orgrequest',
            name='admin_email',
            field=models.EmailField(blank=True, default=''),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='orgrequest',
            name='max_users',
            field=models.IntegerField(default=10),
        ),
        migrations.AddField(
            model_name='orgrequest',
            name='generated_password',
            field=models.CharField(blank=True, max_length=255, default=''),
            preserve_default=False,
        ),
    ]
