"""
Populate the database with demo restaurants, menus, customers, orders and reviews.

    python manage.py seed_demo           # create demo data (safe to re-run)
    python manage.py seed_demo --reset   # remove previous demo data first

Demo logins (password: quickfood123): demo_customer, demo_owner
"""
import random
from datetime import time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from orders.models import Order, OrderItem, OrderStatusEvent, PromoCode
from orders.pricing import build_quote
from restaurants.models import Restaurant, MenuItem, Review, Favorite

User = get_user_model()
PASSWORD = 'quickfood123'
U = 'https://images.unsplash.com/'


def img(photo_id):
    return f'{U}{photo_id}?auto=format&fit=crop&w=1200&q=70'


# name, cuisine, tags, price_level, fee, min_order, prep, hours, image, description, menu
# menu rows: (category, name, price, description, flags)  flags: v=vegetarian, V=vegan, g=gluten free, 1-3 spice
RESTAURANTS = [
    ('Ember & Crust', 'italian', 'pizza, wood fired, date night', 2, '1.99', '10', 18, None,
     'photo-1513104890138-7c749659a591',
     'Neapolitan pizza blistered in a 450 degree wood oven, with slow-proved dough and San Marzano tomatoes.',
     [('Pizza', 'Margherita', '11.50', 'San Marzano tomato, fior di latte, basil, olive oil.', 'v'),
      ('Pizza', 'Hot Honey Pepperoni', '14.00', 'Cup-and-char pepperoni, chilli honey, oregano.', '2'),
      ('Pizza', 'Wild Mushroom & Truffle', '15.50', 'Roasted mushrooms, taleggio, truffle oil, thyme.', 'v'),
      ('Pizza', 'Nduja & Burrata', '16.00', 'Spicy nduja, whole burrata, rocket.', '3'),
      ('Sides', 'Garlic Knots', '5.50', 'Brushed with garlic butter and parmesan.', 'v'),
      ('Sides', 'Caesar Salad', '7.50', 'Little gem, anchovy dressing, sourdough crumbs.', ''),
      ('Desserts', 'Tiramisu', '6.50', 'Espresso-soaked savoiardi, mascarpone, cocoa.', 'v'),
      ('Drinks', 'Blood Orange Soda', '3.00', 'Sparkling Italian soda.', 'Vg')]),
    ('Smash Club', 'american', 'burgers, late night, fries', 1, '0.99', '8', 12, (time(11), time(2)),
     'photo-1568901346375-23c9450c58cd',
     'Double-smashed patties with crispy lace edges, potato buns and fries that stay crunchy all the way home.',
     [('Burgers', 'Classic Double Smash', '9.90', 'Two patties, American cheese, pickles, house sauce.', ''),
      ('Burgers', 'Jalapeno Popper Smash', '11.50', 'Cream cheese, charred jalapeno, bacon jam.', '2'),
      ('Burgers', 'Crispy Chicken Sando', '10.50', 'Buttermilk thigh, slaw, spicy mayo.', '1'),
      ('Burgers', 'Beyond Smash', '10.90', 'Plant patty, vegan cheese, all the fixings.', 'V'),
      ('Sides', 'Shoestring Fries', '3.50', 'Sea salt, extra crispy.', 'Vg'),
      ('Sides', 'Loaded Cheese Fries', '5.90', 'Cheese sauce, scallions, crispy onions.', 'v'),
      ('Drinks', 'Vanilla Bean Shake', '5.50', 'Thick and old school.', 'vg'),
      ('Drinks', 'Cola', '2.20', 'Ice cold.', 'Vg')]),
    ('Dhaka Kitchen', 'bangladeshi', 'biryani, home style, family meals', 1, '1.49', '12', 25, None,
     'photo-1563379091339-03b21ab4a4f8',
     'Old Dhaka flavours: dum-cooked kacchi biryani, slow bhuna curries and fresh borhani.',
     [('Biryani', 'Mutton Kacchi Biryani', '13.90', 'Basmati and tender mutton, sealed and dum-cooked.', '1g'),
      ('Biryani', 'Chicken Tehari', '10.50', 'Fragrant kalijira rice with mustard oil and chicken.', '1g'),
      ('Curries', 'Beef Kala Bhuna', '12.90', 'Chittagong-style, dark and deeply spiced.', '2g'),
      ('Curries', 'Shorshe Ilish', '15.50', 'Hilsa in mustard gravy.', '2g'),
      ('Curries', 'Dal Makhani', '7.50', 'Black lentils, butter, cream.', 'vg'),
      ('Sides', 'Beguni', '3.90', 'Crisp aubergine fritters.', 'Vg'),
      ('Desserts', 'Firni', '4.50', 'Chilled rice pudding with cardamom.', 'vg'),
      ('Drinks', 'Borhani', '2.90', 'Spiced yoghurt drink.', 'vg')]),
    ('Tokyo Tide', 'japanese', 'sushi, ramen, healthy', 3, '2.49', '15', 22, None,
     'photo-1579871494447-9811cf80d66c',
     'Hand-rolled sushi and 18-hour tonkotsu ramen from a counter-style kitchen.',
     [('Sushi', 'Salmon Nigiri (4)', '9.50', 'Scottish salmon, aged rice.', 'g'),
      ('Sushi', 'Spicy Tuna Roll', '11.00', 'Tuna, chilli mayo, cucumber, tempura crunch.', '2'),
      ('Sushi', 'Avocado Cucumber Roll', '7.50', 'Simple and fresh.', 'Vg'),
      ('Ramen', 'Tonkotsu Ramen', '15.00', 'Pork bone broth, chashu, ajitama egg.', ''),
      ('Ramen', 'Miso Veggie Ramen', '13.50', 'White miso, tofu, corn, bok choy.', 'V1'),
      ('Sides', 'Edamame', '4.50', 'Sea salt or chilli garlic.', 'Vg'),
      ('Sides', 'Chicken Karaage', '7.50', 'Crispy thigh, yuzu mayo.', ''),
      ('Drinks', 'Iced Matcha', '4.50', 'Ceremonial grade.', 'vg')]),
    ('Green Theory', 'healthy', 'bowls, vegan, protein', 2, '0.00', '10', 10, None,
     'photo-1512621776951-a57141f2eefd',
     'Macro-balanced bowls and cold-pressed juices. Every bowl lists its calories.',
     [('Bowls', 'Harvest Grain Bowl', '12.50', 'Farro, roast squash, kale, tahini.', 'V'),
      ('Bowls', 'Chicken Power Bowl', '13.50', 'Grilled chicken, quinoa, greens, lemon herb.', 'g'),
      ('Bowls', 'Tofu Poke Bowl', '12.00', 'Sesame tofu, rice, edamame, pickled ginger.', 'V'),
      ('Bowls', 'Salmon Avocado Bowl', '15.00', 'Seared salmon, avocado, brown rice.', 'g'),
      ('Sides', 'Lentil Soup', '5.50', 'Red lentil, cumin, lemon.', 'Vg'),
      ('Drinks', 'Green Machine Juice', '5.90', 'Kale, apple, cucumber, ginger.', 'Vg'),
      ('Desserts', 'Chia Pudding', '4.90', 'Coconut, mango, lime.', 'Vg')]),
    ('Taqueria Sol', 'mexican', 'tacos, spicy, street food', 1, '1.49', '0', 15, None,
     'photo-1565299585323-38d6b0865b47',
     'Corn tortillas pressed to order, al pastor off the trompo and salsas made every morning.',
     [('Tacos', 'Al Pastor Tacos (3)', '9.50', 'Marinated pork, pineapple, onion, cilantro.', '2g'),
      ('Tacos', 'Baja Fish Tacos (3)', '10.50', 'Beer-battered fish, cabbage, chipotle crema.', '1'),
      ('Tacos', 'Mushroom Tinga Tacos (3)', '8.90', 'Smoky chipotle mushrooms, queso fresco.', 'v2g'),
      ('Burritos', 'Carne Asada Burrito', '12.50', 'Grilled steak, rice, beans, guac.', '1'),
      ('Sides', 'Chips & Guacamole', '5.50', 'Made fresh every hour.', 'Vg'),
      ('Sides', 'Elote', '4.50', 'Charred corn, cotija, chilli lime.', 'vg1'),
      ('Drinks', 'Horchata', '3.50', 'Cinnamon rice milk.', 'Vg')]),
    ('Bangkok Street', 'thai', 'noodles, curry, spicy', 2, '1.99', '12', 20, None,
     'photo-1559314809-0d155014e29e',
     'Wok-fired noodles and curries pounded from fresh paste, Bangkok street-stall style.',
     [('Noodles', 'Pad Thai', '12.00', 'Rice noodles, tamarind, peanuts, lime.', 'g1'),
      ('Noodles', 'Drunken Noodles', '12.50', 'Wide noodles, holy basil, bird eye chilli.', '3'),
      ('Curries', 'Green Curry', '13.00', 'Coconut, Thai aubergine, basil.', 'g2'),
      ('Curries', 'Massaman Tofu Curry', '12.00', 'Mild, nutty and rich.', 'Vg1'),
      ('Sides', 'Fresh Spring Rolls', '6.00', 'Rice paper, herbs, peanut sauce.', 'Vg'),
      ('Desserts', 'Mango Sticky Rice', '6.50', 'Sweet coconut rice, ripe mango.', 'Vg'),
      ('Drinks', 'Thai Iced Tea', '3.90', 'Strong and creamy.', 'vg')]),
    ('Sugar Loft', 'desserts', 'cakes, sweet, coffee', 2, '2.49', '10', 8, (time(9), time(23)),
     'photo-1551024601-bec78aea704b',
     'Small-batch doughnuts, layer cakes and proper espresso.',
     [('Desserts', 'Brown Butter Doughnut', '3.90', 'Glazed, flaky salt.', 'v'),
      ('Desserts', 'Basque Cheesecake', '6.50', 'Burnt top, creamy centre.', 'vg'),
      ('Desserts', 'Chocolate Fudge Slice', '5.90', 'Three layers of dark chocolate.', 'v'),
      ('Desserts', 'Vegan Lemon Tart', '5.50', 'Almond crust, bright lemon curd.', 'V'),
      ('Drinks', 'Flat White', '3.50', 'Double ristretto.', 'vg'),
      ('Drinks', 'Iced Caramel Latte', '4.50', 'House caramel.', 'vg')]),
]


