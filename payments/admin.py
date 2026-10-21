from django.contrib import admin

from .models import Payment, Refund


class RefundInline(admin.TabularInline):
    model = Refund
    extra = 0
    readonly_fields = ('amount', 'amount_bdt', 'status', 'gateway_refund_id', 'reason', 'error', 'automatic', 'actor', 'created_at')


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('reference', 'order', 'gateway', 'status', 'amount', 'amount_bdt', 'paid_at', 'created_at')
    list_filter = ('gateway', 'status')
    search_fields = ('reference', 'session_id', 'trx_id')
    inlines = [RefundInline]
