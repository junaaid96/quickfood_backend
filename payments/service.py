"""
Online payments for QuickFood through PaySim BD.

Flow: the customer places an order with an online method, then `start_payment` opens a gateway
session and returns the hosted checkout URL. The gateway sends the customer's browser back to one of
our callback views (and, for SSLCommerz, posts an IPN server to server). We never trust the browser:
every success is confirmed server side (SSLCommerz validation API, bKash execute/query, Nagad
verify) and the amount is checked before an order is marked paid. Unpaid online orders stay hidden
from the kitchen until the payment settles.
"""
import hashlib
import logging
import os
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from . import paysim
from .models import Payment, Refund

log = logging.getLogger(__name__)
ONLINE_METHODS = ('bkash', 'nagad', 'rocket', 'upay', 'sslcommerz')
OFFLINE_METHODS = ('cash', 'card')
CENT = Decimal('0.01')


class PaymentError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def bdt_rate():
    try:
        rate = Decimal(os.getenv('PAYSIM_BDT_RATE', '120').strip() or '120')
    except Exception:
        rate = Decimal('120')
    return rate if rate > 0 else Decimal('120')


def to_bdt(amount):
    return (Decimal(amount) * bdt_rate()).quantize(CENT, rounding=ROUND_HALF_UP)


def api_url():
    return os.getenv('PUBLIC_API_URL', 'https://quickfood-backend-bice.vercel.app/api').strip().rstrip('/')


def frontend_url():
    return os.getenv('FRONTEND_URL', 'https://quickfood-frontend.vercel.app').strip().rstrip('/')


def return_url(order_id, outcome):
    return f'{frontend_url()}/orders/{order_id}?payment={outcome}'


def enabled_gateways():
    return [g for g in ONLINE_METHODS if paysim.configured(g)]


def kitchen_visible(queryset):
    """Orders the restaurant and riders may see: offline payment, or an online payment that settled."""
    paid = Payment.objects.filter(order=OuterRef('pk'), status='paid')
    return queryset.filter(Q(payment_method__in=OFFLINE_METHODS) | Q(Exists(paid)))


def is_settled(order):
    return order.payment_method in OFFLINE_METHODS or order.payments.filter(status='paid').exists()


def paid_payment(order):
    return order.payments.filter(status='paid').order_by('paid_at').first()


# --------------------------------------------------------------------------- starting a payment

def start_payment(order, gateway, user):
    if order.user_id != user.id:
        raise PaymentError('Only the customer can pay for this order.', 403)
    if gateway not in ONLINE_METHODS:
        raise PaymentError('Choose bKash, Nagad, Rocket, Upay or SSLCommerz.')
    if not paysim.configured(gateway):
        raise PaymentError(f'{dict(Payment.GATEWAYS)[gateway]} is not available right now.', 503)
    with transaction.atomic():
        from orders.models import Order
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if locked.status == 'cancelled':
            raise PaymentError('This order was cancelled.')
        if locked.payments.filter(status='paid').exists():
            raise PaymentError('This order is already paid.')
        if locked.status != 'pending':
            raise PaymentError('This order can no longer be paid online.')
        attempt = locked.payments.count() + 1
        locked.payment_method = gateway
        locked.save(update_fields=['payment_method', 'updated_at'])
        amount = locked.total_price
        payment = Payment.objects.create(
            order=locked, gateway=gateway, reference=f'QF{locked.id}-{attempt}',
            amount=amount, amount_bdt=to_bdt(amount),
        )
    try:
        session = _open_session(payment, locked, user)
    except paysim.PaySimError as exc:
        payment.status = 'failed'
        payment.failure_reason = str(exc)[:255]
        payment.save(update_fields=['status', 'failure_reason', 'updated_at'])
        raise PaymentError(str(exc), 502) from exc
    payment.session_id = session['session_id']
    payment.save(update_fields=['session_id', 'updated_at'])
    return payment, session['redirect_url']


