from rest_framework import serializers
from .models import Restaurant, MenuItem, Review
from django.contrib.auth import get_user_model

User = get_user_model()


def absolute_image(serializer, obj):
    """Uploaded image wins; otherwise fall back to an external image URL."""
    if obj.image:
        request = serializer.context.get('request')
        url = obj.image.url
        return request.build_absolute_uri(url) if request else url
    return obj.image_url or None


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'username')


class MenuItemSerializer(serializers.ModelSerializer):
    cover_image = serializers.SerializerMethodField()

    class Meta:
        model = MenuItem
        fields = ('id', 'restaurant', 'name', 'description', 'price', 'image', 'image_url', 'cover_image',
                  'is_available', 'category', 'is_vegetarian', 'is_vegan', 'is_gluten_free', 'spice_level',
                  'calories')
        read_only_fields = ('id', 'restaurant')
        extra_kwargs = {'image': {'required': False, 'allow_null': True}}

    def get_cover_image(self, obj):
        return absolute_image(self, obj)

    def validate_price(self, value):
        if value <= 0:
            raise serializers.ValidationError("Price must be greater than zero.")
        return value

    def validate(self, attrs):
        # Vegan implies vegetarian.
        if attrs.get('is_vegan'):
            attrs['is_vegetarian'] = True
        return attrs


class RestaurantListSerializer(serializers.ModelSerializer):
    """Lightweight card representation used for listings and search."""
    owner = UserSerializer(read_only=True)
    cover_image = serializers.SerializerMethodField()
    cuisine_label = serializers.CharField(source='get_cuisine_display', read_only=True)
    tag_list = serializers.ListField(read_only=True)
    is_open = serializers.BooleanField(read_only=True)
    rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()
    order_count = serializers.SerializerMethodField()
    is_favorite = serializers.SerializerMethodField()
    eta_range = serializers.SerializerMethodField()
    matching_dishes = serializers.SerializerMethodField()

    class Meta:
        model = Restaurant
        fields = ('id', 'name', 'description', 'address', 'phone_number', 'image', 'image_url', 'cover_image',
                  'cuisine', 'cuisine_label', 'tags', 'tag_list', 'price_level', 'delivery_fee', 'min_order',
                  'prep_time_minutes', 'eta_range', 'opens_at', 'closes_at', 'is_accepting_orders', 'is_open',
                  'rating', 'review_count', 'order_count', 'is_favorite', 'matching_dishes', 'created_at', 'owner')
        read_only_fields = ('id', 'created_at')
        extra_kwargs = {'image': {'required': False, 'allow_null': True}}

    def get_cover_image(self, obj):
        return absolute_image(self, obj)

    def get_rating(self, obj):
        value = getattr(obj, 'rating_avg', None)
        return round(float(value), 1) if value is not None else None

    def get_review_count(self, obj):
        return getattr(obj, 'review_total', 0) or 0

    def get_order_count(self, obj):
        return getattr(obj, 'order_total', 0) or 0

    def get_is_favorite(self, obj):
        return bool(getattr(obj, 'is_fav', False))

    def get_eta_range(self, obj):
        # Kitchen time plus a typical 15-30 minute ride.
        return [obj.prep_time_minutes + 15, obj.prep_time_minutes + 30]

    def get_matching_dishes(self, obj):
        matched = getattr(obj, 'matched_items', None)
        return [item.name for item in matched[:3]] if matched else []

    def validate_name(self, value):
        qs = Restaurant.objects.filter(name__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("A restaurant with this name already exists.")
        return value

    def create(self, validated_data):
        validated_data['owner'] = self.context['request'].user
        return super().create(validated_data)


class RestaurantSerializer(RestaurantListSerializer):
    """Full restaurant including its menu, used for the detail page."""
    menu_items = serializers.SerializerMethodField()
    categories = serializers.SerializerMethodField()
    popular_item_ids = serializers.SerializerMethodField()
    rating_breakdown = serializers.SerializerMethodField()

    class Meta(RestaurantListSerializer.Meta):
        fields = RestaurantListSerializer.Meta.fields + ('menu_items', 'categories', 'popular_item_ids',
                                                         'rating_breakdown')

    def _is_owner(self, obj):
        request = self.context.get('request')
        return bool(request and request.user.is_authenticated and obj.owner_id == request.user.id)

    def _menu(self, obj):
        items = list(obj.menu_items.all())
        # Customers only see what they can order; the owner sees everything.
        if not self._is_owner(obj):
            items = [i for i in items if i.is_available]
        return items

    def get_menu_items(self, obj):
        return MenuItemSerializer(self._menu(obj), many=True, context=self.context).data

    def get_categories(self, obj):
        seen = []
        for item in self._menu(obj):
            if item.category not in seen:
                seen.append(item.category)
        return seen

    def get_popular_item_ids(self, obj):
        from django.db.models import Sum
        from orders.models import OrderItem
        rows = (OrderItem.objects.filter(order__restaurant=obj).exclude(order__status='cancelled')
                .values('menu_item_id').annotate(qty=Sum('quantity')).order_by('-qty')[:3])
        return [r['menu_item_id'] for r in rows]

    def get_rating_breakdown(self, obj):
        from django.db.models import Count
        counts = {str(i): 0 for i in range(1, 6)}
        for row in obj.reviews.values('rating').annotate(n=Count('id')):
            counts[str(row['rating'])] = row['n']
        return counts


class ReviewSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    restaurant_name = serializers.CharField(source='restaurant.name', read_only=True)
    items = serializers.SerializerMethodField()

    class Meta:
        model = Review
        fields = ('id', 'order', 'user', 'restaurant', 'restaurant_name', 'rating', 'comment', 'owner_reply',
                  'items', 'created_at')
        read_only_fields = ('id', 'user', 'restaurant', 'owner_reply', 'created_at')

    def get_user(self, obj):
        # Show a friendly, privacy-preserving name: "Sara K."
        u = obj.user
        if u.first_name:
            initial = f" {u.last_name[:1]}." if u.last_name else ''
            return f"{u.first_name}{initial}"
        return u.username

    def get_items(self, obj):
        return [i.menu_item.name for i in obj.order.items.all()][:4]

    def validate_order(self, order):
        request = self.context['request']
        if order.user_id != request.user.id:
            raise serializers.ValidationError("You can only review your own orders.")
        if order.status != 'delivered':
            raise serializers.ValidationError("You can review an order once it has been delivered.")
        if Review.objects.filter(order=order).exists():
            raise serializers.ValidationError("This order has already been reviewed.")
        return order

    def create(self, validated_data):
        order = validated_data['order']
        validated_data['user'] = self.context['request'].user
        validated_data['restaurant'] = order.restaurant
        return super().create(validated_data)
