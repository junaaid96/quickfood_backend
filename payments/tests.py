import os
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from orders.models import Order
from restaurants.models import MenuItem, Restaurant
from . import paysim
from .models import Payment, Refund

User = get_user_model()
ENV = {
    'PAYSIM_SSLCOMMERZ_STORE_ID': 'qf', 'PAYSIM_SSLCOMMERZ_STORE_PASSWD': 'pw',
    'PAYSIM_BKASH_APP_KEY': 'k', 'PAYSIM_BKASH_APP_SECRET': 's', 'PAYSIM_BKASH_USERNAME': 'u', 'PAYSIM_BKASH_PASSWORD': 'p',
    'PAYSIM_NAGAD_MERCHANT_ID': 'm', 'PAYSIM_NAGAD_MERCHANT_PRIVATE_KEY': 'x', 'PAYSIM_BDT_RATE': '120',
    'FRONTEND_URL': 'https://front.test', 'PUBLIC_API_URL': 'https://api.test/api',
}


def ok_refund(**kwargs):
    return {'ok': True, 'refund_id': 'RF-' + str(kwargs.get('amount_bdt')), 'error': ''}


class PaymentFlowTests(APITestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, ENV)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.owner = User.objects.create_user('owner', 'o@example.com', 'x', role='restaurant_owner')
        self.stranger = User.objects.create_user('owner2', 'o2@example.com', 'x', role='restaurant_owner')
        self.customer = User.objects.create_user('cust', 'c@example.com', 'x', role='user')
        self.r = Restaurant.objects.create(owner=self.owner, name='R', description='d', address='a', phone_number='1',
                                           delivery_fee=Decimal('2.00'), min_order=Decimal('5'))
        self.item = MenuItem.objects.create(restaurant=self.r, name='Burger', description='', price=Decimal('10.00'))

    def place(self, method):
        self.client.force_authenticate(self.customer)
        res = self.client.post('/api/orders/', {'order_items': [{'menu_item': self.item.id, 'quantity': 2}],
                                                'delivery_address': '1 Main St', 'payment_method': method,
                                                'contact_phone': '01712345678'}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        return Order.objects.get(pk=res.data['id'])

    def owner_ids(self):
        self.client.force_authenticate(self.owner)
        return [o['id'] for o in self.client.get('/api/orders/').data]

    def start(self, order, gateway, session='S1', url='https://sim.test/pay'):
        self.client.force_authenticate(self.customer)
        target = {'sslcommerz': 'SSLCommerz', 'bkash': 'BKash', 'nagad': 'Nagad'}[gateway]
        method = {'sslcommerz': 'init', 'bkash': 'create', 'nagad': 'start'}[gateway]
        with mock.patch.object(getattr(paysim, target), method, return_value={'redirect_url': url, 'session_id': session}) as m:
            res = self.client.post('/api/payments/start/', {'order': order.id, 'gateway': gateway}, format='json')
        return res, m

    def pay_bkash(self, order, session='PID1'):
        self.start(order, 'bkash', session)
        with mock.patch.object(paysim.BKash, 'execute', return_value={'transactionStatus': 'Completed', 'trxId': 'TRX1', 'amount': str(order.total_price * 120)}):
            res = self.client.get('/api/payments/bkash/callback/', {'paymentID': session, 'status': 'success'})
        return res

    def test_online_order_waits_for_payment_then_reaches_kitchen(self):
        order = self.place('bkash')
        self.assertNotIn(order.id, self.owner_ids())
        res, create = self.start(order, 'bkash', 'PID1')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['redirect_url'], 'https://sim.test/pay')
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs['amount_bdt'], order.total_price * 120)
        self.assertEqual(kwargs['callback_url'], 'https://api.test/api/payments/bkash/callback/')
        with mock.patch.object(paysim.BKash, 'execute', return_value={'transactionStatus': 'Completed', 'trxId': 'TRX9', 'amount': str(order.total_price * 120)}):
            back = self.client.get('/api/payments/bkash/callback/', {'paymentID': 'PID1', 'status': 'success'})
        self.assertEqual(back.status_code, 302)
        self.assertEqual(back['Location'], f'https://front.test/orders/{order.id}?payment=success')
        self.assertIn(order.id, self.owner_ids())
        self.client.force_authenticate(self.customer)
        payment = self.client.get(f'/api/orders/{order.id}/').data['payment']
        self.assertEqual(payment['status'], 'paid')
        self.assertEqual(payment['trx_id'], 'TRX9')
        self.assertEqual(payment['method_label'], 'bKash')

    def test_callbacks_are_idempotent_and_never_trust_the_browser(self):
        order = self.place('bkash')
        self.start(order, 'bkash', 'PID2')
        # Gateway says not completed: a forged "success" must not mark the order paid.
        with mock.patch.object(paysim.BKash, 'execute', return_value={'externalCode': '2056'}), \
                mock.patch.object(paysim.BKash, 'query', return_value={'transactionStatus': 'Initiated'}):
            res = self.client.get('/api/payments/bkash/callback/', {'paymentID': 'PID2', 'status': 'success'})
        self.assertIn('payment=failed', res['Location'])
        self.assertEqual(Payment.objects.get(session_id='PID2').status, 'failed')
        # Retry with a new attempt; a duplicate callback settles once.
        self.pay_bkash(order, 'PID3')
        with mock.patch.object(paysim.BKash, 'execute') as again:
            self.client.get('/api/payments/bkash/callback/', {'paymentID': 'PID3', 'status': 'success'})
        again.assert_not_called()
        self.assertEqual(order.payments.filter(status='paid').count(), 1)
        self.assertEqual(order.payments.count(), 2)

    def test_sslcommerz_validates_amount_and_ipn(self):
        order = self.place('sslcommerz')
        self.start(order, 'sslcommerz', 'SK1')
        payment = Payment.objects.get(order=order)
        bad = {'status': 'VALID', 'tran_id': payment.reference, 'amount': '1.00', 'bank_tran_id': 'B1'}
        with mock.patch.object(paysim.SSLCommerz, 'validate', return_value=bad):
            res = self.client.post('/api/payments/sslcommerz/success/', {'tran_id': payment.reference, 'val_id': 'V1'})
        self.assertIn('payment=failed', res['Location'])
        res, _ = self.start(order, 'sslcommerz', 'SK2')
        payment = order.payments.order_by('-id').first()
        good = {'status': 'VALID', 'tran_id': payment.reference, 'amount': str(payment.amount_bdt), 'bank_tran_id': 'B2'}
        with mock.patch.object(paysim.SSLCommerz, 'validate', return_value=good):
            ipn = self.client.post('/api/payments/sslcommerz/ipn/', {'tran_id': payment.reference, 'val_id': 'V2', 'status': 'VALID'})
            back = self.client.post('/api/payments/sslcommerz/success/', {'tran_id': payment.reference, 'val_id': 'V2'})
        self.assertEqual(ipn.data['outcome'], 'success')
        self.assertIn('payment=success', back['Location'])
        payment.refresh_from_db()
        self.assertEqual((payment.status, payment.trx_id), ('paid', 'B2'))
        cancel = self.client.post('/api/payments/sslcommerz/cancel/', {'tran_id': payment.reference})
        self.assertIn('payment=cancelled', cancel['Location'])
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'paid')  # a late cancel never undoes a settled payment

    def test_nagad_cancel_then_pay_on_delivery(self):
        order = self.place('nagad')
        self.start(order, 'nagad', 'NREF1')
        with mock.patch.object(paysim.Nagad, 'verify', return_value={'status': 'ABORTED'}):
            res = self.client.get('/api/payments/nagad/callback/', {'payment_ref_id': 'NREF1', 'status': 'Aborted'})
        self.assertIn('payment=cancelled', res['Location'])
        self.assertNotIn(order.id, self.owner_ids())
        self.client.force_authenticate(self.customer)
        res = self.client.post('/api/payments/pay-on-delivery/', {'order': order.id}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['payment']['status'], 'on_delivery')
        self.assertIn(order.id, self.owner_ids())

    def test_owner_refunds_partial_then_full_idempotently(self):
        order = self.place('bkash')
        self.pay_bkash(order)
        total = order.total_price
        self.client.force_authenticate(self.stranger)
        denied = self.client.post('/api/payments/refund/', {'order': order.id, 'amount': '1.00'}, format='json')
        self.assertEqual(denied.status_code, 403)
        self.client.force_authenticate(self.owner)
        with mock.patch.object(paysim.BKash, 'refund', side_effect=ok_refund) as refund:
            first = self.client.post('/api/payments/refund/', {'order': order.id, 'amount': '5.00', 'reason': 'Missing fries', 'idempotency_key': 'k1'}, format='json')
            again = self.client.post('/api/payments/refund/', {'order': order.id, 'amount': '5.00', 'idempotency_key': 'k1'}, format='json')
            over = self.client.post('/api/payments/refund/', {'order': order.id, 'amount': str(total), 'idempotency_key': 'k2'}, format='json')
            rest = self.client.post('/api/payments/refund/', {'order': order.id, 'idempotency_key': 'k3'}, format='json')
            done = self.client.post('/api/payments/refund/', {'order': order.id, 'idempotency_key': 'k4'}, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(refund.call_args_list[0].kwargs['amount_bdt'], Decimal('600.00'))
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.data['replayed'])
        self.assertEqual(over.status_code, 400)
        self.assertEqual(rest.status_code, 201)
        self.assertEqual(refund.call_count, 2)
        self.assertEqual(rest.data['order']['payment']['status'], 'refunded')
        self.assertEqual(Decimal(rest.data['order']['payment']['refunded_bdt']), total * 120)
        self.assertEqual(done.status_code, 400)

    def test_declined_refund_is_recorded_without_moving_money(self):
        order = self.place('bkash')
        self.pay_bkash(order)
        self.client.force_authenticate(self.owner)
        with mock.patch.object(paysim.BKash, 'refund', return_value={'ok': False, 'refund_id': '', 'error': 'Refund declined by bKash.'}):
            res = self.client.post('/api/payments/refund/', {'order': order.id, 'amount': '3.00', 'idempotency_key': 'd1'}, format='json')
        self.assertEqual(res.data['refund_status'], 'failed')
        self.assertEqual(res.data['order']['payment']['status'], 'paid')
        self.assertEqual(res.data['order']['payment']['refundable_amount'], str(order.total_price))

    def test_cancelling_a_paid_order_refunds_automatically(self):
        order = self.place('bkash')
        self.pay_bkash(order)
        self.client.force_authenticate(self.owner)
        with mock.patch.object(paysim.BKash, 'refund', side_effect=ok_refund), self.captureOnCommitCallbacks(execute=True):
            res = self.client.patch(f'/api/orders/{order.id}/', {'status': 'cancelled', 'note': 'Out of stock'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        refund = Refund.objects.get()
        self.assertTrue(refund.automatic)
        self.assertEqual(refund.amount, order.total_price)

    def test_customer_cannot_pay_someone_elses_order_or_unknown_gateway(self):
        order = self.place('bkash')
        other = User.objects.create_user('c2', 'c2@example.com', 'x', role='user')
        self.client.force_authenticate(other)
        res = self.client.post('/api/payments/start/', {'order': order.id, 'gateway': 'bkash'}, format='json')
        self.assertEqual(res.status_code, 404)
        self.client.force_authenticate(self.customer)
        res = self.client.post('/api/payments/start/', {'order': order.id, 'gateway': 'paypal'}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_config_lists_configured_gateways(self):
        res = self.client.get('/api/payments/config/')
        self.assertEqual(res.data['gateways'], ['bkash', 'nagad', 'sslcommerz'])
        with mock.patch.dict(os.environ, {'PAYSIM_NAGAD_MERCHANT_PRIVATE_KEY': ''}):
            self.assertEqual(self.client.get('/api/payments/config/').data['gateways'], ['bkash', 'sslcommerz'])
