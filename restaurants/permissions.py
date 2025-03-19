from rest_framework import permissions

class IsRestaurantOwnerOrReadOnly(permissions.BasePermission):
    """
    Custom permission to only allow restaurant owners to edit their restaurants.
    """
    def has_object_permission(self, request, view, obj):
        # Read permissions are allowed to any request
        if request.method in permissions.SAFE_METHODS:
            return True
        
        # Write permissions are only allowed to the restaurant owner
        return obj.owner == request.user and request.user.role == 'restaurant_owner'

class IsMenuItemOwnerOrReadOnly(permissions.BasePermission):
    """
    Custom permission to only allow restaurant owners to edit their menu items.
    """
    def has_object_permission(self, request, view, obj):
        # Read permissions are allowed to any request
        if request.method in permissions.SAFE_METHODS:
            return True
        
        # Write permissions are only allowed to the restaurant owner
        return obj.restaurant.owner == request.user and request.user.role == 'restaurant_owner'