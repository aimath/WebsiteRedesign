from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.forms import modelformset_factory
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .emails import send_supervisor_notification
from .forms import (
    DirectorDefaultsForm,
    DirectorPeriodEntryForm,
    PeriodDescribeForm,
    ZeroWeekConfirmForm,
)
from .models import (
    Activity,
    AIMHoliday,
    DirectorDefaultAllocation,
    PDFSnapshot,
    PeriodReport,
    PeriodReportLine,
    ReportingPeriod,
    ReportingWeek,
    StaffTimesheetProfile,
    WeeklyTimesheet,
    WeeklyTimesheetLine,
)
from .services import (
    initialize_director_period_report,
    initialize_period_report,
    validate_period_percentages,
)

# Ordered day keys used throughout salary views and templates
SALARY_DAY_KEYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
SALARY_DAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

# Indirect/leave/unallowable slots for salary weekly entry
# (name, classification, SalaryIndirectAllocation field name)
SALARY_INDIRECT_SLOTS = [
    ("Administrative", Activity.Classification.INDIRECT, "hours_administrative"),
    ("Other Activity", Activity.Classification.INDIRECT, "hours_other_activity"),
    ("Sick / Personal Day", Activity.Classification.LEAVE, "hours_sick_personal"),
    ("Vacation", Activity.Classification.LEAVE, "hours_vacation"),
    ("Fundraising / PR", Activity.Classification.UNALLOWABLE, "hours_fundraising_pr"),
    (
        "Other Unallowable",
        Activity.Classification.UNALLOWABLE,
        "hours_other_unallowable",
    ),
]


def _get_year_filter(request):
    """
    Return (available_years, selected_year) based on existing ReportingPeriod data
    and the ?year= query param. Defaults to the current year.
    """
    available_years = list(
        ReportingPeriod.objects.values_list("start_date__year", flat=True)
        .distinct()
        .order_by("-start_date__year")
    )
    current_year = timezone.now().year
    try:
        selected_year = int(request.GET.get("year", current_year))
    except (ValueError, TypeError):
        selected_year = current_year
    if available_years and selected_year not in available_years:
        selected_year = available_years[0]  # most recent year with data
    return available_years, selected_year


def _get_staff_profile(request):
    """Returns the StaffTimesheetProfile for the logged-in user, or None."""
    try:
        return request.user.timesheet_profile
    except StaffTimesheetProfile.DoesNotExist:
        return None


# =============================================================================
# DASHBOARD
# =============================================================================


@login_required
def dashboard(request):
    profile = _get_staff_profile(request)
    if not profile:
        return render(request, "timeeffort/no_profile.html")

    if profile.is_director:
        return _director_dashboard(request, profile)
    if profile.is_salary:
        return _salary_dashboard(request, profile)
    return _hourly_dashboard(request, profile)


# =============================================================================
# WEEKLY ENTRY
# =============================================================================


@login_required
def weekly_entry(request, week_id):
    profile = _get_staff_profile(request)
    if not profile:
        raise Http404

    week = get_object_or_404(ReportingWeek, pk=week_id)

    if week.period.is_locked:
        messages.error(request, "This reporting period is locked.")
        return redirect("timeeffort:dashboard")

    if profile.is_director:
        raise Http404

    return _salary_weekly_entry(request, profile, week)


# =============================================================================
# SALARY WEEKLY ENTRY HELPERS
# =============================================================================


def _find_carry_forward_week(profile, week):
    """Return the best source week to carry forward from for salary entry.

    - week_number > 1: previous week in same period
    - week_number == 1: week 2 of previous period (works across 28-day boundary too)
    """
    if week.week_number > 1:
        return ReportingWeek.objects.filter(
            period=week.period, week_number=week.week_number - 1
        ).first()
    # week_number == 1 → go to previous period's week 2
    try:
        prev_period = ReportingPeriod.objects.get(
            calendar=week.period.calendar,
            period_index=week.period.period_index - 1,
        )
        return ReportingWeek.objects.filter(period=prev_period, week_number=2).first()
    except ReportingPeriod.DoesNotExist:
        return None


