from django.contrib import admin

from .models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "person",
        "orcid",
        "email_verified",
    )
    autocomplete_fields = ("person",)
