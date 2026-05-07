from .models import PeriodReport, StaffTimesheetProfile


def timeeffort_notifications(request):
    """
    Inject T&E alert counts into every template context.
    te_returned_count     — reports returned to this user by a supervisor.
    te_pending_approvals  — reports submitted by this user's subordinates, awaiting review.
    """
    if not request.user.is_authenticated:
        return {"te_returned_count": 0, "te_pending_approvals": 0}

    te_returned_count = 0
    try:
        profile = request.user.timesheet_profile
        te_returned_count = PeriodReport.objects.filter(
            staff=profile,
            status=PeriodReport.Status.RETURNED,
        ).count()
    except StaffTimesheetProfile.DoesNotExist:
        pass

    te_pending_approvals = PeriodReport.objects.filter(
        staff__supervisor=request.user,
        status=PeriodReport.Status.SUBMITTED,
    ).count()

    return {
        "te_returned_count": te_returned_count,
        "te_pending_approvals": te_pending_approvals,
    }
