from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from orders.models import Order
from .models import Restaurant, MenuItem, Review

User = get_user_model()


def make_restaurant(owner, name='Testaurant', **kwargs):
    return Restaurant.objects.create(owner=owner, name=name, description='d', address='a', phone_number='1',
                                     **kwargs)


class RestaurantTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user('owner', 'o@example.com', 'x', role='restaurant_owner')
        self.customer = User.objects.create_user('cust', 'c@example.com', 'x', role='user')
        self.pizza = make_restaurant(self.owner, 'Pizza Place', cuisine='italian', delivery_fee=Decimal('0'))
        self.sushi = make_restaurant(self.owner, 'Sushi Bar', cuisine='japanese', is_accepting_orders=False)
        self.marg = MenuItem.objects.create(restaurant=self.pizza, name='Margherita', description='', price=10,
                                            is_vegetarian=True, category='Pizza')
        MenuItem.objects.create(restaurant=self.sushi, name='Tuna Roll', description='', price=8, category='Sushi')
        MenuItem.objects.create(restaurant=self.pizza, name='Hidden', description='', price=8, is_available=False)

    def test_list_is_public_array_and_paginates_on_request(self):
        res = self.client.get('/api/restaurants/restaurant/')
        self.assertEqual(res.status_code, 200)
        self.assertIsInstance(res.data, list)
        res = self.client.get('/api/restaurants/restaurant/?page=1')
        self.assertEqual(res.data['count'], 2)

    def test_search_matches_dishes(self):
        res = self.client.get('/api/restaurants/restaurant/?search=tuna')
        self.assertEqual([r['name'] for r in res.data], ['Sushi Bar'])
        self.assertEqual(res.data[0]['matching_dishes'], ['Tuna Roll'])

    def test_filters(self):
        names = lambda q: [r['name'] for r in self.client.get('/api/restaurants/restaurant/' + q).data]
        self.assertEqual(names('?cuisine=italian'), ['Pizza Place'])
        self.assertEqual(names('?dietary=vegetarian'), ['Pizza Place'])
        self.assertEqual(names('?open_now=1'), ['Pizza Place'])
        self.assertEqual(names('?free_delivery=1'), ['Pizza Place'])

    def test_detail_hides_unavailable_items_from_customers(self):
        res = self.client.get(f'/api/restaurants/restaurant/{self.pizza.id}/')
        self.assertEqual([i['name'] for i in res.data['menu_items']], ['Margherita'])
        self.client.force_authenticate(self.owner)
        res = self.client.get(f'/api/restaurants/restaurant/{self.pizza.id}/')
        self.assertEqual(len(res.data['menu_items']), 2)

    def test_only_owners_create_and_names_unique(self):
        self.client.force_authenticate(self.customer)
        payload = {'name': 'New', 'description': 'd', 'address': 'a', 'phone_number': '1'}
        self.assertEqual(self.client.post('/api/restaurants/restaurant/', payload).status_code, 403)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post('/api/restaurants/restaurant/', payload).status_code, 201)
        self.assertEqual(self.client.post('/api/restaurants/restaurant/', payload).status_code, 400)

    def test_favorite_toggle(self):
        self.client.force_authenticate(self.customer)
        url = f'/api/restaurants/restaurant/{self.pizza.id}/favorite/'
        self.assertTrue(self.client.post(url).data['is_favorite'])
        res = self.client.get('/api/restaurants/restaurant/?favorites=1')
        self.assertEqual([r['name'] for r in res.data], ['Pizza Place'])
        self.assertTrue(res.data[0]['is_favorite'])
        self.assertFalse(self.client.post(url).data['is_favorite'])

    def test_review_requires_delivered_own_order(self):
        order = Order.objects.create(user=self.customer, restaurant=self.pizza, total_price=10, delivery_address='x')
        self.client.force_authenticate(self.customer)
        res = self.client.post('/api/restaurants/reviews/', {'order': order.id, 'rating': 5})
        self.assertEqual(res.status_code, 400)
        order.status = 'delivered'
        order.save()
        res = self.client.post('/api/restaurants/reviews/', {'order': order.id, 'rating': 5, 'comment': 'Yum'})
        self.assertEqual(res.status_code, 201)
        self.assertEqual(self.client.post('/api/restaurants/reviews/', {'order': order.id, 'rating': 4}).status_code, 400)
        detail = self.client.get(f'/api/restaurants/restaurant/{self.pizza.id}/').data
        self.assertEqual(detail['rating'], 5.0)
        self.assertEqual(detail['rating_breakdown']['5'], 1)

        review = Review.objects.get()
        self.assertEqual(self.client.post(f'/api/restaurants/reviews/{review.id}/reply/', {'reply': 'Hi'}).status_code, 403)
        self.client.force_authenticate(self.owner)
        res = self.client.post(f'/api/restaurants/reviews/{review.id}/reply/', {'reply': 'Thanks!'})
        self.assertEqual(res.data['owner_reply'], 'Thanks!')

    def test_meal_planner_fits_budget(self):
        MenuItem.objects.create(restaurant=self.pizza, name='Cola', description='', price=2, category='Drinks')
        res = self.client.get('/api/restaurants/meal-planner/?budget=30&people=2')
        self.assertEqual(res.status_code, 200)
        plans = res.data['plans']
        self.assertEqual(len(plans), 1)  # sushi is paused
        plan = plans[0]
        self.assertLessEqual(Decimal(plan['estimated_total']), Decimal('30'))
        mains = [i for i in plan['items'] if i['category'] == 'Pizza']
        self.assertEqual(sum(i['quantity'] for i in mains), 2)

    def test_meal_planner_rejects_tiny_budget(self):
        res = self.client.get('/api/restaurants/meal-planner/?budget=3&people=2')
        self.assertEqual(res.data['plans'], [])
