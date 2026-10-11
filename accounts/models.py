from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings


class User(AbstractUser):
    ROLE_CHOICES = (
        ('user', 'User'),
        ('restaurant_owner', 'Restaurant Owner'),
        ('rider', 'Delivery Rider'),
    )

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='user')
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    loyalty_points = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.username

    @property
    def is_rider(self):
        return self.role == 'rider'

    @property
    def is_restaurant_owner(self):
        return self.role == 'restaurant_owner'

    # Loyalty tiers, unlocked by lifetime points balance.
    TIERS = (
        (0, 'Bronze'),
        (500, 'Silver'),
        (1500, 'Gold'),
        (4000, 'Platinum'),
    )

    @property
    def loyalty_tier(self):
        tier = self.TIERS[0][1]
        for threshold, name in self.TIERS:
            if self.loyalty_points >= threshold:
                tier = name
        return tier

    @property
    def next_tier(self):
        for threshold, name in self.TIERS:
            if self.loyalty_points < threshold:
                return {'name': name, 'points_needed': threshold - self.loyalty_points, 'threshold': threshold}
        return None


class Address(models.Model):
    """A saved delivery address, e.g. Home or Work."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='addresses')
    label = models.CharField(max_length=40, default='Home')
    line = models.TextField()
    instructions = models.CharField(max_length=200, blank=True, default='')
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-is_default', '-created_at']
        verbose_name_plural = 'addresses'

    def __str__(self):
        return f"{self.label}: {self.line[:40]}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            Address.objects.filter(user=self.user).exclude(pk=self.pk).update(is_default=False)


class RiderProfile(models.Model):
    """Delivery rider details. Stats (deliveries, earnings, rating) are computed from orders."""
    VEHICLES = (
        ('motorbike', 'Motorbike'),
        ('bicycle', 'Bicycle'),
        ('scooter', 'Scooter'),
        ('car', 'Car'),
        ('on_foot', 'On foot'),
    )
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='rider_profile')
    vehicle = models.CharField(max_length=20, choices=VEHICLES, default='motorbike')
    vehicle_plate = models.CharField(max_length=20, blank=True, default='')
    photo = models.ImageField(upload_to='riders/', blank=True, null=True)
    photo_url = models.URLField(blank=True, default='')
    is_available = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Rider {self.user.username}"
