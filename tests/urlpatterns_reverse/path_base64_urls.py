from django.urls import path

from . import views

urlpatterns = [
    path('base64/<base64:value>/', views.empty_view, name='base64'),
]
