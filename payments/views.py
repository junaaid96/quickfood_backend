import logging
import uuid

from django.http import HttpResponseRedirect
from rest_framework import permissions, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.response import Response

from orders.models import Order
from . import paysim, service
from .models import Payment

log = logging.getLogger(__name__)


def _order_for(request, order_id):
    try:
        return Order.objects.get(pk=int(order_id))
    except (TypeError, ValueError, Order.DoesNotExist):
        return None


def _error(exc):
    return Response({'detail': str(exc)}, status=getattr(exc, 'status', 400))


def _serialized(request, order):
    from orders.serializers import OrderSerializer
    fresh = Order.objects.select_related('restaurant__owner', 'user', 'promo_code', 'rider').prefetch_related(
        'items__menu_item', 'events', 'payments__refunds').get(pk=order.pk)
    return OrderSerializer(fresh, context={'request': request}).data


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def config(request):
    """What the checkout can offer. Payments run on PaySim BD, a simulator: no real money moves."""
    return Response({
        'gateways': service.enabled_gateways(),
        'bdt_rate': str(service.bdt_rate()),
        'simulator': True,
        'simulator_url': paysim.base_url(),
    })


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def start(request):
    order = _order_for(request, request.data.get('order'))
    if not order or order.user_id != request.user.id:
        return Response({'detail': 'Order not found.'}, status=404)
    try:
        payment, redirect_url = service.start_payment(order, request.data.get('gateway'), request.user)
    except service.PaymentError as exc:
        return _error(exc)
    return Response({'redirect_url': redirect_url, 'reference': payment.reference, 'amount_bdt': str(payment.amount_bdt)},
                    status=201)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def pay_on_delivery(request):
    order = _order_for(request, request.data.get('order'))
    if not order or order.user_id != request.user.id:
        return Response({'detail': 'Order not found.'}, status=404)
    try:
        order = service.pay_on_delivery(order, request.user)
    except service.PaymentError as exc:
        return _error(exc)
    return Response(_serialized(request, order))


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def refund(request):
    order = _order_for(request, request.data.get('order'))
    if not order:
        return Response({'detail': 'Order not found.'}, status=404)
    if request.user.role != 'restaurant_owner' or order.restaurant.owner_id != request.user.id:
        return Response({'detail': 'Only the restaurant can refund this order.'}, status=403)
    payment = service.paid_payment(order)
    if not payment:
        return Response({'detail': 'This order has no online payment to refund.'}, status=400)
    raw = request.data.get('amount')
    amount = None
    if raw not in (None, ''):
        try:
            from decimal import Decimal
            amount = Decimal(str(raw))
        except Exception:
            return Response({'amount': 'Enter a valid amount.'}, status=400)
    try:
        result, replayed = service.refund_payment(
            payment, amount, reason=request.data.get('reason', ''),
            key=request.data.get('idempotency_key') or f'owner-{uuid.uuid4().hex}', actor=request.user)
    except service.PaymentError as exc:
        return _error(exc)
    data = _serialized(request, order)
    return Response({'refund_status': result.status, 'replayed': replayed, 'error': result.error, 'order': data},
                    status=200 if replayed else 201)


# ----------------------------------------------------------------------------- gateway callbacks
# These are hit by the customer's browser (and the SSLCommerz IPN) without our JWT, so they only
# ever settle a payment after asking the gateway itself.

def _redirect(payment, outcome):
    return HttpResponseRedirect(service.return_url(payment.order_id, outcome))


def _fallback():
    return HttpResponseRedirect(f'{service.frontend_url()}/orders')


@api_view(['POST', 'GET'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
@throttle_classes([])
def sslcommerz_return(request, kind):
    if kind not in ('success', 'fail', 'cancel'):
        return _fallback()
    data = request.data if request.method == 'POST' else request.query_params
    payment = Payment.objects.filter(gateway='sslcommerz', reference=data.get('tran_id', '')).first()
    if not payment:
        return _fallback()
    try:
        if kind == 'success':
            outcome = service.settle_sslcommerz(payment, data.get('val_id', ''))
        else:
            outcome = 'cancelled' if kind == 'cancel' else 'failed'
            service.mark_unpaid(payment, outcome, data.get('error') or ('Cancelled in SSLCommerz' if kind == 'cancel' else 'Card payment failed'))
    except paysim.PaySimError as exc:
        log.warning('sslcommerz return %s: %s', payment.reference, exc)
        outcome = 'pending'
    return _redirect(payment, outcome)


@api_view(['POST'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
@throttle_classes([])
def sslcommerz_ipn(request):
    payment = Payment.objects.filter(gateway='sslcommerz', reference=request.data.get('tran_id', '')).first()
    if not payment:
        return Response({'detail': 'unknown tran_id'}, status=404)
    if request.data.get('status') in ('VALID', 'VALIDATED'):
        try:
            outcome = service.settle_sslcommerz(payment, request.data.get('val_id', ''))
        except paysim.PaySimError:
            return Response({'detail': 'validation unavailable'}, status=503)
    else:
        status_value = request.data.get('status', '')
        outcome = 'cancelled' if status_value == 'CANCELLED' else 'failed'
        service.mark_unpaid(payment, outcome, f'IPN {status_value}')
    return Response({'ok': True, 'outcome': outcome})


@api_view(['GET'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
@throttle_classes([])
def bkash_callback(request):
    payment = Payment.objects.filter(gateway='bkash', session_id=request.query_params.get('paymentID', '')).first()
    if not payment:
        return _fallback()
    try:
        outcome = service.settle_bkash(payment, request.query_params.get('status', ''))
    except paysim.PaySimError as exc:
        log.warning('bkash callback %s: %s', payment.reference, exc)
        outcome = 'pending'
    return _redirect(payment, outcome)


@api_view(['GET'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
@throttle_classes([])
def nagad_callback(request):
    payment = Payment.objects.filter(gateway='nagad', session_id=request.query_params.get('payment_ref_id', '')).first()
    if not payment:
        return _fallback()
    try:
        outcome = service.settle_nagad(payment)
    except paysim.PaySimError as exc:
        log.warning('nagad callback %s: %s', payment.reference, exc)
        outcome = 'pending'
    return _redirect(payment, outcome)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def check(request):
    """Re-ask the gateway (used by the order page when a return was interrupted)."""
    order = _order_for(request, request.data.get('order'))
    if not order or order.user_id != request.user.id:
        return Response({'detail': 'Order not found.'}, status=404)
    payment = order.payments.order_by('-created_at').first()
    if payment and payment.status == 'initiated' and payment.session_id:
        try:
            if payment.gateway == 'nagad':
                service.settle_nagad(payment)
            elif payment.gateway == 'bkash':
                data = paysim.BKash.query(payment.session_id)
                if data.get('transactionStatus') == 'Completed':
                    service.mark_paid(payment, trx_id=data.get('trxId', ''))
        except paysim.PaySimError:
            pass
    return Response(_serialized(request, order))
