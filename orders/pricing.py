"""
Server-side pricing. The checkout quote and order creation both go through
`build_quote`, so what the customer sees is exactly what they pay.
"""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone

CENT = Decimal('0.01')
TRAVEL_MINUTES = 15
SERVICE_FEE_RATE = Decimal('0.05')
SERVICE_FEE_MIN = Decimal('0.99')
SERVICE_FEE_MAX = Decimal('4.99')
POINTS_PER_UNIT = 100          # 100 points = 1.00 off
MAX_POINTS_SHARE = Decimal('0.5')  # points can cover at most half of the food
MAX_TIP = Decimal('100')

DELIVERY_OPTIONS = {
    'priority': {
        'label': 'Priority',
        'description': 'Straight to you, no stops on the way.',
        'fee_delta': Decimal('2.49'),
        'minutes_delta': -8,
    },
    'standard': {
        'label': 'Standard',
        'description': 'The usual route and timing.',
        'fee_delta': Decimal('0'),
        'minutes_delta': 0,
    },
    'eco': {
        'label': 'Wait & Save',
        'description': 'Batched with nearby orders. Fewer trips, lower emissions, lower fee.',
        'fee_delta': Decimal('-1.00'),
        'minutes_delta': 12,
    },
}


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def service_fee_for(subtotal):
    if subtotal <= 0:
        return Decimal('0.00')
    return money(min(max(subtotal * SERVICE_FEE_RATE, SERVICE_FEE_MIN), SERVICE_FEE_MAX))


def delivery_fee_for(restaurant, option):
    delta = DELIVERY_OPTIONS[option]['fee_delta']
    return money(max(restaurant.delivery_fee + delta, Decimal('0')))


def eta_minutes_for(restaurant, option):
    return max(10, restaurant.prep_time_minutes + TRAVEL_MINUTES + DELIVERY_OPTIONS[option]['minutes_delta'])


def delivery_options_for(restaurant):
    return [
        {
            'value': key,
            'label': cfg['label'],
            'description': cfg['description'],
            'fee': str(delivery_fee_for(restaurant, key)),
            'eta_minutes': eta_minutes_for(restaurant, key),
        }
        for key, cfg in DELIVERY_OPTIONS.items()
    ]


def evaluate_promo(code, restaurant, user, subtotal, delivery_fee):
    """Return (promo, discount, error_message)."""
    from .models import PromoCode, Order

    promo = PromoCode.objects.filter(code__iexact=code.strip()).first()
    if promo is None or not promo.is_valid_now:
        return None, Decimal('0'), 'This code is not valid.'
    if promo.restaurant_id and promo.restaurant_id != restaurant.id:
        return None, Decimal('0'), f'This code only works at {promo.restaurant.name}.'
    if subtotal < promo.min_subtotal:
        return None, Decimal('0'), f'Add {money(promo.min_subtotal - subtotal)} more to use this code.'
    if promo.first_order_only and user and user.is_authenticated:
        if Order.objects.filter(user=user).exclude(status='cancelled').exists():
            return None, Decimal('0'), 'This code is for your first order only.'

    if promo.discount_type == 'percent':
        discount = subtotal * promo.value / Decimal('100')
    elif promo.discount_type == 'flat':
        discount = promo.value
    else:
        discount = delivery_fee
    if promo.max_discount is not None:
        discount = min(discount, promo.max_discount)
    discount = min(discount, subtotal + delivery_fee)
    return promo, money(discount), None


def build_quote(restaurant, lines, user=None, delivery_option='standard', promo_code='', use_points=False,
                tip=Decimal('0'), scheduled_for=None):
    """
    `lines` is a list of (MenuItem, quantity). Returns a dict of Decimals plus metadata.
    """
    if delivery_option not in DELIVERY_OPTIONS:
        delivery_option = 'standard'
    subtotal = money(sum((item.price * qty for item, qty in lines), Decimal('0')))
    delivery_fee = delivery_fee_for(restaurant, delivery_option)
    service_fee = service_fee_for(subtotal)
    tip = money(min(max(Decimal(tip or 0), Decimal('0')), MAX_TIP))

    promo, discount, promo_error = None, Decimal('0.00'), None
    if promo_code:
        promo, discount, promo_error = evaluate_promo(promo_code, restaurant, user, subtotal, delivery_fee)
        discount = money(discount)

    points_redeemed, points_discount = 0, Decimal('0.00')
    available_points = user.loyalty_points if user and user.is_authenticated else 0
    if use_points and available_points >= POINTS_PER_UNIT:
        cap = max(subtotal - discount, Decimal('0')) * MAX_POINTS_SHARE
        units = min(available_points // POINTS_PER_UNIT, int(cap))
        points_redeemed = units * POINTS_PER_UNIT
        points_discount = money(units)

    total = money(max(subtotal + delivery_fee + service_fee + tip - discount - points_discount, Decimal('0')))

    eta_minutes = eta_minutes_for(restaurant, delivery_option)
    start = scheduled_for or timezone.now()
    estimated_delivery_at = scheduled_for if scheduled_for else start + timedelta(minutes=eta_minutes)

    return {
        'subtotal': subtotal,
        'delivery_fee': delivery_fee,
        'service_fee': service_fee,
        'discount': discount,
        'points_discount': points_discount,
        'points_redeemed': points_redeemed,
        'points_available': available_points,
        'points_to_earn': int(subtotal),
        'tip': tip,
        'total': total,
        'promo': promo,
        'promo_error': promo_error,
        'delivery_option': delivery_option,
        'eta_minutes': eta_minutes,
        'estimated_delivery_at': estimated_delivery_at,
        'below_min_order': subtotal < restaurant.min_order,
        'min_order': restaurant.min_order,
    }
