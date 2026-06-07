from django.shortcuts import render
from filer.models import File, Folder


def frg_landing(request):
    return render(request, "FRG/frg-landing.html")


def frg_papers(request):
    return render(request, "FRG/frg-papers.html")


def frg_activities(request):
    return render(request, "FRG/frg-activities.html")


def frg_resources(request):
    try:
        frg_folder = Folder.objects.get(name="FRG")
        pdf_files = File.objects.filter(folder=frg_folder, file__endswith=".pdf")
    except Folder.DoesNotExist:
        pdf_files = []
    return render(request, "FRG/frg-resources.html", {"pdf_files": pdf_files})