def _build_salary_rows(profile, week):
    """
    Build pre-populated row data for the salary weekly entry form.

    Priority: existing lines for this week → carry-forward from prior week → defaults.
    Custom lines are placed in 2 fixed slots.

    Returns (direct_rows, indirect_rows, custom_rows).
    """
    holiday_activity_ids = set(
        Activity.objects.filter(is_holiday_activity=True).values_list("id", flat=True)
    )
    holiday_dates = set(
        AIMHoliday.objects.filter(
            date__range=[week.start_date, week.end_date]
        ).values_list("date", flat=True)
    )
    day_dates = {
        d: week.start_date + timedelta(days=i) for i, d in enumerate(SALARY_DAY_KEYS)
    }

    # Existing timesheet lines for this week (if any)
    existing_ts = WeeklyTimesheet.objects.filter(staff=profile, week=week).first()
    existing_by_act = {}
    existing_custom = []
    has_existing_lines = False
    if existing_ts:
        lines_qs = list(existing_ts.lines.select_related("activity").all())
        has_existing_lines = bool(lines_qs)
        for line in lines_qs:
            if line.activity_id:
                existing_by_act[line.activity_id] = line
            else:
                existing_custom.append(line)

    # Carry-forward source: only used when no lines have been saved yet for this week
    cf_by_act = {}
    cf_custom = []
    if not has_existing_lines:
        cf_week = _find_carry_forward_week(profile, week)
        if cf_week:
            cf_ts = WeeklyTimesheet.objects.filter(staff=profile, week=cf_week).first()
            if cf_ts:
                for line in cf_ts.lines.select_related("activity").all():
                    if line.activity_id in holiday_activity_ids:
                        continue
                    if line.activity_id:
                        act = line.activity
                        if act.valid_to and act.valid_to < week.start_date:
                            continue
                        cf_by_act[line.activity_id] = line
                    else:
                        cf_custom.append(line)

    try:
        salary_indirect = profile.salary_indirect
    except Exception:
        salary_indirect = None

    def _get_hours(line, zero_holidays=False):
        h = {}
        for d in SALARY_DAY_KEYS:
            val = getattr(line, f"hours_{d}") or Decimal("0")
            if zero_holidays and day_dates[d] in holiday_dates:
                val = Decimal("0")
            h[d] = val
        return h

    def _zero_hours():
        return {d: Decimal("0") for d in SALARY_DAY_KEYS}

    def _pairs(h):
        """Convert hours dict to list of (day_key, value) pairs for template iteration."""
        return [(d, h[d]) for d in SALARY_DAY_KEYS]

    # --- Direct activity rows ---
    direct_activities = list(
        Activity.objects.filter(
            is_active=True,
            classification=Activity.Classification.DIRECT,
        )
        .exclude(id__in=holiday_activity_ids)
        .order_by("sort_order", "name")
    )
    direct_rows = []
    for act in direct_activities:
        if not act.is_valid_for_week(week.start_date, week.end_date):
            continue
        if act.id in existing_by_act:
            line = existing_by_act[act.id]
            hours = _get_hours(line)
            grant = line.grant_code
        elif act.id in cf_by_act:
            line = cf_by_act[act.id]
            hours = _get_hours(line, zero_holidays=True)
            grant = line.grant_code
        else:
            hours = _zero_hours()
            grant = act.default_grant_code
        direct_rows.append(
            {"activity": act, "grant_code": grant, "hours_pairs": _pairs(hours)}
        )

    # --- Indirect / leave / unallowable rows ---
    # Map activity name → SalaryIndirectAllocation field for default pre-fill
    _indirect_default_map = {name: field for name, _cls, field in SALARY_INDIRECT_SLOTS}

    indirect_activities = list(
        Activity.objects.filter(
            is_active=True,
            classification__in=[
                Activity.Classification.INDIRECT,
                Activity.Classification.LEAVE,
                Activity.Classification.UNALLOWABLE,
            ],
        ).order_by("sort_order", "name")
    )

    indirect_rows = []

    # Holiday activity: always shown first in indirect section; auto-fills 8h on holiday days.
    try:
        holiday_act = Activity.objects.get(is_holiday_activity=True, is_active=True)
        if holiday_act.id in existing_by_act:
            hours = _get_hours(existing_by_act[holiday_act.id])
        else:
            hours = _zero_hours()
            for d in SALARY_DAY_KEYS:
                if day_dates[d] in holiday_dates:
                    hours[d] = Decimal("8")
        indirect_rows.append({"activity": holiday_act, "hours_pairs": _pairs(hours)})
        holiday_activity_ids.add(holiday_act.id)
    except Activity.DoesNotExist:
        pass

    for act in indirect_activities:
        if act.id in holiday_activity_ids:
            continue
        if act.id in existing_by_act:
            line = existing_by_act[act.id]
            hours = _get_hours(line)
        elif act.id in cf_by_act:
            line = cf_by_act[act.id]
            hours = _get_hours(line, zero_holidays=True)
        else:
            default_field = _indirect_default_map.get(act.name)
            default_weekly = (
                getattr(salary_indirect, default_field, Decimal("0"))
                if salary_indirect and default_field
                else Decimal("0")
            )
            hours = _zero_hours()
            if default_weekly > 0:
                per_day = (default_weekly / 5).quantize(Decimal("0.25"))
                for d in ["mon", "tue", "wed", "thu", "fri"]:
                    if day_dates[d] not in holiday_dates:
                        hours[d] = per_day
        indirect_rows.append({"activity": act, "hours_pairs": _pairs(hours)})

    # --- Custom slots (2 fixed) ---
    custom_rows = []
    for slot_idx, slot_num in enumerate([1, 2]):
        if slot_idx < len(existing_custom):
            line = existing_custom[slot_idx]
            custom_rows.append(
                {
                    "slot": slot_num,
                    "name": line.custom_activity_name,
                    "grant_code": line.grant_code,
                    "hours_pairs": _pairs(_get_hours(line)),
                }
            )
        elif slot_idx < len(cf_custom):
            line = cf_custom[slot_idx]
            custom_rows.append(
                {
                    "slot": slot_num,
                    "name": line.custom_activity_name,
                    "grant_code": line.grant_code,
                    "hours_pairs": _pairs(_get_hours(line, zero_holidays=True)),
                }
            )
        else:
            custom_rows.append(
                {
                    "slot": slot_num,
                    "name": "",
                    "grant_code": "",
                    "hours_pairs": _pairs(_zero_hours()),
                }
            )

    return direct_rows, indirect_rows, custom_rows


def _save_salary_lines_from_post(post_data, timesheet):
    """
    Parse POST data from the salary weekly entry form and persist lines.
    Replaces all existing lines. Resets status to DRAFT.
    """
    timesheet.lines.all().delete()

    all_activities = Activity.objects.filter(is_active=True)

    for act in all_activities:
        prefix = f"act_{act.id}_"
        if not any(f"{prefix}{d}" in post_data for d in SALARY_DAY_KEYS):
            continue  # activity wasn't on this form
        grant_code = post_data.get(f"{prefix}grant", act.default_grant_code or "")
        hours = {}
        for d in SALARY_DAY_KEYS:
            val = post_data.get(f"{prefix}{d}", "").strip()
            try:
                hours[f"hours_{d}"] = Decimal(val) if val else Decimal("0")
            except Exception:
                hours[f"hours_{d}"] = Decimal("0")
        WeeklyTimesheetLine.objects.create(
            timesheet=timesheet,
            activity=act,
            grant_code=grant_code,
            **hours,
        )

    for slot in [1, 2]:
        name = post_data.get(f"custom_{slot}_name", "").strip()
        grant_code = post_data.get(f"custom_{slot}_grant", "").strip()
        hours = {}
        has_hours = False
        for d in SALARY_DAY_KEYS:
            val = post_data.get(f"custom_{slot}_{d}", "").strip()
            try:
                h = Decimal(val) if val else Decimal("0")
            except Exception:
                h = Decimal("0")
            hours[f"hours_{d}"] = h
            if h > 0:
                has_hours = True
        if name or has_hours:
            WeeklyTimesheetLine.objects.create(
                timesheet=timesheet,
                activity=None,
                custom_activity_name=name,
                grant_code=grant_code,
                **hours,
            )

    if timesheet.status == WeeklyTimesheet.Status.SUBMITTED:
        timesheet.status = WeeklyTimesheet.Status.DRAFT
        timesheet.save(update_fields=["status", "updated_at"])


