from django.contrib import admin
from .models import Order, OrderItem, OrderStatusEvent, OrderMessage, PromoCode


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


class OrderStatusEventInline(admin.TabularInline):
    model = OrderStatusEvent
    extra = 0
    readonly_fields = ('status', 'actor', 'note', 'created_at')


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'restaurant', 'status', 'delivery_option', 'total_price', 'created_at')
    list_filter = ('status', 'delivery_option', 'payment_method', 'created_at')
    search_fields = ('user__username', 'restaurant__name')
    inlines = [OrderItemInline, OrderStatusEventInline]


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ('order', 'menu_item', 'quantity', 'price')
    list_filter = ('order__status',)
    search_fields = ('order__id', 'menu_item__name')


@admin.register(PromoCode)
class PromoCodeAdmin(admin.ModelAdmin):
    list_display = ('code', 'discount_type', 'value', 'min_subtotal', 'restaurant', 'is_active', 'expires_at')
    list_filter = ('discount_type', 'is_active')
    search_fields = ('code', 'description')


@admin.register(OrderMessage)
class OrderMessageAdmin(admin.ModelAdmin):
    list_display = ('order', 'sender', 'body', 'created_at')
