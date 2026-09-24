from django.db import migrations
from django.db.models import F


def backfill_subtotal(apps, schema_editor):
    # Orders placed before fees existed: their total was the food subtotal.
    Order = apps.get_model('orders', 'Order')
    Order.objects.filter(subtotal=0).update(subtotal=F('total_price'))


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0002_alter_order_options_order_contact_phone_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_subtotal, migrations.RunPython.noop),
    ]