def _salary_weekly_entry(request, profile, week):
    """Weekly entry for salary and hourly staff: pre-listed structured rows."""
    timesheet, _ = WeeklyTimesheet.objects.get_or_create(
        staff=profile,
        week=week,
        defaults={"status": WeeklyTimesheet.Status.DRAFT},
    )
    # Hourly: week 1 or 2 within a single 14-day period.
    # Salary: sequential 1-4 across the 28-day window.
    if profile.is_hourly:
        display_week_number = week.week_number
    else:
        display_week_number = (
            week.week_number
            if week.period.period_index % 2 == 0
            else week.week_number + 2
        )
    holiday_dates = set(
        AIMHoliday.objects.filter(
            date__range=[week.start_date, week.end_date]
        ).values_list("date", flat=True)
    )

    if request.method == "POST":
        action = request.POST.get("action", "save")

        if action == "confirm_zero":
            zero_form = ZeroWeekConfirmForm(request.POST)
            if zero_form.is_valid():
                _save_salary_lines_from_post(request.POST, timesheet)
                timesheet.zero_week_reason = zero_form.cleaned_data["zero_week_reason"]
                timesheet.submit()
                _invalidate_period_report_pdf(profile, week.period)
                messages.success(
                    request,
                    f"Week of {week.start_date} confirmed as zero-hour and submitted.",
                )
                return redirect("timeeffort:dashboard")
            direct_rows, indirect_rows, custom_rows = _build_salary_rows(profile, week)
            return render(
                request,
                "timeeffort/weekly_entry_salary.html",
                {
                    "timesheet": timesheet,
                    "week": week,
                    "profile": profile,
                    "direct_rows": direct_rows,
                    "indirect_rows": indirect_rows,
                    "custom_rows": custom_rows,
                    "zero_form": zero_form,
                    "show_zero_modal": True,
                    "day_labels": SALARY_DAY_LABELS,
                    "day_keys": SALARY_DAY_KEYS,
                    "holiday_dates": holiday_dates,
                },
            )

        _save_salary_lines_from_post(request.POST, timesheet)

        if action == "submit":
            total = timesheet.total_hours
            if total == 0:
                zero_form = ZeroWeekConfirmForm()
                direct_rows, indirect_rows, custom_rows = _build_salary_rows(
                    profile, week
                )
                return render(
                    request,
                    "timeeffort/weekly_entry_salary.html",
                    {
                        "timesheet": timesheet,
                        "week": week,
                        "profile": profile,
                        "direct_rows": direct_rows,
                        "indirect_rows": indirect_rows,
                        "custom_rows": custom_rows,
                        "zero_form": zero_form,
                        "show_zero_modal": True,
                        "day_labels": SALARY_DAY_LABELS,
                        "day_keys": SALARY_DAY_KEYS,
                        "holiday_dates": holiday_dates,
                        "display_week_number": display_week_number,
                    },
                )
            timesheet.submit()
            _invalidate_period_report_pdf(profile, week.period)
            messages.success(request, f"Week of {week.start_date} submitted.")
            return redirect("timeeffort:dashboard")

        messages.success(request, "Draft saved.")
        return redirect("timeeffort:weekly_entry", week_id=week.id)

    # GET
    direct_rows, indirect_rows, custom_rows = _build_salary_rows(profile, week)
    zero_form = ZeroWeekConfirmForm()
    return render(
        request,
        "timeeffort/weekly_entry_salary.html",
        {
            "timesheet": timesheet,
            "week": week,
            "profile": profile,
            "direct_rows": direct_rows,
            "indirect_rows": indirect_rows,
            "custom_rows": custom_rows,
            "zero_form": zero_form,
            "show_zero_modal": False,
            "day_labels": SALARY_DAY_LABELS,
            "day_keys": SALARY_DAY_KEYS,
            "holiday_dates": holiday_dates,
            "display_week_number": display_week_number,
        },
    )


@login_required
@require_POST
def copy_previous_period(request, period_id):
    """Copy all 4 weeks from the previous 28-day salary window into the current window."""
    profile = _get_staff_profile(request)
    if not profile or not profile.is_salary:
        raise Http404

    period = get_object_or_404(ReportingPeriod, pk=period_id)
    # Ensure we have the anchor (even period_index)
    if not period.is_salary_month_start:
        try:
            period = ReportingPeriod.objects.get(
                calendar=period.calendar, period_index=period.period_index - 1
            )
        except ReportingPeriod.DoesNotExist:
            messages.error(request, "Could not find anchor period.")
            return redirect("timeeffort:dashboard")

    prev_anchor_idx = period.period_index - 2
    try:
        prev_anchor = ReportingPeriod.objects.get(
            calendar=period.calendar, period_index=prev_anchor_idx
        )
    except ReportingPeriod.DoesNotExist:
        messages.error(request, "No previous period found to copy from.")
        return redirect("timeeffort:dashboard")

    prev_weeks = list(
        ReportingWeek.objects.filter(
            period__in=_get_salary_periods(prev_anchor)
        ).order_by("start_date")
    )
    curr_weeks = list(
        ReportingWeek.objects.filter(period__in=_get_salary_periods(period)).order_by(
            "start_date"
        )
    )

    if len(prev_weeks) != len(curr_weeks):
        messages.error(request, "Week count mismatch between periods.")
        return redirect("timeeffort:dashboard")

    holiday_activity_ids = set(
        Activity.objects.filter(is_holiday_activity=True).values_list("id", flat=True)
    )

    copied = 0
    for prev_week, curr_week in zip(prev_weeks, curr_weeks):
        prev_ts = WeeklyTimesheet.objects.filter(staff=profile, week=prev_week).first()
        if not prev_ts:
            continue

        curr_ts, _ = WeeklyTimesheet.objects.get_or_create(
            staff=profile,
            week=curr_week,
            defaults={"status": WeeklyTimesheet.Status.DRAFT},
        )

        holiday_dates_curr = set(
            AIMHoliday.objects.filter(
                date__range=[curr_week.start_date, curr_week.end_date]
            ).values_list("date", flat=True)
        )
        day_dates = {
            d: curr_week.start_date + timedelta(days=i)
            for i, d in enumerate(SALARY_DAY_KEYS)
        }

        curr_ts.lines.all().delete()
        for line in prev_ts.lines.select_related("activity").all():
            if line.activity_id and line.activity_id in holiday_activity_ids:
                continue
            if (
                line.activity
                and line.activity.valid_to
                and line.activity.valid_to < curr_week.start_date
            ):
                continue
            hours = {}
            for d in SALARY_DAY_KEYS:
                val = getattr(line, f"hours_{d}") or Decimal("0")
                if day_dates[d] in holiday_dates_curr:
                    val = Decimal("0")
                hours[f"hours_{d}"] = val
            WeeklyTimesheetLine.objects.create(
                timesheet=curr_ts,
                activity_id=line.activity_id,
                custom_activity_name=line.custom_activity_name,
                grant_code=line.grant_code,
                **hours,
            )

        if curr_ts.status == WeeklyTimesheet.Status.SUBMITTED:
            curr_ts.status = WeeklyTimesheet.Status.DRAFT
            curr_ts.save(update_fields=["status", "updated_at"])

        copied += 1

    _invalidate_period_report_pdf(profile, period)
    messages.success(request, f"Copied {copied} week(s) from previous period.")
    return redirect("timeeffort:dashboard")


# =============================================================================
# PERIOD SUMMARY
# =============================================================================


