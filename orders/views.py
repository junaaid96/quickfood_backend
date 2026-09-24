from datetime import timedelta

from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import ExtractHour, TruncDate
from django.utils import timezone
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, MethodNotAllowed, ValidationError
from rest_framework.response import Response

from restaurants.models import Review
from restaurants.serializers import MenuItemSerializer
from restaurants.views import OptionalPagination
from .models import Order, OrderItem, OrderMessage, PromoCode
from .permissions import IsOrderOwnerOrRestaurantOwner
from .serializers import (OrderSerializer, OrderMessageSerializer, PromoCodeSerializer, QuoteSerializer,
                          RestaurantSummarySerializer)

CHAT_WINDOW = timedelta(hours=2)


class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated, IsOrderOwnerOrRestaurantOwner]
    pagination_class = OptionalPagination

    def get_queryset(self):
        user = self.request.user
        params = self.request.query_params

        # Restaurant owners can see orders for their restaurants
        if user.role == 'restaurant_owner':
            queryset = Order.objects.filter(restaurant__owner=user)
            if params.get('restaurant'):
                queryset = queryset.filter(restaurant_id=params['restaurant'])
        else:
            # Regular users can only see their own orders
            queryset = Order.objects.filter(user=user)

        state = params.get('status')
        if state == 'active':
            queryset = queryset.filter(status__in=Order.ACTIVE_STATUSES)
        elif state == 'past':
            queryset = queryset.exclude(status__in=Order.ACTIVE_STATUSES)
        elif state:
            queryset = queryset.filter(status__in=state.split(','))

        return queryset.select_related('restaurant__owner', 'user', 'promo_code', 'review').prefetch_related(
            'items__menu_item', 'events')

    def perform_create(self, serializer):
        # Only customers can create orders
        if self.request.user.role == 'restaurant_owner':
            raise PermissionDenied("Restaurant owners cannot place orders. Only customers can place orders.")

        serializer.save(user=self.request.user)

    def update(self, request, *args, **kwargs):
        # Prevent PUT requests (full updates)
        raise MethodNotAllowed("PUT", detail="Orders cannot be updated after placement. Use PATCH to update status only.")

    def destroy(self, request, *args, **kwargs):
        raise MethodNotAllowed("DELETE", detail="Orders cannot be deleted. Cancel them instead.")

    def _change_status(self, request, order, status_value, note=''):
        valid_statuses = [choice[0] for choice in Order.STATUS_CHOICES]
        if status_value not in valid_statuses:
            return Response(
                {"status": f"Invalid status. Must be one of: {', '.join(valid_statuses)}"},
                status=status.HTTP_400_BAD_REQUEST
            )
        # Only restaurant owners can update status
        if request.user.role != 'restaurant_owner' or order.restaurant.owner != request.user:
            raise PermissionDenied("Only the restaurant owner can update order status.")
        if not order.can_transition_to(status_value):
            return Response(
                {"status": f"Cannot move an order from '{order.get_status_display()}' to '{status_value}'."},
                status=status.HTTP_400_BAD_REQUEST
            )
        order.set_status(status_value, actor=request.user, note=note[:200])
        order = self.get_queryset().get(pk=order.pk)
        return Response(self.get_serializer(order).data)

    def partial_update(self, request, *args, **kwargs):
        # Check if request data is empty
        if not request.data:
            return Response(
                {"status": "This field is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Only allow status updates
        if set(request.data.keys()) - {'status', 'note'}:
            raise PermissionDenied("Only order status can be updated after placement.")

        order = self.get_object()
        return self._change_status(request, order, request.data.get('status'), request.data.get('note', ''))

    @action(detail=True, methods=['patch'])
    def update_status(self, request, pk=None):
        # Check if status value is provided
        status_value = request.data.get('status')
        if not status_value:
            return Response(
                {"status": "This field is required."},
                status=status.HTTP_400_BAD_REQUEST
            )
        order = self.get_object()
        return self._change_status(request, order, status_value, request.data.get('note', ''))

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Customers can cancel while the restaurant has not confirmed yet."""
        order = self.get_object()
        if order.user_id != request.user.id:
            raise PermissionDenied("Only the customer can cancel from here.")
        if order.status != 'pending':
            raise ValidationError({"status": "The restaurant already accepted this order. Message them instead."})
        reason = (request.data.get('reason') or 'Cancelled by customer')[:200]
        order.set_status('cancelled', actor=request.user, note=reason)
        return Response(self.get_serializer(self.get_queryset().get(pk=order.pk)).data)

    @action(detail=True, methods=['post'])
    def reorder(self, request, pk=None):
        """Return the order's items as a ready-to-use cart (only what is still available)."""
        order = self.get_object()
        items, unavailable = [], []
        for line in order.items.select_related('menu_item'):
            if line.menu_item.is_available:
                items.append({
                    'menu_item': MenuItemSerializer(line.menu_item, context={'request': request}).data,
                    'quantity': line.quantity,
                    'note': line.note,
                })
            else:
                unavailable.append(line.menu_item.name)
        return Response({
            'restaurant': RestaurantSummarySerializer(order.restaurant, context={'request': request}).data,
            'items': items,
            'unavailable': unavailable,
        })

    @action(detail=True, methods=['get', 'post'])
    def messages(self, request, pk=None):
        order = self.get_object()
        if request.method == 'POST':
            finished_at = order.delivered_at or (order.updated_at if order.status == 'cancelled' else None)
            if finished_at and timezone.now() - finished_at > CHAT_WINDOW:
                raise ValidationError({"body": "Chat for this order is closed."})
            body = (request.data.get('body') or '').strip()
            if not body:
                raise ValidationError({"body": "Message cannot be empty."})
            OrderMessage.objects.create(order=order, sender=request.user, body=body[:500])
        queryset = order.messages.select_related('sender', 'order__restaurant')
        since = request.query_params.get('since')
        if since and since.isdigit():
            queryset = queryset.filter(id__gt=int(since))
        serializer = OrderMessageSerializer(queryset, many=True, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED if request.method == 'POST' else 200)

    @action(detail=False, methods=['post'])
    def quote(self, request):
        """Price a cart without placing it. Same maths as order creation."""
        serializer = QuoteSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        return Response(serializer.to_quote())

    @action(detail=False, methods=['get'], permission_classes=[permissions.AllowAny])
    def promos(self, request):
        now = timezone.now()
        queryset = PromoCode.objects.filter(is_active=True).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        restaurant = request.query_params.get('restaurant')
        if restaurant:
            queryset = queryset.filter(Q(restaurant__isnull=True) | Q(restaurant_id=restaurant))
        else:
            queryset = queryset.filter(restaurant__isnull=True)
        return Response(PromoCodeSerializer(queryset.select_related('restaurant'), many=True).data)

    @action(detail=False, methods=['get'])
    def analytics(self, request):
        """Owner dashboard numbers for the last N days."""
        user = request.user
        if user.role != 'restaurant_owner':
            raise PermissionDenied("Analytics are available to restaurant owners.")
        try:
            days = max(1, min(int(request.query_params.get('days', 30)), 365))
        except ValueError:
            days = 30
        since = timezone.now() - timedelta(days=days)

        orders = Order.objects.filter(restaurant__owner=user)
        if request.query_params.get('restaurant'):
            orders = orders.filter(restaurant_id=request.query_params['restaurant'])
        active_orders = orders.filter(status__in=Order.ACTIVE_STATUSES).count()
        period = orders.filter(created_at__gte=since)
        billable = period.exclude(status='cancelled')

        totals = billable.aggregate(revenue=Sum('subtotal'), count=Count('id'), tips=Sum('tip'))
        revenue = totals['revenue'] or 0
        count = totals['count'] or 0
        all_count = period.count()
        cancelled = period.filter(status='cancelled').count()

        customers = billable.values('user').annotate(n=Count('id'))
        repeat = sum(1 for c in customers if c['n'] > 1)

        daily = {row['day']: row for row in billable.annotate(day=TruncDate('created_at')).values('day')
                 .annotate(revenue=Sum('subtotal'), orders=Count('id'))}
        series = []
        for i in range(min(days, 30) - 1, -1, -1):
            day = (timezone.localdate() - timedelta(days=i))
            row = daily.get(day)
            series.append({'date': day.isoformat(), 'revenue': float(row['revenue']) if row else 0,
                           'orders': row['orders'] if row else 0})

        top_items = list(OrderItem.objects.filter(order__in=billable).values('menu_item__name')
                         .annotate(quantity=Sum('quantity'), revenue=Sum('price'))
                         .order_by('-quantity')[:5])
        for row in top_items:
            row['name'] = row.pop('menu_item__name')
            row['revenue'] = float(row['revenue'] or 0)

        hours = {row['hour']: row['n'] for row in billable.annotate(hour=ExtractHour('created_at'))
                 .values('hour').annotate(n=Count('id'))}

        reviews = Review.objects.filter(restaurant__owner=user)
        if request.query_params.get('restaurant'):
            reviews = reviews.filter(restaurant_id=request.query_params['restaurant'])
        rating = reviews.aggregate(avg=Avg('rating'), n=Count('id'))

        return Response({
            'days': days,
            'kpis': {
                'revenue': float(revenue),
                'orders': count,
                'avg_order_value': round(float(revenue) / count, 2) if count else 0,
                'tips': float(totals['tips'] or 0),
                'cancel_rate': round(cancelled / all_count * 100, 1) if all_count else 0,
                'repeat_customer_rate': round(repeat / len(customers) * 100, 1) if customers else 0,
                'customers': len(customers),
                'avg_rating': round(float(rating['avg']), 2) if rating['avg'] else None,
                'review_count': rating['n'],
                'active_orders': active_orders,
            },
            'series': series,
            'status_breakdown': dict(period.values_list('status').annotate(n=Count('id'))),
            'delivery_mix': dict(billable.values_list('delivery_option').annotate(n=Count('id'))),
            'top_items': top_items,
            'busiest_hours': [{'hour': h, 'orders': hours.get(h, 0)} for h in range(24)],
        })
