"""
python manage.py fix_call_durations

Backfills duration_seconds for any completed CallLog where:
  - duration_seconds is 0 or null
  - started_at AND ended_at are both set

Also backfills contact_name from AllowedPhoneNumber.label where
  - contact_name is blank
  - contact_phone is set
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from aos_agent.models import CallLog, AllowedPhoneNumber


class Command(BaseCommand):
    help = "Backfill duration_seconds and contact_name for existing calls"

    def handle(self, *args, **options):
        # ── Fix 1: duration_seconds ──────────────────────────────────
        qs = CallLog.objects.filter(
            status__in=("completed", "failed", "no_answer"),
            duration_seconds__lte=0,
            started_at__isnull=False,
            ended_at__isnull=False,
        )
        dur_fixed = 0
        for call in qs:
            secs = int((call.ended_at - call.started_at).total_seconds())
            if secs > 0:
                call.duration_seconds = secs
                call.save(update_fields=["duration_seconds"])
                dur_fixed += 1

        self.stdout.write(self.style.SUCCESS(
            f"✅ Fixed duration_seconds for {dur_fixed} calls"
        ))

        # ── Fix 2: contact_name from AllowedPhoneNumber.label ────────
        nameless = CallLog.objects.filter(
            contact_name="",
            contact_phone__isnull=False,
        ).exclude(contact_phone="")

        name_fixed = 0
        for call in nameless:
            phone = call.contact_phone.strip()
            # Try exact match first, then last-10-digit match
            apn = (
                AllowedPhoneNumber.objects.filter(phone=phone, org=call.org).first()
                or AllowedPhoneNumber.objects.filter(
                    phone__endswith=phone[-10:], org=call.org
                ).first() if len(phone) >= 10 else None
            )
            if apn and apn.label and apn.label.strip().lower() not in ("", "unknown", "customer"):
                call.contact_name = apn.label.strip()
                call.save(update_fields=["contact_name"])
                name_fixed += 1

        self.stdout.write(self.style.SUCCESS(
            f"✅ Fixed contact_name for {name_fixed} calls"
        ))

        self.stdout.write(self.style.SUCCESS("Done."))
