from rest_framework import generics, permissions, status, viewsets
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView
from django.contrib.auth import get_user_model
from .serializers import UserSerializer, CustomTokenObtainPairSerializer, AddressSerializer

User = get_user_model()

# Public demo logins shown on the frontend login page (password is public).
DEMO_USERNAMES = {'demo_customer', 'demo_owner', 'demo_owner2'}


class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.AllowAny]


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        # Demo visitors move orders along; top the board back up so every stage stays visible.
        if response.status_code == 200 and request.data.get('username') in DEMO_USERNAMES:
            try:
                from orders.demo import refresh_demo_orders
                refresh_demo_orders()
            except Exception:  # never block a login on demo housekeeping
                pass
        return response


class UserProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return User.objects.filter(pk=self.request.user.pk)

    def get_object(self):
        return self.request.user

    def update(self, request, *args, **kwargs):
        # Public demo accounts are shared: keep their login details fixed.
        if request.user.username in DEMO_USERNAMES and any(
                k in request.data for k in ('username', 'email', 'password', 'role')):
            return Response({'detail': "Demo accounts can't change username, email, password or role."},
                            status=status.HTTP_403_FORBIDDEN)
        return super().update(request, *args, **kwargs)


class AddressViewSet(viewsets.ModelViewSet):
    serializer_class = AddressSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return self.request.user.addresses.all()

    def perform_create(self, serializer):
        # The first saved address becomes the default automatically.
        is_first = not self.request.user.addresses.exists()
        serializer.save(user=self.request.user, is_default=serializer.validated_data.get('is_default') or is_first)
