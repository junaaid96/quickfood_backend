from rest_framework import serializers
from .models import Order, OrderItem
from restaurants.serializers import RestaurantSerializer, MenuItemSerializer
from django.contrib.auth import get_user_model
from restaurants.models import MenuItem, Restaurant

User = get_user_model()

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'phone_number')

class OrderItemSerializer(serializers.ModelSerializer):
    menu_item_details = MenuItemSerializer(source='menu_item', read_only=True)
    
    class Meta:
        model = OrderItem
        fields = ('id', 'menu_item', 'menu_item_details', 'quantity', 'price')
        read_only_fields = ('price',)

class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    restaurant_details = RestaurantSerializer(source='restaurant', read_only=True)
    user_details = UserSerializer(source='user', read_only=True)
    order_items = serializers.ListField(
        child=serializers.DictField(),
        write_only=True,
        required=True
    )
    
    class Meta:
        model = Order
        fields = ('id', 'user', 'user_details', 'restaurant', 'restaurant_details', 'status', 'total_price', 
                  'delivery_address', 'items', 'order_items', 'created_at', 'updated_at')
        read_only_fields = ('user', 'total_price', 'created_at', 'updated_at')
    
    def create(self, validated_data):
        # Extract the order items data
        order_items_data = validated_data.pop('order_items')
        
        # Ensure we have at least one item
        if not order_items_data:
            raise serializers.ValidationError({"order_items": "At least one item is required"})
        
        # Get restaurant ID from the first menu item
        first_item = order_items_data[0]
        menu_item_id = first_item.get('menu_item')
        
        try:
            menu_item = MenuItem.objects.get(id=menu_item_id)
            restaurant_id = menu_item.restaurant_id
        except MenuItem.DoesNotExist:
            raise serializers.ValidationError(f"Menu item with ID {menu_item_id} does not exist")
        
        # Verify all items belong to the same restaurant
        for item_data in order_items_data[1:]:
            item_id = item_data.get('menu_item')
            try:
                item = MenuItem.objects.get(id=item_id)
                if item.restaurant_id != restaurant_id:
                    raise serializers.ValidationError("All menu items must belong to the same restaurant")
            except MenuItem.DoesNotExist:
                raise serializers.ValidationError(f"Menu item with ID {item_id} does not exist")
        
        # Calculate total price
        total_price = 0
        for item_data in order_items_data:
            menu_item_id = item_data.get('menu_item')
            quantity = item_data.get('quantity', 1)
            
            menu_item = MenuItem.objects.get(id=menu_item_id)
            total_price += menu_item.price * quantity
        
        # Create order with the restaurant ID
        order = Order.objects.create(
            user=self.context['request'].user,
            restaurant_id=restaurant_id,
            total_price=total_price,
            delivery_address=validated_data.get('delivery_address'),
            status=validated_data.get('status', 'pending')
        )
        
        # Create order items
        for item_data in order_items_data:
            menu_item_id = item_data.get('menu_item')
            quantity = item_data.get('quantity', 1)
            
            menu_item = MenuItem.objects.get(id=menu_item_id)
            OrderItem.objects.create(
                order=order,
                menu_item=menu_item,
                quantity=quantity,
                price=menu_item.price
            )
        
        return order