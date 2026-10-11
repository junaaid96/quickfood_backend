"""
Ably realtime, without an extra SDK: token requests are signed locally (HMAC, no network call)
and server events go out through Ably's REST publish endpoint. ABLY_API_KEY stays server-side.

Channels
  user:<id>   status pushes for a customer or restaurant owner (subscribe only)
  order:<id>  rider location for one order (restaurant publishes, customer subscribes)

Everything here is best effort: the REST API stays the source of truth and clients keep polling as a fallback.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import urllib.parse
import urllib.request

from django.db import transaction

log = logging.getLogger(__name__)
TOKEN_TTL_MS = 60 * 60 * 1000
MAX_ORDER_CHANNELS = 40
RIDERS_CHANNEL = 'riders'  # broadcast: an order is waiting for a rider


def api_key():
    return os.getenv('ABLY_API_KEY', '').strip()


def enabled():
    key = api_key()
    return ':' in key and '.' in key.split(':', 1)[0]


def user_channel(user_id):
    return f'user:{user_id}'


def order_channel(order_id):
    return f'order:{order_id}'


def capability_for(user):
    from .models import Order
    cap = {user_channel(user.id): ['subscribe']}
    if user.role == 'restaurant_owner':
        ids = Order.objects.filter(restaurant__owner=user, status__in=Order.ACTIVE_STATUSES).values_list('id', flat=True)
        ops = ['publish', 'subscribe', 'presence']
    elif user.role == 'rider':
        cap[RIDERS_CHANNEL] = ['subscribe']
        ids = Order.objects.filter(rider=user, status__in=Order.ACTIVE_STATUSES).values_list('id', flat=True)
        ops = ['publish', 'subscribe', 'presence']
    else:
        ids = Order.objects.filter(user=user, status__in=Order.ACTIVE_STATUSES).values_list('id', flat=True)
        ops = ['subscribe', 'history']
    for order_id in list(ids)[:MAX_ORDER_CHANNELS]:
        cap[order_channel(order_id)] = ops
    return cap


def token_request_for(user):
    key_name, key_secret = api_key().split(':', 1)
    req = {
        'keyName': key_name,
        'ttl': TOKEN_TTL_MS,
        'capability': json.dumps(capability_for(user), separators=(',', ':')),
        'clientId': f'user-{user.id}',
        'timestamp': int(time.time() * 1000),
        'nonce': secrets.token_hex(16),
    }
    signed = '\n'.join(str(req[k]) for k in ('keyName', 'ttl', 'capability', 'clientId', 'timestamp', 'nonce')) + '\n'
    req['mac'] = base64.b64encode(hmac.new(key_secret.encode(), signed.encode(), hashlib.sha256).digest()).decode()
    return req


def _default_publisher(channel, name, data):
    url = f'https://rest.ably.io/channels/{urllib.parse.quote(channel, safe="")}/messages'
    body = json.dumps({'name': name, 'data': data}).encode()
    auth = base64.b64encode(api_key().encode()).decode()
    request = urllib.request.Request(url, data=body, method='POST', headers={
        'Content-Type': 'application/json', 'Authorization': f'Basic {auth}'})
    urllib.request.urlopen(request, timeout=3).close()


publisher = _default_publisher


def publish(channel, name, data):
    if not enabled():
        return
    try:
        publisher(channel, name, data)
    except Exception as exc:  # never break an API call because realtime is down
        log.warning('ably publish failed: %s', exc)


def push_status(order, event='order.status'):
    """Tell the customer and the restaurant that an order moved, after the transaction commits."""
    payload = {
        'id': order.id,
        'status': order.status,
        'status_label': order.get_status_display(),
        'restaurant': order.restaurant.name,
        'estimated_delivery_at': order.estimated_delivery_at.isoformat() if order.estimated_delivery_at else None,
        'updated_at': order.updated_at.isoformat() if order.updated_at else None,
        'rider': order.rider_id,
        'event': event,
    }
    targets = {order.user_id, order.restaurant.owner_id} | ({order.rider_id} if order.rider_id else set())

    def send():
        for user_id in targets:
            publish(user_channel(user_id), 'order.status', payload)
        publish(order_channel(order.id), 'order.status', payload)
        if order.rider_id is None and order.status in order.RIDER_CLAIMABLE:
            publish(RIDERS_CHANNEL, 'order.available', payload)
        elif event == 'order.rider_assigned':
            publish(RIDERS_CHANNEL, 'order.taken', payload)

    transaction.on_commit(send)
