from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum


class Payment(models.Model):
    """One attempt to pay an order online through PaySim BD (bKash, Nagad, Rocket, Upay or SSLCommerz)."""
    GATEWAYS = (('bkash', 'bKash'), ('nagad', 'Nagad'), ('rocket', 'Rocket'), ('upay', 'Upay'), ('sslcommerz', 'SSLCommerz'))
    STATUSES = (
        ('initiated', 'Awaiting payment'),
        ('paid', 'Paid'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    )

    order = models.ForeignKey('orders.Order', on_delete=models.CASCADE, related_name='payments')
    gateway = models.CharField(max_length=20, choices=GATEWAYS)
    status = models.CharField(max_length=20, choices=STATUSES, default='initiated')
    # Our merchant reference (SSLCommerz tran_id, bKash invoice number, Nagad orderId). Unique per attempt.
    reference = models.CharField(max_length=40, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, help_text='Order currency')
    amount_bdt = models.DecimalField(max_digits=12, decimal_places=2)
    # Gateway session: SSLCommerz sessionkey, bKash paymentId, Nagad paymentReferenceId.
    session_id = models.CharField(max_length=128, blank=True, default='', db_index=True)
    # Settled transaction id: SSLCommerz bank_tran_id, bKash trxID, Nagad issuer trxId.
    trx_id = models.CharField(max_length=128, blank=True, default='')
    val_id = models.CharField(max_length=128, blank=True, default='')
    failure_reason = models.CharField(max_length=255, blank=True, default='')
    paid_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_gateway_display()} {self.reference} ({self.status})'

    @property
    def refunded_amount(self):
        return self.refunds.filter(status='refunded').aggregate(s=Sum('amount'))['s'] or Decimal('0')

    @property
    def refunded_bdt(self):
        return self.refunds.filter(status='refunded').aggregate(s=Sum('amount_bdt'))['s'] or Decimal('0')

    @property
    def refundable_amount(self):
        return max(Decimal('0'), self.amount - self.refunded_amount) if self.status == 'paid' else Decimal('0')


class Refund(models.Model):
    STATUSES = (('refunded', 'Refunded'), ('failed', 'Declined'))

    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name='refunds')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    amount_bdt = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUSES)
    gateway_refund_id = models.CharField(max_length=128, blank=True, default='')
    reason = models.CharField(max_length=200, blank=True, default='')
    error = models.CharField(max_length=255, blank=True, default='')
    # Client-supplied key: a double click or a retry never refunds twice.
    idempotency_key = models.CharField(max_length=64, unique=True)
    automatic = models.BooleanField(default=False, help_text='Issued automatically when a paid order was cancelled')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