@login_required
def period_summary(request, period_id):
    profile = _get_staff_profile(request)
    if not profile:
        raise Http404

    period = get_object_or_404(ReportingPeriod, pk=period_id)

    if profile.is_salary:
        all_periods = _get_salary_periods(period)
        weeks = ReportingWeek.objects.filter(period__in=all_periods).order_by(
            "start_date"
        )
        # Ensure we always use the anchor (salary-month-start) period for the report lookup
        anchor_period = (
            all_periods.filter(
                period_index=(
                    period.period_index
                    if period.period_index % 2 == 0
                    else period.period_index - 1
                )
            ).first()
            or period
        )
        period_end = _get_28day_end(anchor_period)
        period_label = anchor_period.salary_month_label
    else:
        weeks = period.weeks.all()
        anchor_period = period
        period_end = period.end_date
        period_label = period.label

    week_statuses = []
    for week in weeks:
        ts = WeeklyTimesheet.objects.filter(staff=profile, week=week).first()
        week_statuses.append({"week": week, "timesheet": ts})

    all_submitted = bool(week_statuses) and all(
        ws["timesheet"] and ws["timesheet"].status == WeeklyTimesheet.Status.SUBMITTED
        for ws in week_statuses
    )

    report = PeriodReport.objects.filter(staff=profile, period=anchor_period).first()
    latest_pdf = None
    if report:
        latest_pdf = (
            report.pdfs.filter(pdf_type=PDFSnapshot.PDFType.FINAL)
            .order_by("-version")
            .first()
        )

    return render(
        request,
        "timeeffort/period_summary.html",
        {
            "period": anchor_period,
            "period_end": period_end,
            "period_label": period_label,
            "profile": profile,
            "week_statuses": week_statuses,
            "all_submitted": all_submitted,
            "report": report,
            "latest_pdf": latest_pdf,
        },
    )


# =============================================================================
# FINAL REPORT DESCRIBE + GENERATE
# =============================================================================


@login_required
def final_report_describe(request, period_id):
    """
    Intermediate step: show the rollup and let staff enter duties descriptions
    per activity before generating the PDF.
    """
    profile = _get_staff_profile(request)
    if not profile:
        raise Http404

    period = get_object_or_404(ReportingPeriod, pk=period_id)

    # Check weeks directly — PeriodReport may not exist yet on first visit.
    # Salary staff span two 14-day periods (28-day window); check all 4 weeks.
    if profile.is_salary:
        period_weeks = ReportingWeek.objects.filter(
            period__in=_get_salary_periods(period)
        )
    else:
        period_weeks = period.weeks.all()
    submitted_count = WeeklyTimesheet.objects.filter(
        staff=profile,
        week__in=period_weeks,
        status=WeeklyTimesheet.Status.SUBMITTED,
    ).count()

    if submitted_count < period_weeks.count():
        messages.error(
            request, "All weeks must be submitted before generating the final report."
        )
        return redirect("timeeffort:period_summary", period_id=period_id)

    # Get or initialize the period report
    report, _ = PeriodReport.objects.get_or_create(
        staff=profile,
        period=period,
        defaults={"submission_type": PeriodReport.SubmissionType.HOURS},
    )

    DescribeFormSet = modelformset_factory(
        PeriodReportLine,
        form=PeriodDescribeForm,
        extra=0,
    )

    if request.method == "POST":
        formset = DescribeFormSet(
            request.POST, queryset=report.lines.order_by("sort_order")
        )
        if formset.is_valid():
            formset.save()
            report.refresh_from_db()
            lines = report.lines.all()
            if not validate_period_percentages(
                [{"percentage": l.percentage} for l in lines]
            ):
                messages.error(
                    request,
                    f"Percentages do not sum to 100% (got "
                    f"{sum(l.percentage for l in lines):.2f}%). Adjust the percentages and try again.",
                )
                return redirect("timeeffort:final_report_describe", period_id=period_id)

            report.generated_at = timezone.now()
            report.save(update_fields=["generated_at", "updated_at"])
            report.submit()
            send_supervisor_notification(report, request)
            messages.success(request, "Report submitted successfully.")
            return redirect("timeeffort:period_summary", period_id=period_id)
    else:
        # Only re-initialize on GET — never on POST, to avoid wiping line IDs mid-submission.
        if report.status == PeriodReport.Status.DRAFT:
            report = initialize_period_report(report)
            _copy_prior_duties_descriptions(profile, report)
        formset = DescribeFormSet(queryset=report.lines.order_by("sort_order"))

    return render(
        request,
        "timeeffort/final_report_describe.html",
        {
            "period": period,
            "profile": profile,
            "report": report,
            "formset": formset,
        },
    )


# =============================================================================
# PDF DOWNLOAD
# =============================================================================


@login_required
def weekly_print(request, timesheet_id):
    if request.user.is_staff:
        timesheet = get_object_or_404(WeeklyTimesheet, pk=timesheet_id)
    else:
        profile = _get_staff_profile(request)
        if not profile:
            raise Http404
        timesheet = get_object_or_404(WeeklyTimesheet, pk=timesheet_id, staff=profile)
    lines = (
        timesheet.lines.select_related("activity")
        .order_by("activity__sort_order", "activity__name")
        .all()
    )
    return render(
        request,
        "timeeffort/weekly_report_print.html",
        {
            "timesheet": timesheet,
            "staff": timesheet.staff,
            "week": timesheet.week,
            "period": timesheet.week.period,
            "lines": lines,
            "total_hours": timesheet.total_hours,
            "day_labels": SALARY_DAY_LABELS,
            "generated_at": timezone.now(),
        },
    )


@login_required
def download_weekly_pdf(request, timesheet_id):
    # Redirect to the print/HTML view; server-side PDF not yet available.
    return redirect("timeeffort:weekly_print", timesheet_id=timesheet_id)


