from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from restaurants.models import Restaurant, MenuItem
from .models import Order, PromoCode

User = get_user_model()


class OrderFlowTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user('owner', 'o@example.com', 'x', role='restaurant_owner')
        self.other_owner = User.objects.create_user('owner2', 'o2@example.com', 'x', role='restaurant_owner')
        self.customer = User.objects.create_user('cust', 'c@example.com', 'x', role='user')
        self.r = Restaurant.objects.create(owner=self.owner, name='R', description='d', address='a', phone_number='1',
                                           delivery_fee=Decimal('2.00'), min_order=Decimal('5'))
        self.item = MenuItem.objects.create(restaurant=self.r, name='Burger', description='', price=Decimal('10.00'))
        self.other = Restaurant.objects.create(owner=self.other_owner, name='Other', description='d', address='a',
                                               phone_number='1')
        self.other_item = MenuItem.objects.create(restaurant=self.other, name='Taco', description='', price=4)

    def place(self, **extra):
        self.client.force_authenticate(self.customer)
        payload = {'order_items': [{'menu_item': self.item.id, 'quantity': 2}], 'delivery_address': '1 Main St',
                   **extra}
        return self.client.post('/api/orders/', payload, format='json')

    def test_create_order_prices_server_side(self):
        res = self.place(tip='1.00')
        self.assertEqual(res.status_code, 201, res.data)
        order = Order.objects.get()
        self.assertEqual(order.subtotal, Decimal('20.00'))
        self.assertEqual(order.delivery_fee, Decimal('2.00'))
        self.assertEqual(order.service_fee, Decimal('1.00'))  # 5%
        self.assertEqual(order.total_price, Decimal('24.00'))
        self.assertEqual(res.data['events'][0]['status'], 'pending')

    def test_delivery_options_change_fee(self):
        self.place(delivery_option='eco')
        self.assertEqual(Order.objects.get().delivery_fee, Decimal('1.00'))

    def test_rejects_mixed_restaurants_bad_quantity_and_min_order(self):
        self.client.force_authenticate(self.customer)
        mixed = {'order_items': [{'menu_item': self.item.id}, {'menu_item': self.other_item.id}],
                 'delivery_address': 'x'}
        self.assertEqual(self.client.post('/api/orders/', mixed, format='json').status_code, 400)
        bad_qty = {'order_items': [{'menu_item': self.item.id, 'quantity': 0}], 'delivery_address': 'x'}
        self.assertEqual(self.client.post('/api/orders/', bad_qty, format='json').status_code, 400)
        self.item.price = Decimal('2')
        self.item.save()
        small = {'order_items': [{'menu_item': self.item.id, 'quantity': 1}], 'delivery_address': 'x'}
        self.assertEqual(self.client.post('/api/orders/', small, format='json').status_code, 400)

    def test_paused_restaurant_rejects_orders(self):
        self.r.is_accepting_orders = False
        self.r.save()
        self.assertEqual(self.place().status_code, 400)

    def test_owner_cannot_order(self):
        self.client.force_authenticate(self.owner)
        res = self.client.post('/api/orders/', {'order_items': [{'menu_item': self.item.id}],
                                                'delivery_address': 'x'}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_promo_codes(self):
        PromoCode.objects.create(code='SAVE5', discount_type='flat', value=5, min_subtotal=15)
        PromoCode.objects.create(code='FIRST', discount_type='percent', value=50, max_discount=3, first_order_only=True)
        self.assertEqual(self.place(promo_code='nope').status_code, 400)
        res = self.place(promo_code='save5')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(Decimal(res.data['discount']), Decimal('5.00'))
        self.assertEqual(res.data['promo_code'], 'SAVE5')
        # Not the first order any more.
        self.assertEqual(self.place(promo_code='FIRST').status_code, 400)

    def test_quote_matches_order(self):
        self.client.force_authenticate(self.customer)
        payload = {'order_items': [{'menu_item': self.item.id, 'quantity': 2}], 'delivery_option': 'priority',
                   'tip': '2'}
        quote = self.client.post('/api/orders/quote/', payload, format='json').data
        self.assertEqual(len(quote['delivery_options']), 3)
        res = self.place(delivery_option='priority', tip='2')
        self.assertEqual(res.data['total_price'], quote['total'])

    def test_status_flow_points_and_permissions(self):
        order_id = self.place().data['id']
        url = f'/api/orders/{order_id}/'
        # Customer cannot change status; another owner cannot even see it.
        self.assertEqual(self.client.patch(url, {'status': 'preparing'}).status_code, 403)
        self.client.force_authenticate(self.other_owner)
        self.assertEqual(self.client.patch(url, {'status': 'preparing'}).status_code, 404)

        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.patch(url, {'status': 'confirmed'}).status_code, 200)
        self.assertEqual(self.client.patch(url, {'status': 'pending'}).status_code, 400)  # no going back
        self.assertEqual(self.client.patch(f'{url}update_status/', {'status': 'out_for_delivery'}).status_code, 200)
        self.assertEqual(self.client.patch(url, {'status': 'cancelled'}).status_code, 400)
        res = self.client.patch(url, {'status': 'delivered'})
        self.assertEqual([e['status'] for e in res.data['events']],
                         ['pending', 'confirmed', 'out_for_delivery', 'delivered'])
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.loyalty_points, 20)

    def test_points_redemption_and_refund_on_cancel(self):
        self.customer.loyalty_points = 450
        self.customer.save()
        res = self.place(use_points=True)
        self.assertEqual(res.data['points_redeemed'], 400)
        self.assertEqual(Decimal(res.data['points_discount']), Decimal('4.00'))
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.loyalty_points, 50)
        self.client.post(f"/api/orders/{res.data['id']}/cancel/")
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.loyalty_points, 450)

    def test_customer_cancel_only_while_pending(self):
        order_id = self.place().data['id']
        self.assertTrue(self.client.get(f'/api/orders/{order_id}/').data['can_cancel'])
        Order.objects.filter(pk=order_id).update(status='preparing')
        self.assertEqual(self.client.post(f'/api/orders/{order_id}/cancel/').status_code, 400)

    def test_schedule_rules(self):
        soon = (timezone.now() + timedelta(minutes=5)).isoformat()
        self.assertEqual(self.place(scheduled_for=soon).status_code, 400)
        later = (timezone.now() + timedelta(hours=3)).isoformat()
        self.assertEqual(self.place(scheduled_for=later).status_code, 201)

    def test_chat_and_reorder(self):
        order_id = self.place().data['id']
        self.client.post(f'/api/orders/{order_id}/messages/', {'body': 'Extra napkins please'})
        self.client.force_authenticate(self.owner)
        res = self.client.post(f'/api/orders/{order_id}/messages/', {'body': 'Sure!'})
        self.assertEqual(res.status_code, 201)
        self.assertEqual([(m['sender_role'], m['is_mine']) for m in res.data],
                         [('customer', False), ('restaurant', True)])
        self.client.force_authenticate(self.customer)
        self.item.is_available = False
        self.item.save()
        res = self.client.post(f'/api/orders/{order_id}/reorder/')
        self.assertEqual(res.data['unavailable'], ['Burger'])

    def test_analytics_for_owner_only(self):
        order_id = self.place().data['id']
        self.assertEqual(self.client.get('/api/orders/analytics/').status_code, 403)
        self.client.force_authenticate(self.owner)
        self.client.patch(f'/api/orders/{order_id}/', {'status': 'delivered'})
        data = self.client.get('/api/orders/analytics/?days=7').data
        self.assertEqual(data['kpis']['revenue'], 20.0)
        self.assertEqual(data['kpis']['orders'], 1)
        self.assertEqual(len(data['series']), 7)
        self.assertEqual(data['top_items'][0]['name'], 'Burger')
