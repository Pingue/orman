from django.shortcuts import get_object_or_404, render, HttpResponse
from django.http import HttpResponseBadRequest
from django.contrib import messages
from icecream import ic as print


from . import models
# Create your views here.


def index(request):
    people=models.Person.objects.all()
    return render(request, 'index.html', {"people":people})


def admin_instrument_family(request, id=None):
    print(id)
    print(type(id))
    print(request.method)
    if request.method == "POST":
        try:
            if id == 0:
                print("Saving new family")
                family = models.InstrumentFamily(name=request.POST["name"])
            else:
                print("Updating family")
                family = get_object_or_404(models.InstrumentFamily, pk=id)
                print(family.name)
                print(request.POST)
                family.name = request.POST["name"]
                print(family.name)
            family.save()
            return HttpResponse("OK")
        except Exception as e:
            return HttpResponseBadRequest(e)
    elif request.method == "DELETE":
        family = get_object_or_404(models.InstrumentFamily, pk=id)
        print(family)
        try:
            family.delete()
            return HttpResponse("OK")
        except Exception as e:
            return HttpResponseBadRequest(e)
    families=models.InstrumentFamily.objects.all()
    return render(request, 'admin_instrument_family.html', {"families": families})