@login_required
def final_report_print(request, report_id):
    if request.user.is_staff:
        report = get_object_or_404(PeriodReport, pk=report_id)
    else:
        profile = _get_staff_profile(request)
        if not profile:
            raise Http404
        report = get_object_or_404(PeriodReport, pk=report_id, staff=profile)

    if not report.lines.exists():
        messages.error(request, "Generate the final report first before printing.")
        return redirect("timeeffort:period_summary", period_id=report.period_id)

    lines = report.lines.order_by("sort_order", "activity_name_snapshot").all()

    def has_value(ln):
        return bool(ln.percentage) or bool(ln.total_hours)

    direct = [ln for ln in lines if ln.classification_snapshot == Activity.Classification.DIRECT and has_value(ln)]
    indirect = [ln for ln in lines if ln.classification_snapshot == Activity.Classification.INDIRECT and has_value(ln)]
    leave = [ln for ln in lines if ln.classification_snapshot == Activity.Classification.LEAVE and has_value(ln)]
    unallowable = [ln for ln in lines if ln.classification_snapshot == Activity.Classification.UNALLOWABLE and has_value(ln)]

    weekly_data = []
    if report.submission_type == PeriodReport.SubmissionType.HOURS:
        # Use _get_salary_periods to mirror the period_summary view logic exactly.
        # report.covered_periods uses the same filter but going through _get_salary_periods
        # ensures consistent calendar/period_index handling even for legacy periods.
        salary_periods = _get_salary_periods(report.period)
        covered_weeks = ReportingWeek.objects.filter(
            period__in=salary_periods
        ).order_by("start_date")
        for week in covered_weeks:
            ts = WeeklyTimesheet.objects.filter(staff=report.staff, week=week).first()
            lines_qs = (
                [
                    ln for ln in ts.lines.select_related("activity").order_by("activity__sort_order", "activity__name")
                    if ln.total_hours > 0
                ]
                if ts
                else []
            )
            weekly_data.append(
                {
                    "week": week,
                    "timesheet": ts,
                    "lines": lines_qs,
                    "total": ts.total_hours if ts else Decimal("0"),
                }
            )

    period_end = (
        _get_28day_end(report.period)
        if not report.staff.is_hourly
        else report.period.end_date
    )

    return render(
        request,
        "timeeffort/final_report_print.html",
        {
            "report": report,
            "staff": report.staff,
            "period": report.period,
            "period_end": period_end,
            "direct_lines": direct,
            "indirect_lines": indirect,
            "leave_lines": leave,
            "unallowable_lines": unallowable,
            "weekly_data": weekly_data,
            "total_hours": report.total_hours,
            "is_pct_report": report.submission_type == PeriodReport.SubmissionType.PCT,
            "generated_at": timezone.now(),
            "day_labels": SALARY_DAY_LABELS,
        },
    )


@login_required
def download_final_pdf(request, report_id):
    # Redirect to the print/HTML view; PDF download not yet available server-side.
    return redirect("timeeffort:final_report_print", report_id=report_id)


def _serve_pdf(snapshot, filename):
    try:
        snapshot.file.open("rb")
        content = snapshot.file.read()
        snapshot.file.close()
    except Exception:
        raise Http404("PDF file not found.")

    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# =============================================================================
# DIRECTOR VIEWS
# =============================================================================


def _get_28day_end(period):
    """Return the end date of the 28-day window starting at period (even period_index)."""
    try:
        next_p = ReportingPeriod.objects.get(
            calendar=period.calendar,
            period_index=period.period_index + 1,
        )
        return next_p.end_date
    except ReportingPeriod.DoesNotExist:
        return period.end_date


def _get_salary_periods(period):
    """Return the two ReportingPeriods that form the 28-day salary window containing period."""
    anchor_idx = (
        period.period_index if period.period_index % 2 == 0 else period.period_index - 1
    )
    return ReportingPeriod.objects.filter(
        calendar=period.calendar,
        period_index__in=[anchor_idx, anchor_idx + 1],
    ).order_by("period_index")


def _copy_prior_duties_descriptions(profile, report):
    """Pre-fill duties_description on new report lines from the most recent prior period report."""
    prior = (
        PeriodReport.objects.filter(
            staff=profile,
            period__start_date__lt=report.period.start_date,
            status__in=[
                PeriodReport.Status.SUBMITTED,
                PeriodReport.Status.SUPERVISOR_APPROVED,
                PeriodReport.Status.PROCESSED,
                PeriodReport.Status.RETURNED,
            ],
        )
        .order_by("-period__start_date")
        .first()
    )
    if not prior:
        return
    desc_map = {
        ln.activity_name_snapshot: ln.duties_description
        for ln in prior.lines.all()
        if ln.duties_description
    }
    for line in report.lines.all():
        desc = desc_map.get(line.activity_name_snapshot, "")
        if desc and not line.duties_description:
            line.duties_description = desc
            line.save(update_fields=["duties_description"])


def _period_sort_key(summary):
    """
    Sort period cards with three buckets, most recent first within each:
      0 — returned (supervisor sent back, needs immediate action)
      1 — within 60 days of today (either direction) OR has any activity started
      2 — far away (past or future) AND completely untouched — sink to bottom

    Using abs distance so far-future generated periods don't float to the top.
    """
    period_end = summary["period_end"]
    report = summary["report"]
    today = timezone.now().date()

    if report and report.status == PeriodReport.Status.RETURNED:
        return (0, -period_end.toordinal())

    has_any_timesheet = any(
        ws.get("timesheet") is not None for ws in summary.get("week_statuses", [])
    )
    has_activity = has_any_timesheet or report is not None
    is_near = abs((period_end - today).days) <= 60

    if is_near or has_activity:
        return (1, -period_end.toordinal())

    return (2, -period_end.toordinal())


def _hourly_dashboard(request, profile):
    """Hourly staff dashboard: one 14-day period per card, 2 weekly slots each."""
    available_years, selected_year = _get_year_filter(request)

    periods = (
        ReportingPeriod.objects.filter(start_date__year=selected_year)
        .order_by("-start_date")
        .prefetch_related("weeks")
    )

    period_summaries = []
    for period in periods:
        weeks = list(period.weeks.order_by("week_number"))
        submitted_ids = set(
            WeeklyTimesheet.objects.filter(
                staff=profile,
                week__in=weeks,
                status=WeeklyTimesheet.Status.SUBMITTED,
            ).values_list("week_id", flat=True)
        )

        week_statuses = []
        for week in weeks:
            ts = WeeklyTimesheet.objects.filter(staff=profile, week=week).first()
            week_statuses.append(
                {"week": week, "timesheet": ts, "display_number": week.week_number}
            )

        outstanding = [ws for ws in week_statuses if ws["week"].id not in submitted_ids]
        report = PeriodReport.objects.filter(staff=profile, period=period).first()

        period_summaries.append(
            {
                "period": period,
                "period_label": period.label,
                "period_end": period.end_date,
                "total_weeks": len(weeks),
                "submitted_count": len(submitted_ids),
                "outstanding_weeks": outstanding,
                "week_statuses": week_statuses,
                "report": report,
            }
        )

    period_summaries.sort(key=_period_sort_key)

    _SUBMITTED_STATUSES = [
        PeriodReport.Status.SUBMITTED,
        PeriodReport.Status.SUPERVISOR_APPROVED,
        PeriodReport.Status.PROCESSED,
        PeriodReport.Status.RETURNED,
    ]
    returned_reports = (
        PeriodReport.objects.filter(staff=profile, status=PeriodReport.Status.RETURNED)
        .select_related("period")
        .order_by("-returned_at")
    )
    recent_reports = (
        PeriodReport.objects.filter(staff=profile, status__in=_SUBMITTED_STATUSES)
        .exclude(status=PeriodReport.Status.RETURNED)
        .select_related("period")
        .order_by("-period__start_date")[:3]
    )

    return render(
        request,
        "timeeffort/dashboard_weekly.html",
        {
            "profile": profile,
            "period_summaries": period_summaries,
            "returned_reports": returned_reports,
            "recent_reports": recent_reports,
            "available_years": available_years,
            "selected_year": selected_year,
            "is_salary": False,
        },
    )


