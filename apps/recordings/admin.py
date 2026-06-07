from django.contrib import admin
from django.db.models import Count
from django.utils.html import format_html

from .models import Recording, VideoProgram


class RecordingInline(admin.TabularInline):
    model = Recording
    extra = 1
    fields = ["order", "title", "speaker", "recorded_at", "vimeo_url", "description"]
    ordering = ["order", "recorded_at"]


@admin.register(VideoProgram)
class VideoProgramAdmin(admin.ModelAdmin):
    list_display = ["title", "program_type", "start_date", "vimeo_showcase_url", "recording_count"]
    list_filter = ["program_type", "start_date"]
    search_fields = ["title", "description"]
    prepopulated_fields = {"slug": ("title",)}
    autocomplete_fields = ["source_program"]
    ordering = ["-start_date"]
    inlines = [RecordingInline]

    fieldsets = (
        ("Source", {
            "description": (
                "Select an existing workshop to auto-fill title, date, and description. "
                "For non-workshop programs, leave blank and fill the fields below manually."
            ),
            "fields": ("source_program", "program_type"),
        }),
        ("Details (auto-filled from workshop if left blank)", {
            "fields": ("title", "start_date", "slug", "description"),
        }),
        ("Past workshop — Vimeo showcase", {
            "description": (
                "For past workshops: paste the Vimeo showcase URL here. "
                "The list page will link directly to Vimeo. "
                "Leave blank and add recordings below for individually catalogued talks."
            ),
            "fields": ("vimeo_showcase_url",),
        }),
    )

    class Media:
        js = ("recordings/admin_autofill.js",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("source_program").annotate(
            _recording_count=Count("recordings")
        )

    @admin.display(description="Recordings", ordering="_recording_count")
    def recording_count(self, obj):
        return obj._recording_count


@admin.register(Recording)
class RecordingAdmin(admin.ModelAdmin):
    list_display = ["title", "speaker", "program", "recorded_at", "order", "vimeo_link"]
    list_filter = ["program__program_type", "recorded_at"]
    search_fields = ["title", "speaker", "program__title"]
    raw_id_fields = ["program"]
    ordering = ["program", "order", "recorded_at"]

    @admin.display(description="Vimeo")
    def vimeo_link(self, obj):
        if obj.vimeo_id:
            return format_html('<a href="{}" target="_blank">&#9654; Watch</a>', obj.vimeo_url)
        return "—"