def _open_session(payment, order, user):
    base = api_url()
    if payment.gateway == 'sslcommerz':
        return paysim.SSLCommerz.init(
            reference=payment.reference, amount_bdt=payment.amount_bdt, order_id=order.id,
            success_url=f'{base}/payments/sslcommerz/success/', fail_url=f'{base}/payments/sslcommerz/fail/',
            cancel_url=f'{base}/payments/sslcommerz/cancel/', ipn_url=f'{base}/payments/sslcommerz/ipn/',
            customer={'name': user.get_full_name() or user.username, 'email': user.email,
                      'phone': order.contact_phone or getattr(user, 'phone_number', ''), 'address': order.delivery_address[:200]},
        )
    if payment.gateway == 'bkash':
        digits = ''.join(ch for ch in (order.contact_phone or '') if ch.isdigit())[-11:]
        return paysim.BKash.create(reference=payment.reference, amount_bdt=payment.amount_bdt,
                                   payer_reference=digits or f'QF{order.user_id}', callback_url=f'{base}/payments/bkash/callback/')
    if payment.gateway == 'rocket':
        return paysim.Rocket.start(reference=payment.reference, amount_bdt=payment.amount_bdt)
    if payment.gateway == 'upay':
        return paysim.Upay.start(reference=payment.reference, amount_bdt=payment.amount_bdt,
                                 redirect_url=f'{base}/payments/upay/callback/')
    return paysim.Nagad.start(reference=payment.reference, amount_bdt=payment.amount_bdt, order_id=order.id,
                              callback_url=f'{base}/payments/nagad/callback/')


def pay_on_delivery(order, user):
    """Give up on paying online: the kitchen gets the order as cash on delivery."""
    if order.user_id != user.id:
        raise PaymentError('Only the customer can change the payment method.', 403)
    with transaction.atomic():
        from orders.models import Order
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if locked.payments.filter(status='paid').exists():
            raise PaymentError('This order is already paid.')
        if locked.status != 'pending':
            raise PaymentError('This order can no longer be changed.')
        locked.payment_method = 'cash'
        locked.save(update_fields=['payment_method', 'updated_at'])
        locked.payments.filter(status='initiated').update(status='cancelled', failure_reason='Switched to cash on delivery')
        _notify(locked, 'order.placed')
    return locked


# --------------------------------------------------------------------------- settling

def _notify(order, event):
    from orders.realtime import push_status
    push_status(order, event=event)


def mark_paid(payment, *, trx_id, val_id=''):
    """Idempotent: repeated callbacks/IPNs settle once."""
    duplicate = False
    with transaction.atomic():
        p = Payment.objects.select_for_update().select_related('order').get(pk=payment.pk)
        if p.status == 'paid':
            return p
        p.status = 'paid'
        p.trx_id = trx_id or p.trx_id
        p.val_id = val_id or p.val_id
        p.paid_at = timezone.now()
        p.failure_reason = ''
        p.save()
        order = p.order
        # Paid twice (two tabs, or a retry after the first one went through): give this one back.
        duplicate = order.payments.filter(status='paid').exclude(pk=p.pk).exists()
        if not duplicate:
            # Even if the customer switched to cash meanwhile, the money arrived: the order is paid.
            order.payment_method = p.gateway
            order.save(update_fields=['payment_method', 'updated_at'])
            if order.status != 'cancelled':
                _notify(order, 'order.paid')
        cancelled = order.status == 'cancelled'
    if duplicate or cancelled:
        reason = 'Duplicate payment' if duplicate else 'Order was cancelled'
        try:
            refund_payment(p, None, reason=reason, key=f'auto-{p.reference}', actor=None, automatic=True)
        except Exception as exc:  # the owner can still refund by hand
            log.warning('automatic refund for %s failed: %s', p.reference, exc)
    return p


def mark_unpaid(payment, status, reason=''):
    with transaction.atomic():
        p = Payment.objects.select_for_update().get(pk=payment.pk)
        if p.status != 'initiated':
            return p
        p.status = status
        p.failure_reason = (reason or '')[:255]
        p.save(update_fields=['status', 'failure_reason', 'updated_at'])
    return p


def _amount_matches(payment, value):
    try:
        return Decimal(str(value)).quantize(CENT) == payment.amount_bdt
    except Exception:
        return False


def settle_sslcommerz(payment, val_id):
    """Confirm with the SSLCommerz validation API. Returns 'success' or 'failed'."""
    if payment.status == 'paid':
        return 'success'
    if not val_id:
        mark_unpaid(payment, 'failed', 'Missing validation id')
        return 'failed'
    data = paysim.SSLCommerz.validate(val_id)
    if data.get('status') in ('VALID', 'VALIDATED') and data.get('tran_id') == payment.reference \
            and _amount_matches(payment, data.get('amount')) and data.get('currency_type', 'BDT') == 'BDT':
        mark_paid(payment, trx_id=data.get('bank_tran_id', ''), val_id=val_id)
        return 'success'
    mark_unpaid(payment, 'failed', 'Payment could not be validated')
    return 'failed'


