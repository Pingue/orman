from django.urls import path

from . import views

app_name = "orchestra"
urlpatterns = [
    path("", views.index, name="base"),
]
