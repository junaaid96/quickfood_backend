from rest_framework import permissions

class IsOrderOwnerOrRestaurantOwner(permissions.BasePermission):
    """
    Custom permission to only allow order owners or restaurant owners to view/edit orders.
    """
    def has_object_permission(self, request, view, obj):
        # Check if user is the order owner
        if obj.user == request.user:
            return True
        
        # Check if user is the restaurant owner
        if request.user.role == 'restaurant_owner' and obj.restaurant.owner == request.user:
            return True
        
        return False