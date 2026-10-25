from django.urls import path

from . import views

urlpatterns = [
    path('config/', views.config),
    path('start/', views.start),
    path('pay-on-delivery/', views.pay_on_delivery),
    path('refund/', views.refund),
    path('check/', views.check),
    path('sslcommerz/ipn/', views.sslcommerz_ipn),
    path('sslcommerz/<str:kind>/', views.sslcommerz_return),
    path('bkash/callback/', views.bkash_callback),
    path('nagad/callback/', views.nagad_callback),
    path('rocket/return/', views.rocket_return),
    path('upay/callback/', views.upay_callback),
]
