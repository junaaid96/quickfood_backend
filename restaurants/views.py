from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from .models import Restaurant, MenuItem
from .serializers import RestaurantSerializer, MenuItemSerializer
from .permissions import IsRestaurantOwnerOrReadOnly, IsMenuItemOwnerOrReadOnly

class RestaurantViewSet(viewsets.ModelViewSet):
    queryset = Restaurant.objects.all()
    serializer_class = RestaurantSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsRestaurantOwnerOrReadOnly]
    
    def get_queryset(self):
        queryset = Restaurant.objects.all()
        if self.request.user.is_authenticated and self.request.user.role == 'restaurant_owner':
            owner_id = self.request.query_params.get('owner', None)
            if owner_id and owner_id == 'me':
                queryset = queryset.filter(owner=self.request.user)
        return queryset
    
    def perform_create(self, serializer):
        # Check if the user has the restaurant_owner role
        if self.request.user.role != 'restaurant_owner':
            raise PermissionDenied("Only restaurant owners can create restaurants.")
        
        # Check if a restaurant with the same name already exists
        restaurant_name = serializer.validated_data.get('name')
        if Restaurant.objects.filter(name__iexact=restaurant_name).exists():
            raise ValidationError({"name": "A restaurant with this name already exists."})
        
        serializer.save(owner=self.request.user)
    
    @action(detail=True, methods=['get'])
    def menu(self, request, pk=None):
        restaurant = self.get_object()
        menu_items = MenuItem.objects.filter(restaurant=restaurant, is_available=True)
        serializer = MenuItemSerializer(menu_items, many=True)
        return Response(serializer.data)

class MenuItemViewSet(viewsets.ModelViewSet):
    queryset = MenuItem.objects.all()
    serializer_class = MenuItemSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsMenuItemOwnerOrReadOnly]
    
    def get_queryset(self):
        restaurant_id = self.request.query_params.get('restaurant', None)
        if restaurant_id:
            return MenuItem.objects.filter(restaurant_id=restaurant_id)
        return MenuItem.objects.all()
    
    def perform_create(self, serializer):
        # Check if the user has the restaurant_owner role
        if self.request.user.role != 'restaurant_owner':
            raise PermissionDenied("Only restaurant owners can create menu items.")
        
        restaurant_id = self.request.data.get('restaurant')
        if not restaurant_id:
            raise ValidationError({"restaurant": "Restaurant ID is required."})
            
        # Check if the restaurant exists
        if not Restaurant.objects.filter(id=restaurant_id).exists():
            raise ValidationError({"restaurant": f"Restaurant with ID {restaurant_id} does not exist."})
            
        restaurant = Restaurant.objects.get(id=restaurant_id)
        
        # Check if the user is the owner of the restaurant
        if restaurant.owner != self.request.user:
            raise PermissionDenied("You can only add menu items to restaurants you own.")
        
        serializer.save(restaurant=restaurant)
