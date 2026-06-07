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
    list_display = ["title", "program_type", "start_date", "source_program", "recording_count"]
    list_filter = ["program_type", "start_date"]
    search_fields = ["title", "description"]
    prepopulated_fields = {"slug": ("title",)}
    autocomplete_fields = ["source_program"]
    ordering = ["-start_date"]
    inlines = [RecordingInline]

    fieldsets = (
        (None, {
            "fields": ("source_program",),
            "description": (
                "Optional: link to an existing workshop/program record. "
                "Selecting one pre-fills the title and date below."
            ),
        }),
        ("Program details", {
            "fields": ("title", "program_type", "start_date", "slug", "description"),
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