def flags_to_fields(flags):
    return {
        'is_vegetarian': 'v' in flags or 'V' in flags,
        'is_vegan': 'V' in flags,
        'is_gluten_free': 'g' in flags,
        'spice_level': max([int(c) for c in flags if c.isdigit()] or [0]),
    }


class Command(BaseCommand):
    help = 'Seed demo restaurants, menus, customers, orders and reviews.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true', help='Delete existing demo data first.')

    @transaction.atomic
    def handle(self, *args, **options):
        rng = random.Random(42)
        demo_usernames = ['demo_owner', 'demo_owner2', 'demo_customer'] + [f'demo_guest{i}' for i in range(1, 7)]
        if options['reset']:
            User.objects.filter(username__in=demo_usernames).delete()
            PromoCode.objects.filter(code__in=['WELCOME20', 'FREEDEL', 'SAVE5', 'BIRYANI10']).delete()

        def user(username, role, first, last, **extra):
            u, created = User.objects.get_or_create(
                username=username,
                defaults={'role': role, 'first_name': first, 'last_name': last,
                          'email': f'{username}@quickfood.demo', **extra})
            if created:
                u.set_password(PASSWORD)
                u.save()
            return u

        owner = user('demo_owner', 'restaurant_owner', 'Rafi', 'Ahmed', phone_number='+8801700000001')
        owner2 = user('demo_owner2', 'restaurant_owner', 'Maya', 'Chen', phone_number='+8801700000002')
        customer = user('demo_customer', 'user', 'Sara', 'Khan', phone_number='+8801700000010',
                        address='House 12, Road 7, Dhanmondi, Dhaka')
        guests = [user(f'demo_guest{i}', 'user', name, 'Demo')
                  for i, name in enumerate(['Nadia', 'Omar', 'Lena', 'Arif', 'Tom', 'Priya'], start=1)]

        if Restaurant.objects.filter(owner__in=[owner, owner2]).exists():
            self.stdout.write(self.style.WARNING('Demo data already present. Use --reset to rebuild.'))
            return

        restaurants = []
        for idx, (name, cuisine, tags, level, fee, min_order, prep, hours, photo, desc, menu) in enumerate(RESTAURANTS):
            r = Restaurant.objects.create(
                owner=owner if idx % 2 == 0 else owner2, name=name, cuisine=cuisine, tags=tags, price_level=level,
                delivery_fee=Decimal(fee), min_order=Decimal(min_order), prep_time_minutes=prep,
                opens_at=hours[0] if hours else None, closes_at=hours[1] if hours else None,
                image_url=img(photo), description=desc, address=f'{10 + idx * 7} Gulshan Avenue, Dhaka',
                phone_number=f'+88017000001{idx:02d}',
            )
            for category, item_name, price, item_desc, flags in menu:
                MenuItem.objects.create(restaurant=r, category=category, name=item_name, price=Decimal(price),
                                        description=item_desc, **flags_to_fields(flags))
            restaurants.append(r)

        PromoCode.objects.get_or_create(code='WELCOME20', defaults=dict(
            description='20% off your first order, up to $10', discount_type='percent', value=20,
            max_discount=10, first_order_only=True))
        PromoCode.objects.get_or_create(code='FREEDEL', defaults=dict(
            description='Free delivery on orders over $15', discount_type='free_delivery', min_subtotal=15))
        PromoCode.objects.get_or_create(code='SAVE5', defaults=dict(
            description='$5 off orders over $30', discount_type='flat', value=5, min_subtotal=30))
        PromoCode.objects.get_or_create(code='BIRYANI10', defaults=dict(
            description='10% off at Dhaka Kitchen', discount_type='percent', value=10,
            restaurant=restaurants[2]))

        # A month of order history so ratings, popularity and analytics have something to show.
        comments = ['Arrived hot and fast.', 'Portions were generous!', 'Exactly like the photos.',
                    'Good but a little late.', 'New favourite spot.', 'Packaging was spotless.', '']
        now = timezone.now()
        people = guests + [customer]
        for n in range(90):
            r = rng.choice(restaurants)
            items = list(r.menu_items.all())
            lines = [(item, rng.randint(1, 2)) for item in rng.sample(items, k=rng.randint(1, 3))]
            buyer = rng.choice(people)
            option = rng.choice(['standard', 'standard', 'priority', 'eco'])
            quote = build_quote(r, lines, delivery_option=option, tip=Decimal(rng.choice([0, 1, 2, 3])))
            created = now - timedelta(days=rng.randint(1, 29), hours=rng.randint(0, 10), minutes=rng.randint(0, 59))
            state = 'cancelled' if rng.random() < 0.06 else 'delivered'
            order = Order.objects.create(
                user=buyer, restaurant=r, status=state, subtotal=quote['subtotal'],
                delivery_fee=quote['delivery_fee'], service_fee=quote['service_fee'], tip=quote['tip'],
                total_price=quote['total'], delivery_address='Demo address, Dhaka', delivery_option=option,
                points_earned=int(quote['subtotal']) if state == 'delivered' else 0,
                estimated_delivery_at=created + timedelta(minutes=quote['eta_minutes']),
                delivered_at=created + timedelta(minutes=quote['eta_minutes'] + rng.randint(-5, 8))
                if state == 'delivered' else None,
            )
            OrderItem.objects.bulk_create([OrderItem(order=order, menu_item=i, quantity=q, price=i.price)
                                           for i, q in lines])
            Order.objects.filter(pk=order.pk).update(created_at=created, updated_at=created)
            OrderStatusEvent.objects.create(order=order, status='pending')
            OrderStatusEvent.objects.create(order=order, status=state)
            if state == 'delivered':
                buyer.loyalty_points += order.points_earned
                buyer.save(update_fields=['loyalty_points'])
                if rng.random() < 0.7:
                    rating = rng.choices([5, 4, 3, 2], weights=[55, 30, 10, 5])[0]
                    review = Review.objects.create(order=order, user=buyer, restaurant=r, rating=rating,
                                                   comment=rng.choice(comments))
                    Review.objects.filter(pk=review.pk).update(created_at=created + timedelta(hours=2))

        # One order in progress for the demo customer, so live tracking has something to show.
        r = restaurants[2]
        lines = [(r.menu_items.get(name='Mutton Kacchi Biryani'), 1), (r.menu_items.get(name='Borhani'), 2)]
        quote = build_quote(r, lines, delivery_option='standard', tip=Decimal('2'))
        live = Order.objects.create(
            user=customer, restaurant=r, status='preparing', subtotal=quote['subtotal'],
            delivery_fee=quote['delivery_fee'], service_fee=quote['service_fee'], tip=quote['tip'],
            total_price=quote['total'], delivery_address=customer.address, delivery_option='standard',
            estimated_delivery_at=now + timedelta(minutes=28),
        )
        OrderItem.objects.bulk_create([OrderItem(order=live, menu_item=i, quantity=q, price=i.price) for i, q in lines])
        for status, ago in (('pending', 14), ('confirmed', 12), ('preparing', 9)):
            e = OrderStatusEvent.objects.create(order=live, status=status, actor=r.owner)
            OrderStatusEvent.objects.filter(pk=e.pk).update(created_at=now - timedelta(minutes=ago))
        Order.objects.filter(pk=live.pk).update(created_at=now - timedelta(minutes=14))

        Favorite.objects.get_or_create(user=customer, restaurant=restaurants[2])
        Favorite.objects.get_or_create(user=customer, restaurant=restaurants[3])

        self.stdout.write(self.style.SUCCESS(
            f'Seeded {len(restaurants)} restaurants, {Order.objects.count()} orders, '
            f'{Review.objects.count()} reviews. Log in as demo_customer or demo_owner (password: {PASSWORD}).'))