def _salary_dashboard(request, profile):
    """Salary dashboard: groups periods into 28-day windows, shows all 4 weekly slots each."""
    available_years, selected_year = _get_year_filter(request)

    candidates = list(
        ReportingPeriod.objects.filter(start_date__year=selected_year).order_by(
            "-start_date"
        )
    )
    active_pairs = [p for p in candidates if p.is_salary_month_start]

    period_summaries = []
    for period in active_pairs:
        end_date = _get_28day_end(period)
        all_periods = _get_salary_periods(period)
        all_weeks = ReportingWeek.objects.filter(period__in=all_periods).order_by(
            "start_date"
        )

        submitted_ids = set(
            WeeklyTimesheet.objects.filter(
                staff=profile,
                week__in=all_weeks,
                status=WeeklyTimesheet.Status.SUBMITTED,
            ).values_list("week_id", flat=True)
        )
        report = PeriodReport.objects.filter(staff=profile, period=period).first()

        week_statuses = []
        for display_num, week in enumerate(all_weeks, start=1):
            ts = WeeklyTimesheet.objects.filter(staff=profile, week=week).first()
            week_statuses.append(
                {"week": week, "timesheet": ts, "display_number": display_num}
            )

        outstanding = [
            ws
            for ws in week_statuses
            if not ws["timesheet"]
            or ws["timesheet"].status != WeeklyTimesheet.Status.SUBMITTED
        ]

        has_prev = ReportingPeriod.objects.filter(
            calendar=period.calendar,
            period_index=period.period_index - 2,
        ).exists()
        holiday_count = AIMHoliday.objects.filter(
            date__range=[period.start_date, end_date]
        ).count()
        period_summaries.append(
            {
                "period": period,
                "period_label": period.salary_month_label,
                "period_end": end_date,
                "total_weeks": all_weeks.count(),
                "submitted_count": len(submitted_ids),
                "outstanding_weeks": outstanding,
                "week_statuses": week_statuses,
                "report": report,
                "has_prev": has_prev,
                "holiday_count": holiday_count,
                "has_submitted": len(submitted_ids) == all_weeks.count(),
            }
        )

    period_summaries.sort(key=_period_sort_key)

    _SUBMITTED_STATUSES = [
        PeriodReport.Status.SUBMITTED,
        PeriodReport.Status.SUPERVISOR_APPROVED,
        PeriodReport.Status.PROCESSED,
        PeriodReport.Status.RETURNED,
    ]
    returned_reports = (
        PeriodReport.objects.filter(staff=profile, status=PeriodReport.Status.RETURNED)
        .select_related("period")
        .order_by("-returned_at")
    )
    recent_reports = (
        PeriodReport.objects.filter(staff=profile, status__in=_SUBMITTED_STATUSES)
        .exclude(status=PeriodReport.Status.RETURNED)
        .select_related("period")
        .order_by("-period__end_date")[:5]
    )

    return render(
        request,
        "timeeffort/dashboard_weekly.html",
        {
            "profile": profile,
            "period_summaries": period_summaries,
            "is_salary": True,
            "returned_reports": returned_reports,
            "recent_reports": recent_reports,
            "available_years": available_years,
            "selected_year": selected_year,
        },
    )


def _invalidate_period_report_pdf(profile, period):
    """Clear the generated final PDF for the PeriodReport covering this period.

    Called after a weekly timesheet is re-submitted so the next download triggers
    a fresh rollup and PDF regeneration.
    """
    if not profile.is_hourly:
        anchor_idx = (
            period.period_index
            if period.period_index % 2 == 0
            else period.period_index - 1
        )
        try:
            anchor_period = ReportingPeriod.objects.get(
                calendar=period.calendar, period_index=anchor_idx
            )
        except ReportingPeriod.DoesNotExist:
            return
    else:
        anchor_period = period

    report = PeriodReport.objects.filter(staff=profile, period=anchor_period).first()
    if report and report.generated_at is not None:
        report.pdfs.filter(pdf_type=PDFSnapshot.PDFType.FINAL).delete()
        report.generated_at = None
        report.status = PeriodReport.Status.DRAFT
        report.save(update_fields=["generated_at", "status", "updated_at"])


def _director_dashboard(request, profile):
    """Director dashboard: shows 28-day period pairs only (no weekly timesheets)."""
    available_years, selected_year = _get_year_filter(request)

    candidates = list(
        ReportingPeriod.objects.filter(start_date__year=selected_year).order_by(
            "-start_date"
        )
    )
    # Only even period_index periods (start of 28-day salary month)
    active_pairs = [p for p in candidates if p.is_salary_month_start]

    period_summaries = []
    for period in active_pairs:
        report = PeriodReport.objects.filter(staff=profile, period=period).first()
        end_date = _get_28day_end(period)
        holiday_count = AIMHoliday.objects.filter(
            date__range=[period.start_date, end_date]
        ).count()
        period_summaries.append(
            {
                "period": period,
                "period_label": period.salary_month_label,
                "period_end": end_date,
                "report": report,
                "holiday_count": holiday_count,
                "holiday_pct": holiday_count * 5,
            }
        )

    period_summaries.sort(key=_period_sort_key)

    has_defaults = DirectorDefaultAllocation.objects.filter(profile=profile).exists()
    returned_reports = (
        PeriodReport.objects.filter(staff=profile, status=PeriodReport.Status.RETURNED)
        .select_related("period")
        .order_by("-returned_at")
    )
    recent_reports = (
        PeriodReport.objects.filter(
            staff=profile,
            status__in=[
                PeriodReport.Status.SUBMITTED,
                PeriodReport.Status.SUPERVISOR_APPROVED,
                PeriodReport.Status.PROCESSED,
            ],
        )
        .select_related("period")
        .order_by("-period__start_date")[:5]
    )

    return render(
        request,
        "timeeffort/dashboard_director.html",
        {
            "profile": profile,
            "period_summaries": period_summaries,
            "has_defaults": has_defaults,
            "returned_reports": returned_reports,
            "recent_reports": recent_reports,
            "available_years": available_years,
            "selected_year": selected_year,
        },
    )


