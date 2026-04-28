from django.shortcuts import get_object_or_404, render

from . import models
# Create your views here.


def index(request):
    return render(request, 'music/index.html')