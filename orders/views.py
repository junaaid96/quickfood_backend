from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, MethodNotAllowed
from .models import Order, OrderItem
from .serializers import OrderSerializer, OrderItemSerializer
from .permissions import IsOrderOwnerOrRestaurantOwner

class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated, IsOrderOwnerOrRestaurantOwner]
    
    def get_queryset(self):
        user = self.request.user
        
        # Restaurant owners can see orders for their restaurants
        if user.role == 'restaurant_owner':
            return Order.objects.filter(restaurant__owner=user)
        
        # Regular users can only see their own orders
        return Order.objects.filter(user=user)
    
    def perform_create(self, serializer):
        # Only customers can create orders
        if self.request.user.role == 'restaurant_owner':
            raise PermissionDenied("Restaurant owners cannot place orders. Only customers can place orders.")
        
        serializer.save(user=self.request.user)
    
    def update(self, request, *args, **kwargs):
        # Prevent PUT requests (full updates)
        raise MethodNotAllowed("PUT", detail="Orders cannot be updated after placement. Use PATCH to update status only.")
    
    def partial_update(self, request, *args, **kwargs):
        # Check if request data is empty
        if not request.data:
            return Response(
                {"status": "This field is required."},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        # Only allow status updates
        if list(request.data.keys()) != ['status']:
            raise PermissionDenied("Only order status can be updated after placement.")
        
        # Validate status value
        status_value = request.data.get('status')
        valid_statuses = [choice[0] for choice in Order.STATUS_CHOICES]
        if status_value not in valid_statuses:
            return Response(
                {"status": f"Invalid status. Must be one of: {', '.join(valid_statuses)}"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get the order
        order = self.get_object()
        
        # Only restaurant owners can update status
        if request.user.role != 'restaurant_owner' or order.restaurant.owner != request.user:
            raise PermissionDenied("Only the restaurant owner can update order status.")
        
        # Update status
        order.status = status_value
        order.save()
        
        serializer = self.get_serializer(order)
        return Response(serializer.data)
    
    @action(detail=True, methods=['patch'])
    def update_status(self, request, pk=None):
        # Check if status value is provided
        status_value = request.data.get('status')
        if not status_value:
            return Response(
                {"status": "This field is required."},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        # Validate status value
        valid_statuses = [choice[0] for choice in Order.STATUS_CHOICES]
        if status_value not in valid_statuses:
            return Response(
                {"status": f"Invalid status. Must be one of: {', '.join(valid_statuses)}"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        order = self.get_object()
        
        # Only restaurant owners can update order status
        if request.user.role != 'restaurant_owner' or order.restaurant.owner != request.user:
            return Response(
                {"detail": "You do not have permission to update this order's status."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        order.status = status_value
        order.save()
        
        serializer = self.get_serializer(order)
        return Response(serializer.data)
