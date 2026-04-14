"""
Migration 0008 — Seed dummy service customers
Run: python manage.py migrate
These are sample customers the service agent will call.
"""
from django.db import migrations


DUMMY_CUSTOMERS = [
    {"name": "Abhijit Thorat",  "phone":"7030878757", "tag":"paid"},
    {"name": "Rahul Sharma",    "phone": "9876543210", "tag": "paid"},
    {"name": "Priya Mehta",     "phone": "9823456789", "tag": "paid"},
    {"name": "Amit Verma",      "phone": "9812345678", "tag": "free"},
    {"name": "Sneha Patil",     "phone": "9845671234", "tag": "free"},
    {"name": "Rajesh Nair",     "phone": "9867891234", "tag": "paid"},
    {"name": "Kavya Reddy",     "phone": "9834567890", "tag": "free"},
    {"name": "Arjun Singh",     "phone": "9856781234", "tag": "paid"},
    {"name": "Pooja Iyer",      "phone": "9878901234", "tag": "untagged"},
    {"name": "Vikram Joshi",    "phone": "9890123456", "tag": "untagged"},
    {"name": "Ananya Gupta",    "phone": "9901234567", "tag": "paid"},
]


def seed_customers(apps, schema_editor):
    Organisation = apps.get_model("aos_agent", "Organisation")
    Customer     = apps.get_model("aos_agent", "Customer")

    # Get or create a default org to attach customers to
    org = Organisation.objects.filter(is_active=True).first()
    if org is None:
        org = Organisation.objects.create(
            name     = "Default Org",
            industry = "generic",
        )

    for c in DUMMY_CUSTOMERS:
        # Only create if phone doesn't already exist (idempotent)
        if not Customer.objects.filter(phone=c["phone"]).exists():
            Customer.objects.create(
                org        = org,
                name       = c["name"],
                phone      = c["phone"],
                extra_data = {"tag": c["tag"]},   # paid / free / untagged
            )


def unseed_customers(apps, schema_editor):
    Customer = apps.get_model("aos_agent", "Customer")
    phones   = [c["phone"] for c in DUMMY_CUSTOMERS]
    Customer.objects.filter(phone__in=phones).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("aos_agent", "0007_manualanalysis"),
    ]

    operations = [
        migrations.RunPython(seed_customers, reverse_code=unseed_customers),
    ]
