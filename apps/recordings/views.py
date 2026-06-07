from itertools import groupby

from django.db.models import Count, Prefetch
from django.views.generic import DetailView, ListView

from .models import Recording, VideoProgram


class ProgramListView(ListView):
    model = VideoProgram
    template_name = "recordings/program_list.html"
    context_object_name = "programs"
    paginate_by = 10

    def get_queryset(self):
        qs = (
            VideoProgram.objects.annotate(recording_count=Count("recordings"))
            .order_by("-start_date")
        )
        program_type = self.request.GET.get("type")
        if program_type in VideoProgram.ProgramType.values:
            qs = qs.filter(program_type=program_type)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["program_types"] = VideoProgram.ProgramType.choices
        ctx["active_type"] = self.request.GET.get("type", "")
        return ctx


class ProgramDetailView(DetailView):
    model = VideoProgram
    template_name = "recordings/program_detail.html"
    context_object_name = "program"

    def get_queryset(self):
        return VideoProgram.objects.prefetch_related(
            Prefetch(
                "recordings",
                queryset=Recording.objects.order_by("order", "recorded_at"),
            )
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        recordings = list(self.object.recordings.all())

        # Group by date so templates can render day headings without a Day model.
        # Each entry is (date, [Recording, ...]).
        grouped = [
            (date, list(group))
            for date, group in groupby(recordings, key=lambda r: r.recorded_at.date())
        ]
        ctx["recordings_by_day"] = grouped
        ctx["recording_count"] = len(recordings)
        ctx["is_multiday"] = len(grouped) > 1
        return ctx
