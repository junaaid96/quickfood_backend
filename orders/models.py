from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from restaurants.models import Restaurant, MenuItem


class PromoCode(models.Model):
    DISCOUNT_TYPES = (
        ('percent', 'Percent off subtotal'),
        ('flat', 'Flat amount off'),
        ('free_delivery', 'Free delivery'),
    )

    code = models.CharField(max_length=30, unique=True)
    description = models.CharField(max_length=200, blank=True, default='')
    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPES, default='percent')
    value = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    max_discount = models.DecimalField(max_digits=8, decimal_places=2, blank=True, null=True)
    min_subtotal = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    first_order_only = models.BooleanField(default=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, blank=True, null=True, related_name='promo_codes',
                                   help_text='Leave empty for a platform-wide code.')
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        self.code = self.code.upper().strip()
        super().save(*args, **kwargs)

    @property
    def is_valid_now(self):
        return self.is_active and (self.expires_at is None or self.expires_at > timezone.now())


class Order(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('preparing', 'Preparing'),
        ('out_for_delivery', 'Out for Delivery'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
    )
    # Forward-only lifecycle. Owners may skip steps; nobody can move backwards.
    STATUS_FLOW = ['pending', 'confirmed', 'preparing', 'out_for_delivery', 'delivered']
    ACTIVE_STATUSES = ['pending', 'confirmed', 'preparing', 'out_for_delivery']

    DELIVERY_OPTIONS = (
        ('priority', 'Priority'),
        ('standard', 'Standard'),
        ('eco', 'Wait & Save'),
    )
    PAYMENT_METHODS = (
        ('cash', 'Cash on delivery'),
        ('card', 'Card'),
    )

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='orders')
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='orders')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    delivery_fee = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    service_fee = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    points_discount = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    tip = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)

    delivery_address = models.TextField()
    delivery_option = models.CharField(max_length=20, choices=DELIVERY_OPTIONS, default='standard')
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default='cash')
    contact_phone = models.CharField(max_length=20, blank=True, default='')
    notes = models.CharField(max_length=300, blank=True, default='')
    promo_code = models.ForeignKey(PromoCode, on_delete=models.SET_NULL, blank=True, null=True, related_name='orders')
    points_redeemed = models.PositiveIntegerField(default=0)
    points_earned = models.PositiveIntegerField(default=0)

    scheduled_for = models.DateTimeField(blank=True, null=True)
    estimated_delivery_at = models.DateTimeField(blank=True, null=True)
    delivered_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['restaurant', 'status']),
            models.Index(fields=['user', '-created_at']),
        ]

    def __str__(self):
        return f"Order #{self.id} - {self.user.username}"

    def can_transition_to(self, new_status):
        if self.status in ('delivered', 'cancelled'):
            return False
        if new_status == 'cancelled':
            return self.status != 'out_for_delivery'
        if new_status not in self.STATUS_FLOW:
            return False
        return self.STATUS_FLOW.index(new_status) > self.STATUS_FLOW.index(self.status)

    def set_status(self, new_status, actor=None, note=''):
        """Change status, record a timeline event and settle loyalty points on delivery."""
        self.status = new_status
        if new_status == 'delivered':
            self.delivered_at = timezone.now()
            # 1 point per whole currency unit spent on food.
            self.points_earned = int(self.subtotal)
            user = self.user
            user.loyalty_points += self.points_earned
            user.save(update_fields=['loyalty_points'])
        elif new_status == 'cancelled' and self.points_redeemed:
            user = self.user
            user.loyalty_points += self.points_redeemed
            user.save(update_fields=['loyalty_points'])
        self.save()
        OrderStatusEvent.objects.create(order=self, status=new_status, actor=actor, note=note)

    @property
    def savings(self):
        return (self.discount or Decimal('0')) + (self.points_discount or Decimal('0'))


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    note = models.CharField(max_length=140, blank=True, default='')

    def __str__(self):
        return f"{self.quantity} x {self.menu_item.name}"

    def save(self, *args, **kwargs):
        # Set the price from the menu item if not provided
        if not self.price:
            self.price = self.menu_item.price
        super().save(*args, **kwargs)


class OrderStatusEvent(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='events')
    status = models.CharField(max_length=20, choices=Order.STATUS_CHOICES)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True)
    note = models.CharField(max_length=200, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']


class OrderMessage(models.Model):
    """Live chat between the customer and the restaurant about one order."""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    body = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
