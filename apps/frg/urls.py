from django.urls import path

from . import views

app_name = "frg"

urlpatterns = [
    path("", views.frg_landing, name="landing"),
    path("papers/", views.frg_papers, name="papers"),
    path("activities/", views.frg_activities, name="activities"),
    path("resources/", views.frg_resources, name="resources"),
]
