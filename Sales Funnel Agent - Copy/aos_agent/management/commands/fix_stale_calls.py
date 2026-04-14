"""
aos_agent/management/commands/fix_stale_calls.py
──────────────────────────────────────────────────────────────────────────────
Management command to resolve calls stuck in 'in_progress'.

Use this as a cron fallback if you don't have Celery Beat running:

    # Run every 10 minutes via crontab:
    */10 * * * * /path/to/venv/bin/python /path/to/manage.py fix_stale_calls

Usage:
    python manage.py fix_stale_calls
    python manage.py fix_stale_calls --minutes 30    # calls older than 30m
    python manage.py fix_stale_calls --dry-run       # preview only
──────────────────────────────────────────────────────────────────────────────
"""

from django.core.management.base import BaseCommand
from django.utils.timezone import now
from datetime import timedelta


class Command(BaseCommand):
    help = "Resolve calls stuck in in_progress by polling Bolna for their final status"

    def add_arguments(self, parser):
        parser.add_argument(
            "--minutes",
            type=int,
            default=15,
            help="Treat calls as stale if in_progress for more than N minutes (default: 15)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be changed without saving",
        )

    def handle(self, *args, **options):
        from aos_agent.models import CallLog
        from aos_agent.bolna_service import get_call_status

        minutes  = options["minutes"]
        dry_run  = options["dry_run"]
        cutoff   = now() - timedelta(minutes=minutes)

        stale = CallLog.objects.filter(
            status="in_progress",
            started_at__lt=cutoff,
        )

        self.stdout.write(
            self.style.WARNING(
                f"Found {stale.count()} stale in_progress call(s) older than {minutes} minutes"
            )
        )

        if dry_run:
            for call in stale:
                self.stdout.write(
                    f"  [DRY RUN] Would resolve: call {call.id} "
                    f"(bolna_id={call.bolna_call_id}, started={call.started_at})"
                )
            return

        resolved = 0
        for call in stale:
            duration   = 0
            new_status = "completed"

            if call.bolna_call_id:
                try:
                    result     = get_call_status(call.bolna_call_id)
                    b_status   = result.get("status", "").lower()
                    duration   = result.get("_duration_seconds", 0)

                    if b_status in ("failed", "error"):
                        new_status = "failed"
                    elif b_status in ("no-answer", "no_answer", "busy"):
                        new_status = "no_answer"
                    else:
                        new_status = "completed"

                    self.stdout.write(
                        f"  call {call.id}: Bolna says status={b_status}, duration={duration}s"
                    )
                except Exception as exc:
                    self.stdout.write(
                        self.style.ERROR(f"  call {call.id}: Bolna poll error — {exc}")
                    )
            else:
                self.stdout.write(
                    f"  call {call.id}: no bolna_call_id — marking completed"
                )

            update_fields = ["status", "ended_at"]
            call.status   = new_status
            call.ended_at = call.ended_at or now()

            if duration > 0:
                call.duration_seconds = duration
                update_fields.append("duration_seconds")

            call.save(update_fields=update_fields)
            resolved += 1

            self.stdout.write(
                self.style.SUCCESS(
                    f"  ✓ Resolved call {call.id} → {new_status} ({duration}s)"
                )
            )

        self.stdout.write(
            self.style.SUCCESS(f"\nDone. Resolved {resolved} stale call(s).")
        )
