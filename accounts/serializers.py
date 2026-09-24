from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import Address

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
