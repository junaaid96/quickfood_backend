from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from restaurants.models import MenuItem, Restaurant
from restaurants.serializers import MenuItemSerializer, absolute_image
from .models import Order, OrderItem, OrderStatusEvent, OrderMessage, PromoCode
from .pricing import build_quote, delivery_options_for

User = get_user_model()
MAX_QUANTITY = 50


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'phone_number', 'first_name', 'last_name')


class RestaurantSummarySerializer(serializers.ModelSerializer):
    cover_image = serializers.SerializerMethodField()
    owner = serializers.SerializerMethodField()

    class Meta:
        model = Restaurant
        fields = ('id', 'name', 'description', 'address', 'phone_number', 'image', 'cover_image', 'cuisine',
                  'prep_time_minutes', 'delivery_fee', 'owner', 'created_at')

    def get_cover_image(self, obj):
        return absolute_image(self, obj)

    def get_owner(self, obj):
        return {'id': obj.owner_id, 'username': obj.owner.username}


class OrderItemSerializer(serializers.ModelSerializer):
    menu_item_details = MenuItemSerializer(source='menu_item', read_only=True)

    class Meta:
        model = OrderItem
        fields = ('id', 'menu_item', 'menu_item_details', 'quantity', 'price', 'note')
        read_only_fields = ('price',)


class OrderStatusEventSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = OrderStatusEvent
        fields = ('status', 'status_label', 'note', 'created_at')


class OrderMessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.SerializerMethodField()
    sender_role = serializers.SerializerMethodField()
    is_mine = serializers.SerializerMethodField()

    class Meta:
        model = OrderMessage
        fields = ('id', 'body', 'sender_name', 'sender_role', 'is_mine', 'created_at')
        read_only_fields = ('id', 'created_at')

    def get_sender_name(self, obj):
        if obj.sender_id == obj.order.restaurant.owner_id:
            return obj.order.restaurant.name
        return obj.sender.first_name or obj.sender.username

    def get_sender_role(self, obj):
        return 'restaurant' if obj.sender_id == obj.order.restaurant.owner_id else 'customer'

    def get_is_mine(self, obj):
        request = self.context.get('request')
        return bool(request and obj.sender_id == request.user.id)


def parse_order_lines(order_items_data, restaurant_id=None):
    """Validate raw [{menu_item, quantity, note}] input. Returns (restaurant, [(MenuItem, qty, note)])."""
    if not order_items_data:
        raise serializers.ValidationError({"order_items": "At least one item is required."})

    merged = {}
    for raw in order_items_data:
        try:
            item_id = int(raw.get('menu_item'))
            quantity = int(raw.get('quantity', 1))
        except (TypeError, ValueError):
            raise serializers.ValidationError({"order_items": "Each item needs a numeric menu_item and quantity."})
        if quantity < 1 or quantity > MAX_QUANTITY:
            raise serializers.ValidationError({"order_items": f"Quantity must be between 1 and {MAX_QUANTITY}."})
        note = str(raw.get('note', '') or '')[:140]
        prev_qty, prev_note = merged.get(item_id, (0, ''))
        merged[item_id] = (prev_qty + quantity, prev_note or note)

    items = MenuItem.objects.select_related('restaurant').in_bulk(list(merged))
    missing = [str(i) for i in merged if i not in items]
    if missing:
        raise serializers.ValidationError(f"Menu item with ID {', '.join(missing)} does not exist")

    restaurants = {item.restaurant_id for item in items.values()}
    if len(restaurants) > 1:
        raise serializers.ValidationError("All menu items must belong to the same restaurant")
    restaurant = next(iter(items.values())).restaurant
    if restaurant_id and int(restaurant_id) != restaurant.id:
        raise serializers.ValidationError({"restaurant": "Items do not belong to this restaurant."})

    unavailable = [item.name for item in items.values() if not item.is_available]
    if unavailable:
        raise serializers.ValidationError({"order_items": f"Currently unavailable: {', '.join(unavailable)}."})

    lines = [(items[item_id], qty, note) for item_id, (qty, note) in merged.items()]
    return restaurant, lines


def validate_schedule(value):
    if value is None:
        return None
    now = timezone.now()
    if value < now + timedelta(minutes=30):
        raise serializers.ValidationError({"scheduled_for": "Schedule at least 30 minutes ahead."})
    if value > now + timedelta(days=7):
        raise serializers.ValidationError({"scheduled_for": "You can schedule up to 7 days ahead."})
    return value


class CheckoutInputMixin(serializers.Serializer):
    order_items = serializers.ListField(child=serializers.DictField(), write_only=True, required=True)
    delivery_option = serializers.ChoiceField(choices=Order.DELIVERY_OPTIONS, default='standard')
    promo_code = serializers.CharField(required=False, allow_blank=True, write_only=True)
    use_points = serializers.BooleanField(required=False, default=False, write_only=True)
    tip = serializers.DecimalField(max_digits=8, decimal_places=2, required=False, default=Decimal('0'),
                                   min_value=Decimal('0'), max_value=Decimal('100'))
    scheduled_for = serializers.DateTimeField(required=False, allow_null=True)


