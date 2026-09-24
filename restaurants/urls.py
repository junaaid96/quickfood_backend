from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import RestaurantViewSet, MenuItemViewSet, ReviewViewSet, MealPlannerView

router = DefaultRouter()
router.register(r'restaurant', RestaurantViewSet)
router.register(r'menu-items', MenuItemViewSet)
router.register(r'reviews', ReviewViewSet, basename='review')

urlpatterns = [
    path('meal-planner/', MealPlannerView.as_view(), name='meal_planner'),
    path('', include(router.urls)),
]
