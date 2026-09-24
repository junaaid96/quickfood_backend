from django.db.models import Avg, Count, Exists, F, OuterRef, Prefetch, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Restaurant, MenuItem, Review, Favorite
from .permissions import IsRestaurantOwnerOrReadOnly, IsMenuItemOwnerOrReadOnly
from .planner import plan_for_restaurant, STRATEGIES
from .serializers import RestaurantSerializer, RestaurantListSerializer, MenuItemSerializer, ReviewSerializer

DIETARY_FIELDS = {
    'vegetarian': 'is_vegetarian',
    'vegan': 'is_vegan',
    'gluten_free': 'is_gluten_free',
}


class OptionalPagination(PageNumberPagination):
    """Paginate only when the client asks for ?page=, so plain list calls keep returning arrays."""
    page_size = 12
    page_size_query_param = 'page_size'
    max_page_size = 50

    def paginate_queryset(self, queryset, request, view=None):
        if 'page' not in request.query_params:
            return None
        return super().paginate_queryset(queryset, request, view)


def open_now_q():
    now = timezone.localtime().time()
    no_hours = Q(opens_at__isnull=True) | Q(closes_at__isnull=True)
    same_day = Q(opens_at__lte=F('closes_at')) & Q(opens_at__lte=now, closes_at__gte=now)
    overnight = Q(opens_at__gt=F('closes_at')) & (Q(opens_at__lte=now) | Q(closes_at__gte=now))
    return Q(is_accepting_orders=True) & (no_hours | same_day | overnight)


def annotate_restaurants(queryset, user):
    from orders.models import Order
    reviews = Review.objects.filter(restaurant=OuterRef('pk')).values('restaurant')
    orders = Order.objects.filter(restaurant=OuterRef('pk')).exclude(status='cancelled').values('restaurant')
    queryset = queryset.select_related('owner').annotate(
        rating_avg=Subquery(reviews.annotate(v=Avg('rating')).values('v')[:1]),
        review_total=Coalesce(Subquery(reviews.annotate(c=Count('id')).values('c')[:1]), Value(0)),
        order_total=Coalesce(Subquery(orders.annotate(c=Count('id')).values('c')[:1]), Value(0)),
    )
    if user.is_authenticated:
        queryset = queryset.annotate(
            is_fav=Exists(Favorite.objects.filter(user=user, restaurant=OuterRef('pk')))
        )
    return queryset


