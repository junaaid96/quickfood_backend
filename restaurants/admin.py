from django.contrib import admin
from .models import Restaurant, MenuItem, Review, Favorite


@admin.register(Restaurant)
class RestaurantAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'cuisine', 'delivery_fee', 'is_accepting_orders', 'created_at')
    list_filter = ('cuisine', 'is_accepting_orders', 'created_at')
    search_fields = ('name', 'description', 'address', 'tags')


@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'restaurant', 'category', 'price', 'is_available')
    list_filter = ('is_available', 'is_vegetarian', 'is_vegan', 'restaurant')
    search_fields = ('name', 'description')


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('restaurant', 'user', 'rating', 'created_at')
    list_filter = ('rating',)


admin.site.register(Favorite)