def settle_bkash(payment, status):
    if payment.status == 'paid':
        return 'success'
    if status != 'success':
        mark_unpaid(payment, 'cancelled' if status == 'cancel' else 'failed',
                    'Cancelled in bKash' if status == 'cancel' else 'bKash payment failed')
        return 'cancelled' if status == 'cancel' else 'failed'
    data = paysim.BKash.execute(payment.session_id)
    if data.get('transactionStatus') != 'Completed':
        # Already executed (retry, double callback): ask bKash what happened.
        data = paysim.BKash.query(payment.session_id)
    if data.get('transactionStatus') == 'Completed' and _amount_matches(payment, data.get('amount')):
        mark_paid(payment, trx_id=data.get('trxId', ''))
        return 'success'
    mark_unpaid(payment, 'failed', data.get('errorMessageEn') or 'bKash payment failed')
    return 'failed'


def settle_nagad(payment):
    if payment.status == 'paid':
        return 'success'
    data = paysim.Nagad.verify(payment.session_id)
    status = data.get('status')
    if status == 'Success' and _amount_matches(payment, data.get('amount')) and data.get('orderId') == payment.reference:
        mark_paid(payment, trx_id=data.get('trxId', ''))
        return 'success'
    if status == 'ABORTED':
        mark_unpaid(payment, 'cancelled', 'Cancelled in Nagad')
        return 'cancelled'
    if status == 'PENDING':
        return 'pending'
    mark_unpaid(payment, 'failed', 'Nagad payment failed')
    return 'failed'


def settle_rocket(payment):
    if payment.status == 'paid':
        return 'success'
    data = paysim.Rocket.result(payment.session_id)
    result, code = data.get('RESULT', ''), data.get('RESULT_CODE', '')
    if result == 'OK' and code == '000':
        mark_paid(payment, trx_id=data.get('RRN') or payment.session_id)
        return 'success'
    if result == 'PENDING' or data.get('RESULT_PS') == 'PENDING':
        return 'pending'
    if result == 'TIMEOUT':
        mark_unpaid(payment, 'cancelled', 'Cancelled in Rocket')
        return 'cancelled'
    mark_unpaid(payment, 'failed', 'Rocket payment failed')
    return 'failed'


def settle_upay(payment):
    if payment.status == 'paid':
        return 'success'
    data = paysim.Upay.status(payment.reference)
    status = data.get('status')
    if status == 'success' and _amount_matches(payment, data.get('amount')) and data.get('txn_id') == payment.reference:
        mark_paid(payment, trx_id=data.get('trx_id') or '')
        return 'success'
    if status == 'pending':
        return 'pending'
    if status in ('cancelled', 'expired'):
        mark_unpaid(payment, 'cancelled', 'Cancelled in Upay')
        return 'cancelled'
    mark_unpaid(payment, 'failed', 'Upay payment failed')
    return 'failed'


# --------------------------------------------------------------------------- refunds

def _gateway_ref(key):
    return 'QFR' + hashlib.sha256(key.encode()).hexdigest()[:16].upper()