class RestaurantViewSet(viewsets.ModelViewSet):
    queryset = Restaurant.objects.all()
    serializer_class = RestaurantSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsRestaurantOwnerOrReadOnly]
    pagination_class = OptionalPagination

    def get_serializer_class(self):
        if self.action == 'list':
            return RestaurantListSerializer
        return RestaurantSerializer

    def get_queryset(self):
        user = self.request.user
        params = self.request.query_params
        queryset = annotate_restaurants(Restaurant.objects.all(), user)

        if self.action != 'list':
            return queryset.prefetch_related('menu_items')

        if params.get('owner') == 'me' and user.is_authenticated and user.role == 'restaurant_owner':
            queryset = queryset.filter(owner=user)
        if params.get('favorites') in ('1', 'true') and user.is_authenticated:
            queryset = queryset.filter(favorited_by__user=user)

        search = params.get('search', '').strip()
        if search:
            dish_match = Q(menu_items__name__icontains=search, menu_items__is_available=True)
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(description__icontains=search) | Q(cuisine__icontains=search)
                | Q(tags__icontains=search) | dish_match
            ).distinct()
            queryset = queryset.prefetch_related(Prefetch(
                'menu_items',
                queryset=MenuItem.objects.filter(name__icontains=search, is_available=True),
                to_attr='matched_items',
            ))

        cuisines = [c for c in params.get('cuisine', '').split(',') if c]
        if cuisines:
            queryset = queryset.filter(cuisine__in=cuisines)
        dietary = DIETARY_FIELDS.get(params.get('dietary', ''))
        if dietary:
            queryset = queryset.filter(pk__in=MenuItem.objects.filter(is_available=True, **{dietary: True})
                                       .values('restaurant_id'))
        if params.get('open_now') in ('1', 'true'):
            queryset = queryset.filter(open_now_q())
        if params.get('free_delivery') in ('1', 'true'):
            queryset = queryset.filter(delivery_fee=0)
        if params.get('price_level'):
            levels = [int(x) for x in params['price_level'].split(',') if x.isdigit()]
            queryset = queryset.filter(price_level__in=levels)
        if params.get('max_eta', '').isdigit():
            # eta upper bound is prep + 30
            queryset = queryset.filter(prep_time_minutes__lte=int(params['max_eta']) - 30)
        try:
            min_rating = float(params.get('min_rating', 0))
        except ValueError:
            min_rating = 0
        if min_rating:
            queryset = queryset.filter(rating_avg__gte=min_rating)

        ordering = params.get('ordering', 'recommended')
        order_map = {
            'rating': [F('rating_avg').desc(nulls_last=True), '-review_total'],
            'fastest': ['prep_time_minutes', 'delivery_fee'],
            'delivery_fee': ['delivery_fee', 'prep_time_minutes'],
            'newest': ['-created_at'],
            'popular': ['-order_total'],
            'price_low': ['price_level', 'delivery_fee'],
        }
        if ordering in order_map:
            return queryset.order_by(*order_map[ordering])
        # Recommended: accepting orders first, then rating, then popularity.
        return queryset.order_by('-is_accepting_orders', F('rating_avg').desc(nulls_last=True), '-order_total')

    def perform_create(self, serializer):
        # Check if the user has the restaurant_owner role
        if self.request.user.role != 'restaurant_owner':
            raise PermissionDenied("Only restaurant owners can create restaurants.")
        serializer.save(owner=self.request.user)

    @action(detail=True, methods=['get'])
    def menu(self, request, pk=None):
        restaurant = self.get_object()
        menu_items = MenuItem.objects.filter(restaurant=restaurant, is_available=True)
        serializer = MenuItemSerializer(menu_items, many=True, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def favorite(self, request, pk=None):
        """Toggle this restaurant in the user's favourites."""
        restaurant = self.get_object()
        fav, created = Favorite.objects.get_or_create(user=request.user, restaurant=restaurant)
        if not created:
            fav.delete()
        return Response({'is_favorite': created})

    @action(detail=False, methods=['get'], permission_classes=[permissions.AllowAny])
    def cuisines(self, request):
        counts = dict(Restaurant.objects.values_list('cuisine').annotate(n=Count('id')))
        data = [
            {'value': value, 'label': label, 'count': counts.get(value, 0)}
            for value, label in Restaurant.CUISINE_CHOICES if counts.get(value)
        ]
        return Response(data)


class MenuItemViewSet(viewsets.ModelViewSet):
    queryset = MenuItem.objects.all()
    serializer_class = MenuItemSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsMenuItemOwnerOrReadOnly]

    def get_queryset(self):
        queryset = MenuItem.objects.select_related('restaurant')
        restaurant_id = self.request.query_params.get('restaurant', None)
        if restaurant_id:
            queryset = queryset.filter(restaurant_id=restaurant_id)
        search = self.request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(description__icontains=search),
                                       is_available=True)
        return queryset

    def perform_create(self, serializer):
        # Check if the user has the restaurant_owner role
        if self.request.user.role != 'restaurant_owner':
            raise PermissionDenied("Only restaurant owners can create menu items.")

        restaurant_id = self.request.data.get('restaurant')
        if not restaurant_id:
            raise ValidationError({"restaurant": "Restaurant ID is required."})

        restaurant = Restaurant.objects.filter(id=restaurant_id).first()
        if restaurant is None:
            raise ValidationError({"restaurant": f"Restaurant with ID {restaurant_id} does not exist."})

        # Check if the user is the owner of the restaurant
        if restaurant.owner != self.request.user:
            raise PermissionDenied("You can only add menu items to restaurants you own.")

        serializer.save(restaurant=restaurant)


