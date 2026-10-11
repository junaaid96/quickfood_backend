from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import Address, RiderProfile

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, style={'input_type': 'password'})
    first_name = serializers.CharField(required=True)
    last_name = serializers.CharField(required=True)
    email = serializers.EmailField(required=True)
    role = serializers.ChoiceField(choices=User.ROLE_CHOICES, required=True)
    loyalty_tier = serializers.CharField(read_only=True)
    next_tier = serializers.JSONField(read_only=True)

    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'password', 'first_name', 'last_name', 'role', 'phone_number',
                  'address', 'loyalty_points', 'loyalty_tier', 'next_tier', 'date_joined')
        read_only_fields = ('id', 'loyalty_points', 'date_joined')

    def validate_email(self, value):
        """
        Check that the email is not already in use.
        """
        user = self.context.get('request').user if self.context.get('request') else None
        if User.objects.filter(email__iexact=value).exclude(pk=getattr(user, 'pk', None)).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def validate_password(self, value):
        validate_password(value)
        return value

    def create(self, validated_data):
        user = User.objects.create_user(**validated_data)
        if user.role == 'rider':
            RiderProfile.objects.create(user=user)
        return user

    def update(self, instance, validated_data):
        # The role is chosen at sign up and cannot be self-promoted afterwards.
        validated_data.pop('role', None)
        password = validated_data.pop('password', None)
        instance = super().update(instance, validated_data)
        if password:
            instance.set_password(password)
            instance.save(update_fields=['password'])
        return instance


class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = ('id', 'label', 'line', 'instructions', 'is_default', 'created_at')
        read_only_fields = ('id', 'created_at')


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        # custom claims
        token['username'] = user.username
        token['email'] = user.email
        token['role'] = user.role

        return token


class RiderProfileSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    phone_number = serializers.CharField(source='user.phone_number', required=False, allow_blank=True, allow_null=True)
    first_name = serializers.CharField(source='user.first_name', required=False)
    last_name = serializers.CharField(source='user.last_name', required=False)
    photo = serializers.ImageField(write_only=True, required=False, allow_null=True)
    photo_display = serializers.SerializerMethodField()
    vehicle_label = serializers.CharField(source='get_vehicle_display', read_only=True)
    stats = serializers.SerializerMethodField()

    class Meta:
        model = RiderProfile
        fields = ('id', 'name', 'first_name', 'last_name', 'phone_number', 'vehicle', 'vehicle_label', 'vehicle_plate',
                  'photo', 'photo_display', 'is_available', 'stats')
        read_only_fields = ('id',)

    def get_name(self, obj):
        return obj.user.get_full_name() or obj.user.username

    def get_photo_display(self, obj):
        if obj.photo:
            request = self.context.get('request')
            return request.build_absolute_uri(obj.photo.url) if request else obj.photo.url
        return obj.photo_url or None

    def get_stats(self, obj):
        return rider_stats(obj.user)

    def update(self, instance, validated_data):
        user_data = validated_data.pop('user', {})
        for k, v in user_data.items():
            setattr(instance.user, k, v)
        if user_data:
            instance.user.save()
        return super().update(instance, validated_data)


def rider_stats(user):
    from django.db.models import Avg, Count, Sum
    from django.utils import timezone
    from orders.models import Order
    done = Order.objects.filter(rider=user, status='delivered')
    agg = done.aggregate(n=Count('id'), fees=Sum('delivery_fee'), tips=Sum('tip'), rating=Avg('rider_rating'),
                         rated=Count('rider_rating'))
    today = done.filter(delivered_at__date=timezone.localdate()).aggregate(
        n=Count('id'), fees=Sum('delivery_fee'), tips=Sum('tip'))
    money = lambda f, t: float((f or 0) + (t or 0))  # noqa: E731
    return {
        'deliveries': agg['n'] or 0,
        'earnings': round(money(agg['fees'], agg['tips']), 2),
        'tips': float(agg['tips'] or 0),
        'rating': round(float(agg['rating']), 2) if agg['rating'] else None,
        'rating_count': agg['rated'] or 0,
        'today_deliveries': today['n'] or 0,
        'today_earnings': round(money(today['fees'], today['tips']), 2),
        'active': Order.objects.filter(rider=user, status__in=['confirmed', 'preparing', 'ready', 'out_for_delivery']).count(),
    }
