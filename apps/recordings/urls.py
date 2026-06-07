from django.urls import path

from .views import ProgramDetailView, ProgramListView

app_name = "recordings"

urlpatterns = [
    path("", ProgramListView.as_view(), name="program-list"),
    path("<slug:slug>/", ProgramDetailView.as_view(), name="program-detail"),
]
