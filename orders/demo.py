"""
Keep the public demo lively: a handful of orders sitting in every live stage,
so the kitchen board and the customer's tracking page always have something to show.
"""
import random
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .models import Order, OrderItem, OrderStatusEvent
from .pricing import build_quote

User = get_user_model()

DEMO_NOTE = 'Demo order'
# (customer username or None for a guest, status, minutes since placed, prep minutes, assigned to demo rider)
PLAN = [
    ('demo_customer', 'out_for_delivery', 26, 15, True),
    ('demo_customer', 'preparing', 9, 18, False),
    (None, 'pending', 2, None, False),
    (None, 'pending', 6, None, False),
    (None, 'confirmed', 5, 20, False),
    (None, 'preparing', 12, 15, True),
    (None, 'ready', 19, 15, False),
    (None, 'ready', 17, 12, False),
    (None, 'out_for_delivery', 24, 12, False),
]
RIDER_PHOTO = 'https://images.unsplash.com/photo-1633332755192-727a05c4013d?auto=format&fit=crop&w=400&q=70'


def ensure_demo_rider():
    from accounts.models import RiderProfile
    rider, _ = User.objects.get_or_create(username='demo_rider', defaults={
        'role': 'rider', 'first_name': 'Karim', 'last_name': 'Hossain', 'email': 'demo_rider@quickfood.demo',
        'phone_number': '+8801700000020'})
    rider.role = 'rider'
    rider.set_password('quickfood123')
    rider.is_staff = rider.is_superuser = False
    rider.save()
    RiderProfile.objects.get_or_create(user=rider, defaults={
        'vehicle': 'motorbike', 'vehicle_plate': 'DHA-METRO-11-2345', 'photo_url': RIDER_PHOTO, 'is_available': True})
    # Give the demo rider a delivery history so ratings and earnings have something to show.
    if not Order.objects.filter(rider=rider, status='delivered').exists():
        rng = random.Random(7)
        past = list(Order.objects.filter(status='delivered', rider__isnull=True).order_by('-created_at')[:28])
        for order in past:
            Order.objects.filter(pk=order.pk).update(
                rider=rider, rider_assigned_at=order.created_at,
                rider_rating=rng.choices([5, 4, 3, None], weights=[60, 25, 5, 10])[0])
    return rider
STEPS = ['pending', 'confirmed', 'preparing', 'ready', 'out_for_delivery']


def demo_orders_needed(owner):
    """True when visitors have moved most demo orders along and the board looks empty."""
    active = Order.objects.filter(restaurant__owner=owner, status__in=Order.ACTIVE_STATUSES)
    return (active.count() < 4 or not active.filter(status='pending').exists()
            or not active.filter(status='ready', rider__isnull=True).exists())


@transaction.atomic
def refresh_demo_orders(force=False):
    owner = User.objects.filter(username='demo_owner').first()
    customer = User.objects.filter(username='demo_customer').first()
    if not owner or not customer:
        return 0
    rider = ensure_demo_rider()
    if not force and not demo_orders_needed(owner):
        return 0
    restaurants = list(owner.restaurants.prefetch_related('menu_items'))
    if not restaurants:
        return 0
    guests = list(User.objects.filter(username__startswith='demo_guest')) or [customer]

    # Retire the previous batch of demo orders that are still open, then lay out a fresh one.
    stale = Order.objects.filter(restaurant__owner=owner, status__in=Order.ACTIVE_STATUSES)
    stale_customer = Order.objects.filter(user=customer, status__in=Order.ACTIVE_STATUSES)
    stale_rider = Order.objects.filter(rider=rider, status__in=Order.ACTIVE_STATUSES)
    for order in set(stale) | set(stale_customer) | set(stale_rider):
        Order.objects.filter(pk=order.pk).update(status='delivered', delivered_at=timezone.now())
        OrderStatusEvent.objects.create(order=order, status='delivered', note='Demo refresh')

    rng = random.Random()
    now = timezone.now()
    created = 0
    for username, state, ago, prep, with_rider in PLAN:
        buyer = customer if username else rng.choice(guests)
        r = rng.choice(restaurants)
        items = [i for i in r.menu_items.all() if i.is_available]
        if not items:
            continue
        lines = [(item, rng.randint(1, 2)) for item in rng.sample(items, k=min(len(items), rng.randint(1, 3)))]
        option = rng.choice(['standard', 'standard', 'priority', 'eco'])
        quote = build_quote(r, lines, delivery_option=option, tip=Decimal(rng.choice([0, 1, 2])))
        placed = now - timedelta(minutes=ago)
        order = Order.objects.create(
            user=buyer, restaurant=r, status=state, subtotal=quote['subtotal'], delivery_fee=quote['delivery_fee'],
            service_fee=quote['service_fee'], tip=quote['tip'], total_price=quote['total'],
            delivery_address=buyer.address or 'Road 11, Banani, Dhaka', delivery_option=option,
            contact_phone=buyer.phone_number or '', prep_minutes=prep,
            payment_method=rng.choice(['cash', 'card']),
            rider=rider if with_rider else None, rider_assigned_at=now - timedelta(minutes=ago // 2) if with_rider else None,
        )
        OrderItem.objects.bulk_create([OrderItem(order=order, menu_item=i, quantity=q, price=i.price)
                                       for i, q in lines])
        # Spread the timeline realistically between "placed" and now.
        reached = STEPS[:STEPS.index(state) + 1]
        travel = order.travel_minutes()
        for n, step in enumerate(reached):
            at = placed + timedelta(minutes=round(ago * n / max(len(reached), 1)))
            ev = OrderStatusEvent.objects.create(order=order, status=step, actor=buyer if step == 'pending' else owner,
                                                 note='Order placed' if step == 'pending' else '')
            OrderStatusEvent.objects.filter(pk=ev.pk).update(created_at=at)
        if state == 'out_for_delivery':
            eta = now + timedelta(minutes=max(4, travel - 6))
        elif state == 'ready':
            eta = now + timedelta(minutes=travel + 3)
        elif prep:
            eta = placed + timedelta(minutes=2 + prep + travel)
            eta = max(eta, now + timedelta(minutes=travel + 4))
        else:
            eta = now + timedelta(minutes=quote['eta_minutes'])
        Order.objects.filter(pk=order.pk).update(created_at=placed, updated_at=now, estimated_delivery_at=eta)
        created += 1
    return created
