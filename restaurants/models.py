from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class Restaurant(models.Model):
    CUISINE_CHOICES = (
        ('american', 'American'),
        ('bangladeshi', 'Bangladeshi'),
        ('chinese', 'Chinese'),
        ('desserts', 'Desserts'),
        ('healthy', 'Healthy'),
        ('indian', 'Indian'),
        ('italian', 'Italian'),
        ('japanese', 'Japanese'),
        ('korean', 'Korean'),
        ('mexican', 'Mexican'),
        ('middle_eastern', 'Middle Eastern'),
        ('thai', 'Thai'),
        ('cafe', 'Cafe'),
        ('other', 'Other'),
    )
    PRICE_LEVEL_CHOICES = ((1, '$'), (2, '$$'), (3, '$$$'))

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='restaurants')
    name = models.CharField(max_length=100)
    description = models.TextField()
    address = models.TextField()
    phone_number = models.CharField(max_length=15)
    image = models.ImageField(upload_to='restaurants/', blank=True, null=True)
    image_url = models.URLField(max_length=500, blank=True, default='')

    cuisine = models.CharField(max_length=30, choices=CUISINE_CHOICES, default='other', db_index=True)
    tags = models.CharField(max_length=200, blank=True, default='', help_text='Comma separated, e.g. "burgers, late night"')
    price_level = models.PositiveSmallIntegerField(choices=PRICE_LEVEL_CHOICES, default=2)
    delivery_fee = models.DecimalField(max_digits=6, decimal_places=2, default=1.99)
    min_order = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    prep_time_minutes = models.PositiveSmallIntegerField(default=20, validators=[MinValueValidator(1), MaxValueValidator(180)])
    opens_at = models.TimeField(blank=True, null=True)
    closes_at = models.TimeField(blank=True, null=True)
    is_accepting_orders = models.BooleanField(default=True, help_text='Owners can pause new orders when the kitchen is busy.')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    @property
    def is_open(self):
        """Open when accepting orders and inside opening hours (if hours are set)."""
        if not self.is_accepting_orders:
            return False
        if not self.opens_at or not self.closes_at:
            return True
        now = timezone.localtime().time()
        if self.opens_at <= self.closes_at:
            return self.opens_at <= now <= self.closes_at
        # Hours that cross midnight, e.g. 18:00 - 02:00
        return now >= self.opens_at or now <= self.closes_at

    @property
    def tag_list(self):
        return [t.strip() for t in self.tags.split(',') if t.strip()]


class MenuItem(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='menu_items')
    name = models.CharField(max_length=100)
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    image = models.ImageField(upload_to='menu_items/', blank=True, null=True)
    image_url = models.URLField(max_length=500, blank=True, default='')
    is_available = models.BooleanField(default=True)

    category = models.CharField(max_length=50, default='Mains')
    is_vegetarian = models.BooleanField(default=False)
    is_vegan = models.BooleanField(default=False)
    is_gluten_free = models.BooleanField(default=False)
    spice_level = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(3)])
    calories = models.PositiveIntegerField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category', 'name']
        indexes = [models.Index(fields=['restaurant', 'is_available'])]

    def __str__(self):
        return f"{self.name} - {self.restaurant.name}"


class Review(models.Model):
    """A verified review: one per delivered order."""
    order = models.OneToOneField('orders.Order', on_delete=models.CASCADE, related_name='review')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reviews')
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='reviews')
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True, default='')
    owner_reply = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.rating}* {self.restaurant.name} by {self.user.username}"


class Favorite(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='favorites')
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='favorited_by')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'restaurant'], name='unique_favorite')]