@login_required
def director_period_entry(request, period_id):
    profile = _get_staff_profile(request)
    if not profile or not profile.is_director:
        raise Http404

    period = get_object_or_404(ReportingPeriod, pk=period_id)

    if not period.is_salary_month_start:
        messages.error(request, "Invalid period for a director report.")
        return redirect("timeeffort:dashboard")

    if period.is_locked:
        messages.error(request, "This reporting period is locked.")
        return redirect("timeeffort:dashboard")

    end_date = _get_28day_end(period)
    holiday_count = AIMHoliday.objects.filter(
        date__range=[period.start_date, end_date]
    ).count()
    holiday_pct = Decimal(str(holiday_count * 5))
    holidays = AIMHoliday.objects.filter(date__range=[period.start_date, end_date])

    report, _ = PeriodReport.objects.get_or_create(
        staff=profile,
        period=period,
        defaults={"submission_type": PeriodReport.SubmissionType.PCT},
    )

    if request.method == "POST":
        action = request.POST.get("action", "save")
        form = DirectorPeriodEntryForm(request.POST, holiday_pct=holiday_pct)
        if form.is_valid():
            _save_director_report_lines(
                report,
                form.cleaned_data,
                holiday_pct,
                form.cleaned_data.get("main_grant_code", ""),
            )
            if action == "submit":
                report.submit()
                send_supervisor_notification(report, request)
                messages.success(
                    request,
                    f"Effort report for {period.salary_month_label} submitted.",
                )
                return redirect("timeeffort:dashboard")
            messages.success(request, "Draft saved.")
            return redirect("timeeffort:director_period_entry", period_id=period.id)
    else:
        if report.lines.exists():
            initial = _report_lines_to_form_data(report)
        else:
            initialize_director_period_report(report, holiday_pct=holiday_pct)
            initial = _report_lines_to_form_data(report)
        initial.setdefault("pct_holiday", holiday_pct)
        form = DirectorPeriodEntryForm(initial=initial, holiday_pct=holiday_pct)

    return render(
        request,
        "timeeffort/director_period_entry.html",
        {
            "profile": profile,
            "period": period,
            "report": report,
            "end_date": end_date,
            "form": form,
            "holiday_count": holiday_count,
            "holiday_pct": holiday_pct,
            "holidays": holidays,
        },
    )


@login_required
def download_director_pdf(request, submission_id):
    """submission_id is a PeriodReport pk (name kept for URL compatibility)."""
    # Redirect to the print/HTML view; server-side PDF not yet available.
    return redirect("timeeffort:final_report_print", report_id=submission_id)


# --- Director helpers ---


def _save_director_report_lines(report, cleaned, holiday_pct, main_grant_code=""):
    """Persist DirectorPeriodEntryForm data as PeriodReportLine objects."""
    from .models import Activity

    report.lines.all().delete()
    sort = 0

    main_pct = cleaned.get("main_grant_pct") or Decimal("0")
    PeriodReportLine.objects.create(
        period_report=report,
        activity_name_snapshot=f"Direct — {main_grant_code or 'Main Grant'}",
        grant_code_snapshot=main_grant_code,
        classification_snapshot=Activity.Classification.DIRECT,
        total_hours=None,
        percentage=main_pct,
        duties_description=cleaned.get("main_grant_desc", ""),
        sort_order=sort,
    )
    sort += 1

    for i in range(1, 5):
        code = cleaned.get(f"extra_grant_code_{i}", "")
        pct = cleaned.get(f"extra_grant_pct_{i}") or Decimal("0")
        if code or pct > 0:
            PeriodReportLine.objects.create(
                period_report=report,
                activity_name_snapshot=f"Direct — {code or f'Grant {i}'}",
                grant_code_snapshot=code,
                classification_snapshot=Activity.Classification.DIRECT,
                total_hours=None,
                percentage=pct,
                duties_description=cleaned.get(f"extra_grant_desc_{i}", ""),
                sort_order=sort,
            )
            sort += 1

    indirect_rows = [
        (
            "Administrative",
            "pct_administrative",
            "desc_administrative",
            Activity.Classification.INDIRECT,
        ),
        (
            "Other Activity",
            "pct_other_activity",
            "desc_other_activity",
            Activity.Classification.INDIRECT,
        ),
        (
            "Sick / Personal Day",
            "pct_sick_personal",
            "desc_sick_personal",
            Activity.Classification.LEAVE,
        ),
        ("Vacation", "pct_vacation", "desc_vacation", Activity.Classification.LEAVE),
        (
            "Employer Holiday",
            "pct_holiday",
            "desc_holiday",
            Activity.Classification.INDIRECT,
        ),
        (
            "Fundraising / PR",
            "pct_fundraising_pr",
            "desc_fundraising_pr",
            Activity.Classification.UNALLOWABLE,
        ),
        (
            "Other Unallowable",
            "pct_other_unallowable",
            "desc_other_unallowable",
            Activity.Classification.UNALLOWABLE,
        ),
    ]
    for label, pct_field, desc_field, classification in indirect_rows:
        pct = cleaned.get(pct_field) or Decimal("0")
        if pct > 0:
            PeriodReportLine.objects.create(
                period_report=report,
                activity_name_snapshot=label,
                grant_code_snapshot="",
                classification_snapshot=classification,
                total_hours=None,
                percentage=pct,
                duties_description=cleaned.get(desc_field, ""),
                sort_order=sort,
            )
            sort += 1


def _report_lines_to_form_data(report):
    """Read PeriodReportLine objects back into DirectorPeriodEntryForm initial data."""
    from .models import Activity

    data = {}
    extra_idx = 1
    for line in report.lines.order_by("sort_order"):
        if line.classification_snapshot == Activity.Classification.DIRECT:
            if "main_grant_pct" not in data:
                data["main_grant_code"] = line.grant_code_snapshot
                data["main_grant_pct"] = line.percentage
                data["main_grant_desc"] = line.duties_description
            elif extra_idx <= 4:
                data[f"extra_grant_code_{extra_idx}"] = line.grant_code_snapshot
                data[f"extra_grant_pct_{extra_idx}"] = line.percentage
                data[f"extra_grant_desc_{extra_idx}"] = line.duties_description
                extra_idx += 1
        elif line.activity_name_snapshot == "Employer Holiday":
            data["pct_holiday"] = line.percentage
            data["desc_holiday"] = line.duties_description
        else:
            label_map = {
                "Administrative": ("pct_administrative", "desc_administrative"),
                "Other Activity": ("pct_other_activity", "desc_other_activity"),
                "Sick / Personal Day": ("pct_sick_personal", "desc_sick_personal"),
                "Vacation": ("pct_vacation", "desc_vacation"),
                "Fundraising / PR": ("pct_fundraising_pr", "desc_fundraising_pr"),
                "Other Unallowable": (
                    "pct_other_unallowable",
                    "desc_other_unallowable",
                ),
            }
            if line.activity_name_snapshot in label_map:
                pct_f, desc_f = label_map[line.activity_name_snapshot]
                data[pct_f] = line.percentage
                data[desc_f] = line.duties_description
    return data


