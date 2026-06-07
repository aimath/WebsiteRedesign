import re

from django.db import models
from django.urls import reverse
from django.utils.text import slugify


class VideoProgram(models.Model):
    class ProgramType(models.TextChoices):
        WORKSHOP = "workshop", "Workshop"
        PUBLIC_LECTURE = "lecture", "Public Lecture"
        EVENT = "event", "Event"

    # Optional link back to the canonical Program record. When set, the admin
    # uses it to pre-fill title/start_date. Fields remain editable so this model
    # stays self-contained if the source program is later deleted or renamed.
    source_program = models.ForeignKey(
        "programs.Workshop",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="video_programs",
        help_text="Link to the source workshop. Used to pre-fill title and date.",
    )
    title = models.CharField(max_length=255)
    program_type = models.CharField(
        max_length=20,
        choices=ProgramType.choices,
        db_index=True,
    )
    description = models.TextField(blank=True)
    start_date = models.DateField()
    slug = models.SlugField(unique=True, blank=True)

    class Meta:
        ordering = ["-start_date"]
        verbose_name = "Video Program"
        verbose_name_plural = "Video Programs"

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
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