def refund_payment(payment, amount, *, reason, key, actor, automatic=False):
    """Full (amount=None) or partial refund. Idempotent on `key`."""
    key = (key or '').strip()[:64]
    if not key:
        raise PaymentError('A refund key is required.')
    with transaction.atomic():
        p = Payment.objects.select_for_update().get(pk=payment.pk)
        existing = Refund.objects.filter(idempotency_key=key).first()
        if existing:
            if existing.payment_id != p.id or (amount is not None and existing.amount != Decimal(amount).quantize(CENT)):
                raise PaymentError('This refund request was already used for a different amount.', 409)
            return existing, True
        if p.status != 'paid':
            raise PaymentError('Only paid orders can be refunded.')
        refundable = p.refundable_amount
        if refundable <= 0:
            raise PaymentError('This payment is already fully refunded.')
        value = refundable if amount is None else Decimal(amount).quantize(CENT)
        if value <= 0:
            raise PaymentError('Refund amount must be more than zero.')
        if value > refundable:
            raise PaymentError(f'You can refund at most {refundable}.')
        remaining_bdt = p.amount_bdt - p.refunded_bdt
        value_bdt = remaining_bdt if value == refundable else min(to_bdt(value), remaining_bdt)
        reason = (reason or ('Order cancelled' if automatic else 'Refund')).strip()[:200]
        ref = _gateway_ref(key)
        try:
            if p.gateway == 'sslcommerz':
                result = paysim.SSLCommerz.refund(bank_tran_id=p.trx_id, amount_bdt=value_bdt, remarks=reason, refund_trans_id=ref)
            elif p.gateway == 'bkash':
                result = paysim.BKash.refund(payment_id=p.session_id, trx_id=p.trx_id, amount_bdt=value_bdt, reason=reason, idempotency_key=ref)
            elif p.gateway == 'rocket':
                result = paysim.Rocket.refund(reference=p.reference, amount_bdt=value_bdt, reason=reason, idempotency_key=ref)
            elif p.gateway == 'upay':
                result = paysim.Upay.refund(reference=p.reference, amount_bdt=value_bdt)
            else:
                result = paysim.Nagad.refund(payment_ref_id=p.session_id, original_amount_bdt=p.amount_bdt,
                                             amount_bdt=value_bdt, reason=reason, reference_no=ref)
        except paysim.PaySimError as exc:
            raise PaymentError(str(exc), 502) from exc
        refund = Refund.objects.create(
            payment=p, amount=value, amount_bdt=value_bdt, status='refunded' if result['ok'] else 'failed',
            gateway_refund_id=result.get('refund_id', ''), reason=reason, error=result.get('error', '')[:255],
            idempotency_key=key, automatic=automatic, actor=actor,
        )
        p.save(update_fields=['updated_at'])
        _notify(p.order, 'order.refund')
    return refund, False


def refund_on_cancel(order_id):
    """A paid order was cancelled (customer or kitchen): give the money back automatically."""
    from orders.models import Order
    order = Order.objects.filter(pk=order_id).first()
    if not order:
        return
    payment = paid_payment(order)
    if not payment or payment.refundable_amount <= 0:
        return
    try:
        refund_payment(payment, None, reason='Order cancelled', key=f'auto-cancel-{order.id}', actor=None, automatic=True)
    except Exception as exc:
        log.warning('automatic refund for order %s failed: %s', order_id, exc)


def payment_summary(order):
    payments = list(order.payments.all())
    if order.payment_method in OFFLINE_METHODS and not any(p.status == 'paid' for p in payments):
        return {'method': order.payment_method, 'online': False, 'status': 'on_delivery'}
    paid = next((p for p in sorted(payments, key=lambda x: x.paid_at or x.created_at) if p.status == 'paid'), None)
    latest = payments[0] if payments else None
    current = paid or latest
    if not current:
        return {'method': order.payment_method, 'online': True, 'status': 'unpaid', 'attempts': 0, 'refunds': []}
    refunds = list(current.refunds.all()) if paid else []
    refunded = sum((r.amount for r in refunds if r.status == 'refunded'), Decimal('0'))
    refunded_bdt = sum((r.amount_bdt for r in refunds if r.status == 'refunded'), Decimal('0'))
    if paid:
        status = 'refunded' if refunded >= paid.amount else 'partially_refunded' if refunded > 0 else 'paid'
    else:
        status = {'initiated': 'unpaid'}.get(current.status, current.status)
    return {
        'method': current.gateway,
        'method_label': current.get_gateway_display(),
        'online': True,
        'status': status,
        'reference': current.reference,
        'trx_id': current.trx_id,
        'amount': str(current.amount),
        'amount_bdt': str(current.amount_bdt),
        'bdt_rate': str(bdt_rate()),
        'paid_at': current.paid_at.isoformat() if current.paid_at else None,
        'failure_reason': '' if paid else current.failure_reason,
        'attempts': len(payments),
        'refunded_amount': str(refunded),
        'refunded_bdt': str(refunded_bdt),
        'refundable_amount': str(max(Decimal('0'), current.amount - refunded) if paid else Decimal('0')),
        'refunds': [{
            'id': r.id, 'amount': str(r.amount), 'amount_bdt': str(r.amount_bdt), 'status': r.status,
            'reference': r.gateway_refund_id, 'reason': r.reason, 'error': r.error, 'automatic': r.automatic,
            'created_at': r.created_at.isoformat(),
        } for r in refunds],
        'simulator': True,
    }