class QuoteSerializer(CheckoutInputMixin):
    restaurant = serializers.IntegerField(required=False)

    def to_quote(self):
        data = self.validated_data
        restaurant, lines = parse_order_lines(data['order_items'], data.get('restaurant'))
        scheduled_for = validate_schedule(data.get('scheduled_for'))
        quote = build_quote(
            restaurant, [(item, qty) for item, qty, _ in lines], user=self.context['request'].user,
            delivery_option=data.get('delivery_option', 'standard'), promo_code=data.get('promo_code', ''),
            use_points=data.get('use_points', False), tip=data.get('tip', 0), scheduled_for=scheduled_for,
        )
        promo = quote.pop('promo')
        result = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in quote.items()}
        result['promo_code'] = promo.code if promo else None
        result['promo_description'] = promo.description if promo else None
        result['restaurant'] = restaurant.id
        result['restaurant_open'] = restaurant.is_open
        result['delivery_options'] = delivery_options_for(restaurant)
        return result


class OrderSerializer(CheckoutInputMixin, serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    restaurant_details = RestaurantSummarySerializer(source='restaurant', read_only=True)
    user_details = UserSerializer(source='user', read_only=True)
    events = OrderStatusEventSerializer(many=True, read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    delivery_option_label = serializers.CharField(source='get_delivery_option_display', read_only=True)
    promo_code = serializers.CharField(required=False, allow_blank=True)
    review = serializers.SerializerMethodField()
    can_cancel = serializers.SerializerMethodField()
    next_statuses = serializers.SerializerMethodField()
    rider_details = serializers.SerializerMethodField()
    eta_minutes = serializers.SerializerMethodField()
    payment = serializers.SerializerMethodField()
    restaurant = serializers.PrimaryKeyRelatedField(queryset=Restaurant.objects.all(), required=False)

    class Meta:
        model = Order
        fields = ('id', 'user', 'user_details', 'restaurant', 'restaurant_details', 'status', 'status_label',
                  'subtotal', 'delivery_fee', 'service_fee', 'discount', 'points_discount', 'tip', 'total_price',
                  'delivery_address', 'delivery_option', 'delivery_option_label', 'payment_method', 'contact_phone',
                  'notes', 'promo_code', 'use_points', 'points_redeemed', 'points_earned', 'scheduled_for',
                  'estimated_delivery_at', 'delivered_at', 'prep_minutes', 'cancelled_by', 'eta_minutes',
                  'next_statuses', 'rider', 'rider_details', 'rider_rating', 'rider_assigned_at', 'items', 'order_items', 'events', 'review', 'can_cancel', 'payment',
                  'created_at', 'updated_at')
        read_only_fields = ('user', 'status', 'subtotal', 'delivery_fee', 'service_fee', 'discount',
                            'points_discount', 'total_price', 'points_redeemed', 'points_earned',
                            'estimated_delivery_at', 'delivered_at', 'prep_minutes', 'cancelled_by', 'rider', 'rider_rating',
                            'rider_assigned_at', 'created_at', 'updated_at')

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['promo_code'] = instance.promo_code.code if instance.promo_code_id else None
        return data

    def get_payment(self, obj):
        from payments.service import payment_summary
        return payment_summary(obj)

    def validate_payment_method(self, value):
        from payments.service import ONLINE_METHODS, enabled_gateways
        if value in ONLINE_METHODS and value not in enabled_gateways():
            raise serializers.ValidationError('This payment method is not available right now.')
        return value

    def get_review(self, obj):
        review = getattr(obj, 'review', None)
        if review is None:
            return None
        return {'id': review.id, 'rating': review.rating, 'comment': review.comment,
                'owner_reply': review.owner_reply}

    def get_can_cancel(self, obj):
        request = self.context.get('request')
        return bool(request and obj.user_id == request.user.id and obj.status == 'pending')

    def get_rider_details(self, obj):
        if not obj.rider_id:
            return None
        rider = obj.rider
        profile = getattr(rider, 'rider_profile', None)
        photo = None
        if profile is not None:
            photo = profile.photo.url if profile.photo else (profile.photo_url or None)
            request = self.context.get('request')
            if photo and profile.photo and request:
                photo = request.build_absolute_uri(photo)
        from django.db.models import Avg
        rating = Order.objects.filter(rider=rider, rider_rating__isnull=False).aggregate(r=Avg('rider_rating'))['r']
        return {
            'id': rider.id, 'name': rider.get_full_name() or rider.username, 'phone_number': rider.phone_number or '',
            'vehicle': profile.get_vehicle_display() if profile else '', 'vehicle_plate': profile.vehicle_plate if profile else '',
            'photo': photo, 'rating': round(float(rating), 1) if rating else None,
        }

    def get_next_statuses(self, obj):
        """What this user can do next, so boards never offer an illegal move."""
        request = self.context.get('request')
        role = getattr(request.user, 'role', None) if request else None
        if role == 'rider':
            if obj.rider_id != request.user.id:
                return ['claim'] if obj.rider_id is None and obj.status in Order.RIDER_CLAIMABLE else []
            return {'ready': ['out_for_delivery'], 'out_for_delivery': ['delivered']}.get(obj.status, [])
        if not request or role != 'restaurant_owner' or obj.user_id == request.user.id:
            return []
        if obj.status in ('delivered', 'cancelled'):
            return []
        flow = Order.STATUS_FLOW
        nxt = flow[flow.index(obj.status) + 1:flow.index(obj.status) + 2]
        if obj.rider_id and nxt and nxt[0] in ('out_for_delivery', 'delivered'):
            nxt = []
        return nxt + (['cancelled'] if obj.can_transition_to('cancelled') else [])

    def get_eta_minutes(self, obj):
        if obj.status in ('delivered', 'cancelled') or not obj.estimated_delivery_at:
            return None
        return max(0, round((obj.estimated_delivery_at - timezone.now()).total_seconds() / 60))

    def validate_delivery_address(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("Delivery address is required.")
        return value.strip()

    @transaction.atomic
    def create(self, validated_data):
        request = self.context['request']
        user = request.user
        restaurant_obj = validated_data.get('restaurant')
        restaurant, lines = parse_order_lines(validated_data.pop('order_items'),
                                              restaurant_obj.id if restaurant_obj else None)
        scheduled_for = validate_schedule(validated_data.get('scheduled_for'))

        if not restaurant.is_accepting_orders:
            raise serializers.ValidationError({"restaurant": f"{restaurant.name} is not taking orders right now."})
        if not restaurant.is_open and not scheduled_for:
            raise serializers.ValidationError({"restaurant": f"{restaurant.name} is closed. Schedule your order for later."})

        # Lock the user row so concurrent orders cannot spend the same points twice.
        user = User.objects.select_for_update().get(pk=user.pk)
        quote = build_quote(
            restaurant, [(item, qty) for item, qty, _ in lines], user=user,
            delivery_option=validated_data.get('delivery_option', 'standard'),
            promo_code=validated_data.get('promo_code', ''), use_points=validated_data.get('use_points', False),
            tip=validated_data.get('tip', 0), scheduled_for=scheduled_for,
        )
        if quote['below_min_order']:
            raise serializers.ValidationError({"order_items": f"Minimum order at {restaurant.name} is {restaurant.min_order}."})
        if quote['promo_error']:
            raise serializers.ValidationError({"promo_code": quote['promo_error']})

        order = Order.objects.create(
            user=user,
            restaurant=restaurant,
            subtotal=quote['subtotal'],
            delivery_fee=quote['delivery_fee'],
            service_fee=quote['service_fee'],
            discount=quote['discount'],
            points_discount=quote['points_discount'],
            points_redeemed=quote['points_redeemed'],
            tip=quote['tip'],
            total_price=quote['total'],
            promo_code=quote['promo'],
            delivery_address=validated_data['delivery_address'],
            delivery_option=quote['delivery_option'],
            payment_method=validated_data.get('payment_method', 'cash'),
            contact_phone=validated_data.get('contact_phone', '') or (user.phone_number or ''),
            notes=validated_data.get('notes', ''),
            scheduled_for=scheduled_for,
            estimated_delivery_at=quote['estimated_delivery_at'],
            status='pending',
        )
        OrderItem.objects.bulk_create([
            OrderItem(order=order, menu_item=item, quantity=qty, price=item.price, note=note)
            for item, qty, note in lines
        ])
        if quote['points_redeemed']:
            user.loyalty_points -= quote['points_redeemed']
            user.save(update_fields=['loyalty_points'])
        OrderStatusEvent.objects.create(order=order, status='pending', actor=user,
                                        note='Scheduled order placed' if scheduled_for else 'Order placed')
        from payments.service import OFFLINE_METHODS
        if order.payment_method in OFFLINE_METHODS:
            # Online orders reach the kitchen once the payment settles (payments.service.mark_paid).
            from .realtime import push_status
            push_status(order)
        return order


class PromoCodeSerializer(serializers.ModelSerializer):
    restaurant_name = serializers.CharField(source='restaurant.name', read_only=True, default=None)

    class Meta:
        model = PromoCode
        fields = ('code', 'description', 'discount_type', 'value', 'max_discount', 'min_subtotal',
                  'first_order_only', 'restaurant', 'restaurant_name', 'expires_at')
