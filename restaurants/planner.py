"""
Budget Bites: build a complete meal for N people that fits a total budget.

For every restaurant we try to give each person a main, then spend what is left on
sides, drinks and a dessert to share. The budget covers the whole bill: food, the
restaurant's delivery fee and the estimated service fee.
"""
from decimal import Decimal, ROUND_HALF_UP

from orders.pricing import service_fee_for

SIDE_WORDS = ('side', 'starter', 'appetizer', 'snack', 'small', 'bread', 'soup', 'salad')
DRINK_WORDS = ('drink', 'beverage', 'juice', 'coffee', 'tea', 'shake', 'lassi', 'soda')
DESSERT_WORDS = ('dessert', 'sweet', 'cake', 'ice cream')

STRATEGIES = {
    'popular': "Crowd favourite",
    'value': "Most food for the money",
    'treat': "Treat yourself",
}


def _slot(item):
    cat = item.category.lower()
    if any(w in cat for w in DRINK_WORDS):
        return 'drink'
    if any(w in cat for w in DESSERT_WORDS):
        return 'dessert'
    if any(w in cat for w in SIDE_WORDS):
        return 'side'
    return 'main'


def _money(value):
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _bill(subtotal, delivery_fee):
    return subtotal + delivery_fee + service_fee_for(subtotal)


def plan_for_restaurant(restaurant, items, budget, people, popularity, strategy='popular'):
    """Return a plan dict or None when nothing sensible fits."""
    budget = Decimal(budget)
    delivery_fee = restaurant.delivery_fee

    slots = {'main': [], 'side': [], 'drink': [], 'dessert': []}
    for item in items:
        slots[_slot(item)].append(item)
    mains = slots['main']
    if not mains:
        return None

    def order_key(item):
        if strategy == 'value':
            return (item.price, -popularity.get(item.id, 0))
        if strategy == 'treat':
            return (-item.price, -popularity.get(item.id, 0))
        return (-popularity.get(item.id, 0), item.price)

    cheapest_main = min(m.price for m in mains)
    if _bill(cheapest_main * people, delivery_fee) > budget:
        return None

    chosen = {}
    subtotal = Decimal('0')

    def add(item):
        nonlocal subtotal
        chosen[item.id] = chosen.get(item.id, 0) + 1
        subtotal += item.price

    # One main per person, favouring variety, while keeping enough room for the others.
    ranked_mains = sorted(mains, key=order_key)
    for person in range(people):
        remaining_people = people - person - 1
        options = sorted(ranked_mains, key=lambda m: (chosen.get(m.id, 0), ranked_mains.index(m)))
        for main in options:
            if _bill(subtotal + main.price + cheapest_main * remaining_people, delivery_fee) <= budget:
                add(main)
                break

    # Extras: a side for every two people, a drink each, one dessert to share.
    wishlist = []
    wishlist += sorted(slots['side'], key=order_key)[: max(1, people // 2)]
    for i in range(people):
        drinks = sorted(slots['drink'], key=order_key)
        if drinks:
            wishlist.append(drinks[i % len(drinks)])
    wishlist += sorted(slots['dessert'], key=order_key)[:1]
    for extra in wishlist:
        if _bill(subtotal + extra.price, delivery_fee) <= budget:
            add(extra)

    if subtotal < restaurant.min_order:
        return None

    by_id = {i.id: i for i in items}
    total = _money(_bill(subtotal, delivery_fee))
    return {
        'strategy': strategy,
        'label': STRATEGIES[strategy],
        'items': [
            {'menu_item_id': item_id, 'name': by_id[item_id].name, 'price': str(by_id[item_id].price),
             'quantity': qty, 'category': by_id[item_id].category}
            for item_id, qty in chosen.items()
        ],
        'subtotal': str(_money(subtotal)),
        'delivery_fee': str(delivery_fee),
        'service_fee': str(service_fee_for(subtotal)),
        'estimated_total': str(total),
        'leftover': str(_money(budget - total)),
        'budget_used': round(float(total / budget) * 100) if budget else 0,
        'item_count': sum(chosen.values()),
    }
