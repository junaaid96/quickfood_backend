from rest_framework import serializers
from .models import Order, OrderItem
from restaurants.serializers import RestaurantSerializer, MenuItemSerializer

class OrderItemSerializer(serializers.ModelSerializer):
    menu_item_details = MenuItemSerializer(source='menu_item', read_only=True)
    
    class Meta:
        model = OrderItem
        fields = ('id', 'menu_item', 'menu_item_details', 'quantity', 'price')
        read_only_fields = ('price',)

class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    restaurant_details = RestaurantSerializer(source='restaurant', read_only=True)
    
    class Meta:
        model = Order
        fields = ('id', 'user', 'restaurant', 'restaurant_details', 'status', 'total_price', 
                  'delivery_address', 'items', 'created_at', 'updated_at')
        read_only_fields = ('user', 'total_price', 'created_at', 'updated_at', 'restaurant')
    
    def create(self, validated_data):
        items_data = validated_data.pop('items')
        
        # Calculate total price
        total_price = 0
        for item_data in items_data:
            menu_item = item_data['menu_item']
            quantity = item_data['quantity']
            total_price += menu_item.price * quantity
        
        # Create order without setting user here
        order = Order.objects.create(
            total_price=total_price,
            **validated_data
        )
        
        # Create order items
        for item_data in items_data:
            menu_item = item_data['menu_item']
            quantity = item_data['quantity']
            OrderItem.objects.create(
                order=order,
                menu_item=menu_item,
                quantity=quantity,
                price=menu_item.price
            )
        
        return order