class ReviewViewSet(viewsets.ModelViewSet):
    serializer_class = ReviewSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]
    pagination_class = OptionalPagination
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        queryset = Review.objects.select_related('user', 'restaurant', 'order').prefetch_related('order__items__menu_item')
        params = self.request.query_params
        if params.get('restaurant'):
            queryset = queryset.filter(restaurant_id=params['restaurant'])
        if params.get('mine') in ('1', 'true') and self.request.user.is_authenticated:
            user = self.request.user
            if user.role == 'restaurant_owner':
                queryset = queryset.filter(restaurant__owner=user)
            else:
                queryset = queryset.filter(user=user)
        return queryset

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def reply(self, request, pk=None):
        review = self.get_object()
        if review.restaurant.owner_id != request.user.id:
            raise PermissionDenied("Only the restaurant owner can reply to this review.")
        reply = (request.data.get('reply') or '').strip()
        if not reply:
            raise ValidationError({'reply': 'Reply cannot be empty.'})
        review.owner_reply = reply[:1000]
        review.save(update_fields=['owner_reply'])
        return Response(self.get_serializer(review).data)


class MealPlannerView(APIView):
    """GET /api/restaurants/meal-planner/?budget=25&people=2&dietary=vegetarian&cuisine=thai&restaurant=3"""
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        params = request.query_params
        try:
            budget = float(params.get('budget', 20))
            people = int(params.get('people', 1))
        except ValueError:
            raise ValidationError({'detail': 'budget and people must be numbers.'})
        if not (1 <= budget <= 1000) or not (1 <= people <= 12):
            raise ValidationError({'detail': 'Budget must be 1-1000 and people 1-12.'})

        restaurants = Restaurant.objects.filter(open_now_q())
        if params.get('restaurant'):
            restaurants = Restaurant.objects.filter(pk=params['restaurant'])
        cuisines = [c for c in params.get('cuisine', '').split(',') if c]
        if cuisines:
            restaurants = restaurants.filter(cuisine__in=cuisines)
        restaurants = annotate_restaurants(restaurants, request.user)

        item_filter = Q(is_available=True)
        dietary = DIETARY_FIELDS.get(params.get('dietary', ''))
        if dietary:
            item_filter &= Q(**{dietary: True})
        if params.get('max_spice', '').isdigit():
            item_filter &= Q(spice_level__lte=int(params['max_spice']))
        restaurants = restaurants.prefetch_related(
            Prefetch('menu_items', queryset=MenuItem.objects.filter(item_filter), to_attr='plan_items'))

        from orders.models import OrderItem
        popularity = dict(OrderItem.objects.exclude(order__status='cancelled').values('menu_item_id')
                          .annotate(q=Sum('quantity')).values_list('menu_item_id', 'q'))

        single = bool(params.get('restaurant'))
        strategies = list(STRATEGIES) if single else ['popular']
        results = []
        for restaurant in restaurants:
            seen = set()
            for strategy in strategies:
                plan = plan_for_restaurant(restaurant, restaurant.plan_items, budget, people, popularity, strategy)
                if not plan:
                    continue
                signature = tuple(sorted((i['menu_item_id'], i['quantity']) for i in plan['items']))
                if signature in seen:
                    continue
                seen.add(signature)
                plan['restaurant'] = RestaurantListSerializer(restaurant, context={'request': request}).data
                results.append(plan)

        def score(plan):
            rating = plan['restaurant']['rating'] or 3.5
            return plan['budget_used'] / 100 + rating / 5 + min(plan['item_count'], people * 3) * 0.05

        if not single:
            results.sort(key=score, reverse=True)
        return Response({'budget': budget, 'people': people, 'plans': results[:8]})
