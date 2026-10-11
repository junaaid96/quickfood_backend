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

        # Riders see orders assigned to them, and unassigned ones they could pick up.
        if request.user.role == 'rider':
            if obj.rider_id == request.user.id:
                return True
            return obj.rider_id is None and obj.status in obj.RIDER_CLAIMABLE
        
        return False