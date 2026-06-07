import logging
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse

logger = logging.getLogger(__name__)


def send_supervisor_notification(report, request):
    """Send email to supervisor when an employee submits a period report."""
    supervisor = report.staff.supervisor
    if not supervisor:
        logger.warning(f"PeriodReport {report.id}: no supervisor assigned, skipping notification")
        return

    if not supervisor.email:
        logger.warning(
            f"PeriodReport {report.id}: supervisor {supervisor.get_full_name()} has no email, skipping notification"
        )
        return

    review_url = request.build_absolute_uri(
        reverse("timeeffort:supervisor_review", args=[report.id])
    )

    body = render_to_string(
        "timeeffort/email/supervisor_notification.txt",
        {
            "supervisor_name": supervisor.get_full_name() or supervisor.username,
            "employee_name": report.employee_name_snapshot,
            "period_label": report.period.label,
            "review_url": review_url,
        },
    )

    try:
        send_mail(
            subject=f"Time & Effort Report Ready for Review — {report.employee_name_snapshot}",
            message=body,
            from_email=settings.TIMEEFFORT_FROM_EMAIL,
            recipient_list=[supervisor.email],
            fail_silently=False,
        )
        logger.info(f"Supervisor notification sent to {supervisor.email} for PeriodReport {report.id}")
    except Exception:
        logger.exception(f"Failed to send supervisor notification for PeriodReport {report.id}")