@login_required
def director_set_defaults(request):
    profile = _get_staff_profile(request)
    if not profile or profile.staff_type != StaffTimesheetProfile.StaffType.DIRECTOR:
        raise Http404

    defaults, _ = DirectorDefaultAllocation.objects.get_or_create(profile=profile)

    if request.method == "POST":
        form = DirectorDefaultsForm(request.POST, instance=defaults)
        if form.is_valid():
            form.save()
            messages.success(request, "Default allocations saved.")
            return redirect("timeeffort:dashboard")
    else:
        form = DirectorDefaultsForm(instance=defaults)

    return render(
        request,
        "timeeffort/director_defaults.html",
        {
            "profile": profile,
            "form": form,
        },
    )


# --- Director helpers ---


def _defaults_to_form_data(profile, holiday_pct):
    try:
        d = profile.director_defaults
        data = {
            "main_grant_code": d.main_grant_code,
            "main_grant_pct": d.main_grant_pct,
            "pct_administrative": max(Decimal("0"), d.pct_administrative - holiday_pct),
            "pct_other_activity": d.pct_other_activity,
            "pct_sick_personal": d.pct_sick_personal,
            "pct_vacation": d.pct_vacation,
            "pct_fundraising_pr": d.pct_fundraising_pr,
            "pct_other_unallowable": d.pct_other_unallowable,
        }
        for i in range(1, 5):
            data[f"extra_grant_code_{i}"] = getattr(d, f"extra_grant_code_{i}", "")
            data[f"extra_grant_pct_{i}"] = getattr(
                d, f"extra_grant_pct_{i}", Decimal("0")
            )
        return data
    except DirectorDefaultAllocation.DoesNotExist:
        return {}


# =============================================================================
# CFO / ADMIN — ALL REPORTS PORTAL
# =============================================================================


@login_required
def all_reports(request):
    if not request.user.has_perm("timeeffort.view_all_reports"):
        raise Http404

    available_years, selected_year = _get_year_filter(request)
    staff_type_filter = request.GET.get("staff_type", "")

    reports = (
        PeriodReport.objects.filter(
            status__in=[
                PeriodReport.Status.SUBMITTED,
                PeriodReport.Status.SUPERVISOR_APPROVED,
                PeriodReport.Status.RETURNED,
                PeriodReport.Status.PROCESSED,
            ],
            period__start_date__year=selected_year,
        )
        .select_related("staff__user", "period")
        .order_by("-period__start_date", "employee_name_snapshot")
    )

    if staff_type_filter:
        reports = reports.filter(staff__staff_type=staff_type_filter)

    return render(
        request,
        "timeeffort/all_reports.html",
        {
            "reports": reports,
            "available_years": available_years,
            "selected_year": selected_year,
            "staff_type_filter": staff_type_filter,
            "staff_type_choices": StaffTimesheetProfile.StaffType.choices,
        },
    )


# =============================================================================
# SUPERVISOR VIEWS
# =============================================================================


@login_required
def supervisor_queue(request):
    """Period reports submitted by the current user's direct reports, awaiting review."""
    pending = (
        PeriodReport.objects.filter(
            staff__supervisor=request.user,
            status=PeriodReport.Status.SUBMITTED,
        )
        .select_related("staff__user", "period")
        .order_by("employee_name_snapshot", "-submitted_at")
    )
    recent = (
        PeriodReport.objects.filter(
            staff__supervisor=request.user,
            status__in=[
                PeriodReport.Status.SUPERVISOR_APPROVED,
                PeriodReport.Status.RETURNED,
                PeriodReport.Status.PROCESSED,
            ],
        )
        .select_related("staff__user", "period")
        .order_by("-updated_at")[:20]
    )
    return render(
        request,
        "timeeffort/supervisor_queue.html",
        {
            "pending": pending,
            "recent": recent,
        },
    )


@login_required
def supervisor_review(request, report_id):
    """Supervisor reviews a specific period report; can approve or return with notes."""
    report = get_object_or_404(
        PeriodReport,
        pk=report_id,
        staff__supervisor=request.user,
    )

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "approve" and report.status == PeriodReport.Status.SUBMITTED:
            report.supervisor_approve(request.user)
            messages.success(
                request, f"Report for {report.employee_name_snapshot} approved."
            )
            return redirect("timeeffort:supervisor_queue")
        elif action == "return":
            notes = request.POST.get("supervisor_notes", "").strip()
            if not notes:
                messages.error(
                    request, "Please provide a reason for returning the report."
                )
            else:
                report.return_report(request.user, notes)
                messages.success(
                    request,
                    f"Report returned to {report.employee_name_snapshot} with comments.",
                )
                return redirect("timeeffort:supervisor_queue")

    lines = report.lines.order_by("sort_order", "activity_name_snapshot").all()

    def has_value(ln):
        return bool(ln.percentage) or bool(ln.total_hours)

    direct = [ln for ln in lines if ln.classification_snapshot == Activity.Classification.DIRECT and has_value(ln)]
    indirect = [ln for ln in lines if ln.classification_snapshot == Activity.Classification.INDIRECT and has_value(ln)]
    leave = [ln for ln in lines if ln.classification_snapshot == Activity.Classification.LEAVE and has_value(ln)]
    unallowable = [ln for ln in lines if ln.classification_snapshot == Activity.Classification.UNALLOWABLE and has_value(ln)]

    weekly_data = []
    if report.submission_type == PeriodReport.SubmissionType.HOURS:
        salary_periods = _get_salary_periods(report.period)
        covered_weeks = ReportingWeek.objects.filter(
            period__in=salary_periods
        ).order_by("start_date")
        for week in covered_weeks:
            ts = WeeklyTimesheet.objects.filter(staff=report.staff, week=week).first()
            lines_qs = (
                [
                    ln for ln in ts.lines.select_related("activity").order_by("activity__sort_order", "activity__name")
                    if ln.total_hours > 0
                ]
                if ts
                else []
            )
            weekly_data.append(
                {
                    "week": week,
                    "timesheet": ts,
                    "lines": lines_qs,
                    "total": ts.total_hours if ts else Decimal("0"),
                }
            )

    period_end = (
        _get_28day_end(report.period)
        if not report.staff.is_hourly
        else report.period.end_date
    )

    return render(
        request,
        "timeeffort/supervisor_review.html",
        {
            "report": report,
            "period": report.period,
            "period_end": period_end,
            "direct_lines": direct,
            "indirect_lines": indirect,
            "leave_lines": leave,
            "unallowable_lines": unallowable,
            "weekly_data": weekly_data,
            "total_hours": report.total_hours,
            "is_pct_report": report.submission_type == PeriodReport.SubmissionType.PCT,
            "day_labels": SALARY_DAY_LABELS,
            "generated_at": timezone.now(),
        },
    )
