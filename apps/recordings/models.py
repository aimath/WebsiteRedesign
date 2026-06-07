import re

from django.db import models
from django.urls import reverse
from django.utils.text import slugify


class VideoProgram(models.Model):
    class ProgramType(models.TextChoices):
        WORKSHOP = "workshop", "Workshop"
        PUBLIC_LECTURE = "lecture", "Public Lecture"
        EVENT = "event", "Event"

    source_program = models.ForeignKey(
        "programs.Workshop",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="video_programs",
        help_text="Link to an existing workshop. Title, date, and description will be copied from it automatically.",
    )
    title = models.CharField(max_length=255, blank=True)
    program_type = models.CharField(
        max_length=20,
        choices=ProgramType.choices,
        default=ProgramType.WORKSHOP,
        db_index=True,
    )
    description = models.TextField(blank=True)
    start_date = models.DateField(null=True, blank=True)
    slug = models.SlugField(unique=True, blank=True)

    # For past workshops: paste the Vimeo showcase/album URL.
    # When set and no individual recordings exist, the list links directly here.
    vimeo_showcase_url = models.URLField(
        blank=True,
        help_text="Vimeo showcase or album URL for the full workshop (e.g. https://vimeo.com/showcase/XXXXXXX). "
                  "Use this for past workshops instead of adding individual recordings.",
    )

    class Meta:
        ordering = ["-start_date"]
        verbose_name = "Video Program"
        verbose_name_plural = "Video Programs"

    def __str__(self):
        return self.title or str(self.source_program) or f"VideoProgram {self.pk}"

    def save(self, *args, **kwargs):
        if self.source_program:
            if not self.title:
                self.title = self.source_program.title
            if not self.start_date:
                self.start_date = self.source_program.start_date
            if not self.description and self.source_program.description:
                self.description = self.source_program.description
            self.program_type = self.ProgramType.WORKSHOP

        if not self.slug and self.title:
            base = slugify(self.title)[:50]
            slug = base
            n = 1
            while VideoProgram.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug

        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("recordings:program-detail", kwargs={"slug": self.slug})

    @property
    def has_recordings(self):
        return self.recordings.exists()

    @property
    def vimeo_link(self):
        """The primary Vimeo destination: showcase URL if set, else None (use detail page)."""
        return self.vimeo_showcase_url or None


class Recording(models.Model):
    program = models.ForeignKey(
        VideoProgram,
        on_delete=models.CASCADE,
        related_name="recordings",
    )
    title = models.CharField(max_length=255)
    speaker = models.CharField(max_length=255)
    recorded_at = models.DateTimeField()
    vimeo_url = models.URLField(
        help_text="Full Vimeo URL, e.g. https://vimeo.com/123456789",
    )
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(
        default=0,
        help_text="Lower numbers appear first. Ties broken by recorded date.",
    )

    class Meta:
        ordering = ["order", "recorded_at"]
        verbose_name = "Recording"

    def __str__(self):
        return f"{self.title} — {self.speaker}"

    @property
    def vimeo_id(self):
        match = re.search(r"vimeo\.com/(?:video/)?(\d+)", self.vimeo_url)
        return match.group(1) if match else None

    @property
    def embed_url(self):
        vid = self.vimeo_id
        return f"https://player.vimeo.com/video/{vid}?title=0&byline=0&portrait=0" if vid else None
