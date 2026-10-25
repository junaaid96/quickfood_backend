"""
Thin clients for PaySim BD (https://github.com/junaaid96/paysim-bd), an open-source simulator of
SSLCommerz, bKash, Nagad, Rocket (DBBL) and Upay. The request shapes are the real gateways' shapes, so swapping the base
URL and credentials for a real sandbox is the only change a production integration would need.

All amounts sent to the gateways are BDT strings with two decimals.
"""
import base64
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from pathlib import Path

log = logging.getLogger(__name__)
TIMEOUT = 15


class PaySimError(Exception):
    """The simulator answered with an error (or could not be reached)."""


def setting(name, default=''):
    return os.getenv(name, default).strip()


def base_url():
    return setting('PAYSIM_BASE_URL', 'https://paysim-bd-codejborg.vercel.app').rstrip('/')


def bdt(amount):
    return f'{Decimal(amount).quantize(Decimal("0.01"))}'


def _request(method, path, *, json_body=None, form=None, params=None, headers=None):
    url = base_url() + path
    if params:
        url += ('&' if '?' in url else '?') + urllib.parse.urlencode(params)
    data = None
    hdrs = {'Accept': 'application/json', **(headers or {})}
    if json_body is not None:
        data = json.dumps(json_body).encode()
        hdrs['Content-Type'] = 'application/json'
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        hdrs['Content-Type'] = 'application/x-www-form-urlencoded'
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            raw = res.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw or b'{}')
        except ValueError:
            body = {}
        message = body.get('message') or body.get('errorMessageEn') or f'HTTP {exc.code}'
        err = PaySimError(message)
        err.body = body
        raise err from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise PaySimError('Payment gateway is unreachable right now. Please try again.') from exc
    try:
        return json.loads(raw or b'{}')
    except ValueError as exc:
        raise PaySimError('Unexpected gateway response.') from exc


# --------------------------------------------------------------------------- SSLCommerz

class SSLCommerz:
    name = 'sslcommerz'

    @staticmethod
    def creds():
        return {'store_id': setting('PAYSIM_SSLCOMMERZ_STORE_ID'), 'store_passwd': setting('PAYSIM_SSLCOMMERZ_STORE_PASSWD')}

    @classmethod
    def init(cls, *, reference, amount_bdt, success_url, fail_url, cancel_url, ipn_url, customer, order_id):
        body = _request('POST', '/gwprocess/v4/api.php', form={
            **cls.creds(),
            'total_amount': bdt(amount_bdt), 'currency': 'BDT', 'tran_id': reference,
            'product_category': 'food', 'product_name': f'QuickFood order #{order_id}', 'product_profile': 'general',
            'success_url': success_url, 'fail_url': fail_url, 'cancel_url': cancel_url, 'ipn_url': ipn_url,
            'cus_name': customer.get('name') or 'QuickFood customer', 'cus_email': customer.get('email') or 'customer@quickfood.demo',
            'cus_phone': customer.get('phone') or '01700000000', 'cus_add1': customer.get('address') or 'N/A',
            'cus_city': 'N/A', 'cus_country': 'Bangladesh', 'shipping_method': 'NO', 'num_of_item': 1,
            'value_a': str(order_id),
        })
        if body.get('status') != 'SUCCESS' or not body.get('GatewayPageURL'):
            raise PaySimError(body.get('failedreason') or 'Could not start the SSLCommerz session.')
        return {'redirect_url': body['GatewayPageURL'], 'session_id': body.get('sessionkey', '')}

    @classmethod
    def validate(cls, val_id):
        return _request('GET', '/validator/api/validationserverAPI.php',
                        params={'val_id': val_id, **cls.creds(), 'format': 'json'})

    @classmethod
    def refund(cls, *, bank_tran_id, amount_bdt, remarks, refund_trans_id):
        body = _request('GET', '/validator/api/merchantTransIDvalidationAPI.php', params={
            **cls.creds(), 'format': 'json', 'bank_tran_id': bank_tran_id, 'refund_amount': bdt(amount_bdt),
            'refund_remarks': remarks or 'Refund', 'refund_trans_id': refund_trans_id,
        })
        if body.get('APIConnect') != 'DONE':
            raise PaySimError(body.get('errorReason') or 'Refund request was rejected.')
        ok = body.get('status') in ('success', 'processing')
        return {'ok': ok, 'refund_id': body.get('refund_ref_id', ''), 'error': '' if ok else body.get('errorReason') or 'Refund declined'}


# --------------------------------------------------------------------------- bKash

_bkash_token = {'value': '', 'expires': 0.0}
_bkash_lock = threading.Lock()


class BKash:
    name = 'bkash'

    @staticmethod
    def _grant():
        body = _request('POST', '/v2/tokenized-checkout/auth/grant-token',
                        json_body={'app_key': setting('PAYSIM_BKASH_APP_KEY'), 'app_secret': setting('PAYSIM_BKASH_APP_SECRET')},
                        headers={'username': setting('PAYSIM_BKASH_USERNAME'), 'password': setting('PAYSIM_BKASH_PASSWORD')})
        if not body.get('id_token'):
            raise PaySimError(body.get('statusMessage') or 'bKash authorisation failed.')
        return body['id_token'], int(body.get('expires_in') or 3600)

    @classmethod
    def _headers(cls, fresh=False):
        with _bkash_lock:
            if fresh or not _bkash_token['value'] or _bkash_token['expires'] < time.time() + 60:
                token, ttl = cls._grant()
                _bkash_token.update(value=token, expires=time.time() + ttl)
            return {'Authorization': _bkash_token['value'], 'X-APP-Key': setting('PAYSIM_BKASH_APP_KEY')}

    @classmethod
    def _call(cls, path, body, extra_headers=None):
        res = _request('POST', path, json_body=body, headers={**cls._headers(), **(extra_headers or {})})
        if res.get('statusCode') in ('9999',) or res.get('externalCode') == '2001' or 'Invalid or expired authorization' in str(res):
            res = _request('POST', path, json_body=body, headers={**cls._headers(fresh=True), **(extra_headers or {})})
        return res

    @classmethod
    def create(cls, *, reference, amount_bdt, payer_reference, callback_url):
        body = cls._call('/v2/tokenized-checkout/payment/create', {
            'payerReference': payer_reference or '01700000000', 'callbackURL': callback_url,
            'amount': bdt(amount_bdt), 'currency': 'BDT', 'intent': 'sale', 'merchantInvoiceNumber': reference,
        })
        if not body.get('paymentId') or not body.get('bkashURL'):
            raise PaySimError(body.get('errorMessageEn') or 'Could not start the bKash payment.')
        return {'redirect_url': body['bkashURL'], 'session_id': body['paymentId']}

    @classmethod
    def execute(cls, payment_id):
        return cls._call('/v2/tokenized-checkout/payment/execute', {'paymentId': payment_id})

    @classmethod
    def query(cls, payment_id):
        return cls._call('/v2/tokenized-checkout/query/payment', {'paymentId': payment_id})

    @classmethod
    def refund(cls, *, payment_id, trx_id, amount_bdt, reason, idempotency_key):
        body = cls._call('/v2/tokenized-checkout/refund/payment/transaction', {
            'paymentId': payment_id, 'trxId': trx_id, 'refundAmount': bdt(amount_bdt),
            'sku': 'quickfood-order', 'reason': (reason or 'Refund')[:255],
        }, {'Idempotency-Key': idempotency_key})
        if body.get('refundTransactionStatus') == 'Completed':
            return {'ok': True, 'refund_id': body.get('refundTrxId', ''), 'error': ''}
        if body.get('externalCode') == '2003':
            return {'ok': False, 'refund_id': '', 'error': 'Refund declined by bKash.'}
        raise PaySimError(body.get('errorMessageEn') or 'Refund request was rejected.')


# --------------------------------------------------------------------------- Nagad

def _load_key(pem_text, private):
    from cryptography.hazmat.primitives import serialization
    data = pem_text.replace('\\n', '\n').encode()
    if private:
        return serialization.load_pem_private_key(data, password=None)
    return serialization.load_pem_public_key(data)


class Nagad:
    name = 'nagad'
    BASE = '/remote-payment-gateway-1.0/api/dfs'

    @staticmethod
    def merchant_id():
        return setting('PAYSIM_NAGAD_MERCHANT_ID')

    @staticmethod
    def _gateway_public_key():
        pem = setting('PAYSIM_NAGAD_PUBLIC_KEY') or (Path(__file__).with_name('paysim-nagad-public.pem')).read_text()
        return _load_key(pem, private=False)

    @staticmethod
    def _merchant_private_key():
        pem = setting('PAYSIM_NAGAD_MERCHANT_PRIVATE_KEY')
        if not pem:
            raise PaySimError('Nagad is not configured.')
        return _load_key(pem, private=True)

    @classmethod
    def seal(cls, payload):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        plaintext = json.dumps(payload, separators=(',', ':')).encode()
        sensitive = cls._gateway_public_key().encrypt(plaintext, padding.PKCS1v15())
        signature = cls._merchant_private_key().sign(plaintext, padding.PKCS1v15(), hashes.SHA256())
        return {
            'dateTime': datetime.now(dt_timezone.utc).strftime('%Y%m%d%H%M%S'),
            'sensitiveData': base64.b64encode(sensitive).decode(),
            'signature': base64.b64encode(signature).decode(),
        }

    @classmethod
    def open(cls, body):
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        try:
            plaintext = cls._merchant_private_key().decrypt(base64.b64decode(body['sensitiveData']), padding.PKCS1v15())
            cls._gateway_public_key().verify(base64.b64decode(body['signature']), plaintext, padding.PKCS1v15(), hashes.SHA256())
        except (KeyError, ValueError, InvalidSignature) as exc:
            raise PaySimError('Could not verify the Nagad response.') from exc
        return json.loads(plaintext)

    @classmethod
    def start(cls, *, reference, amount_bdt, callback_url, order_id):
        mid = cls.merchant_id()
        challenge = base64.b16encode(os.urandom(20)).decode().lower()
        init = _request('POST', f'{cls.BASE}/check-out/initialize/{mid}/{reference}',
                        json_body=cls.seal({'merchantId': mid, 'orderId': reference, 'challenge': challenge}))
        opened = cls.open(init)
        ref = opened.get('paymentReferenceId')
        if not ref:
            raise PaySimError('Could not start the Nagad payment.')
        done = _request('POST', f'{cls.BASE}/check-out/complete/{ref}', json_body={
            **cls.seal({'merchantId': mid, 'orderId': reference, 'amount': bdt(amount_bdt), 'challenge': opened.get('challenge', '')}),
            'merchantCallbackURL': callback_url,
            'additionalMerchantInfo': {'orderId': str(order_id), 'shop': 'QuickFood'},
        })
        if done.get('status') != 'Success' or not done.get('callBackUrl'):
            raise PaySimError(done.get('message') or 'Could not start the Nagad payment.')
        return {'redirect_url': done['callBackUrl'], 'session_id': ref}

    @classmethod
    def verify(cls, payment_ref_id):
        return _request('GET', f'{cls.BASE}/verify/payment/{urllib.parse.quote(payment_ref_id)}')

    @classmethod
    def refund(cls, *, payment_ref_id, original_amount_bdt, amount_bdt, reason, reference_no):
        mid = cls.merchant_id()
        body = _request('POST', f'{cls.BASE}/check-out/refund/{mid}', json_body=cls.seal({
            'merchantId': mid, 'paymentRefId': payment_ref_id,
            'originalRequestDate': datetime.now(dt_timezone.utc).strftime('%Y%m%d'),
            'originalAmount': bdt(original_amount_bdt), 'cancelAmount': bdt(amount_bdt),
            'referenceNo': reference_no, 'referenceMessage': (reason or 'Refund')[:120],
        }))
        opened = cls.open(body)
        ok = opened.get('status') == 'Success'
        return {'ok': ok, 'refund_id': opened.get('cancelTrxId', ''), 'error': '' if ok else opened.get('message') or 'Refund declined'}


# --------------------------------------------------------------------------- Rocket (DBBL)
# DBBL's e-commerce SOAP service (WSDL: https://ecomtest.dutchbanglabank.com/ecomws/dbblecomtxn?wsdl):
# getransid opens a session, the customer pays on /ecomm2/ClientHandler and comes back to the
# store's return_url with ?trans_id=, then getresultrocket says how it ended. DBBL has no refund
# API, so refunds go through the PaySim dashboard API (in production: the DBBL merchant panel).

def _xml_escape(value):
    return (str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


class Rocket:
    name = 'rocket'
    CARD_TYPE = '6'

    @staticmethod
    def creds():
        return setting('PAYSIM_ROCKET_USERID'), setting('PAYSIM_ROCKET_PWD')

    @classmethod
    def _soap(cls, operation, fields):
        userid, pwd = cls.creds()
        body = ''.join(f'<{k}>{_xml_escape(v)}</{k}>' for k, v in {'userid': userid, 'pwd': pwd, **fields}.items())
        envelope = ('<?xml version="1.0" encoding="UTF-8"?><soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
                    f'xmlns:ws="http://ecom.dbbl/"><soapenv:Body><ws:{operation}>{body}</ws:{operation}></soapenv:Body></soapenv:Envelope>')
        req = urllib.request.Request(base_url() + '/ecomws/dbblecomtxn', data=envelope.encode(), method='POST',
                                     headers={'Content-Type': 'text/xml; charset=utf-8', 'SOAPAction': ''})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
                raw = res.read().decode()
        except urllib.error.HTTPError as exc:
            raise PaySimError(f'Rocket answered HTTP {exc.code}') from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise PaySimError('Payment gateway is unreachable right now. Please try again.') from exc
        start, end = raw.find('<return>'), raw.find('</return>')
        if start < 0 or end < 0:
            raise PaySimError('Unexpected Rocket response.')
        text = raw[start + 8:end].replace('&gt;', '>').replace('&lt;', '<').replace('&amp;', '&')
        if text.lower().startswith('error'):
            raise PaySimError(text[:200])
        lines = {}
        for line in text.splitlines():
            key, sep, value = line.partition(':')
            if sep:
                lines[key.strip()] = value.strip()
        return lines

    @classmethod
    def start(cls, *, reference, amount_bdt, client_ip=''):
        paisa = int((Decimal(amount_bdt) * 100).quantize(Decimal('1')))
        data = cls._soap('getransid', {'amount': paisa, 'cardtype': cls.CARD_TYPE, 'txnrefnum': reference,
                                       'clientip': client_ip or '127.0.0.1'})
        trans_id = data.get('TRANSACTION_ID')
        if not trans_id:
            raise PaySimError('Could not start the Rocket payment.')
        url = f'{base_url()}/ecomm2/ClientHandler?' + urllib.parse.urlencode({'card_type': cls.CARD_TYPE, 'trans_id': trans_id})
        return {'redirect_url': url, 'session_id': trans_id}

    @classmethod
    def result(cls, trans_id):
        return cls._soap('getresultrocket', {'transid': trans_id, 'clientip': ''})

    @classmethod
    def refund(cls, *, reference, amount_bdt, reason, idempotency_key):
        txns = _request('GET', '/api/txns', params={'gateway': 'rocket', 'limit': 200})
        txn = next((t for t in txns if t.get('gatewayTxnId') == reference), None)
        if not txn:
            raise PaySimError('Rocket transaction not found in PaySim.')
        try:
            body = _request('POST', f"/api/txns/{txn['id']}/refunds", json_body={
                'amountMinor': int((Decimal(amount_bdt) * 100).quantize(Decimal('1'))),
                'reason': (reason or 'Refund')[:200], 'idempotencyKey': idempotency_key[:64]})
        except PaySimError as exc:
            return {'ok': False, 'refund_id': '', 'error': str(exc)}
        refund = body.get('refund') or {}
        ok = refund.get('status') == 'completed'
        return {'ok': ok, 'refund_id': refund.get('refundId', ''), 'error': '' if ok else 'Refund declined'}


# --------------------------------------------------------------------------- Upay
# Upay PGW API v4.1.0 as described by community docs (https://bdpaygate.github.io/docs/upay):
# merchant-auth -> merchant-payment-init -> hosted page -> redirect_url?status=&invoice_id=,
# confirmed with single-payment-status; refunds via the bulk refund endpoint.

class Upay:
    name = 'upay'
    _token = {'value': '', 'expires': 0.0}
    _lock = threading.Lock()

    @classmethod
    def _auth(cls, fresh=False):
        with cls._lock:
            if not fresh and cls._token['value'] and cls._token['expires'] > time.time():
                return cls._token['value']
            data = _request('POST', '/payment/merchant-auth/', json_body={
                'merchant_id': setting('PAYSIM_UPAY_MERCHANT_ID'), 'merchant_key': setting('PAYSIM_UPAY_MERCHANT_KEY')})
            token = (data.get('data') or {}).get('token')
            if not token:
                raise PaySimError(data.get('message') or 'Upay login failed.')
            cls._token.update(value=token, expires=time.time() + 600)
            return token

    @classmethod
    def _call(cls, method, path, body=None):
        try:
            return _request(method, path, json_body=body, headers={'Authorization': f'UPAY {cls._auth()}'})
        except PaySimError as exc:
            if (getattr(exc, 'body', {}) or {}).get('code') != 'UNA4001':
                raise
            return _request(method, path, json_body=body, headers={'Authorization': f'UPAY {cls._auth(fresh=True)}'})

    @classmethod
    def start(cls, *, reference, amount_bdt, redirect_url):
        data = cls._call('POST', '/payment/merchant-payment-init/', {
            'date': datetime.now(dt_timezone.utc).strftime('%Y-%m-%d'), 'txn_id': reference, 'invoice_id': reference,
            'amount': bdt(amount_bdt), 'redirect_url': redirect_url, 'merchant_id': setting('PAYSIM_UPAY_MERCHANT_ID'),
            'merchant_name': 'QuickFood',
        })
        info = data.get('data') or {}
        if data.get('code') != 'MPIS2002' or not info.get('gateway_url'):
            raise PaySimError(data.get('message') or 'Could not start the Upay payment.')
        return {'redirect_url': info['gateway_url'], 'session_id': info.get('session_id', '')}

    @classmethod
    def status(cls, reference):
        return (cls._call('GET', f'/payment/single-payment-status/{urllib.parse.quote(reference)}/').get('data') or {})

    @classmethod
    def refund(cls, *, reference, amount_bdt):
        data = cls._call('POST', '/payment/bulk/refund/', [{'txn_id': reference, 'refund_amount': bdt(amount_bdt)}])
        info = data.get('data') or {}
        if reference in (info.get('success_list') or []):
            return {'ok': True, 'refund_id': f'UPR-{reference}-{int(time.time() * 1000)}', 'error': ''}
        failed = (info.get('failed_list') or {}).get(reference) or {}
        return {'ok': False, 'refund_id': '', 'error': (failed.get('en') if isinstance(failed, dict) else str(failed)) or data.get('message') or 'Refund declined'}


GATEWAYS = {'sslcommerz': SSLCommerz, 'bkash': BKash, 'nagad': Nagad, 'rocket': Rocket, 'upay': Upay}


def configured(gateway):
    if gateway == 'sslcommerz':
        return bool(setting('PAYSIM_SSLCOMMERZ_STORE_ID') and setting('PAYSIM_SSLCOMMERZ_STORE_PASSWD'))
    if gateway == 'bkash':
        return all(setting(k) for k in ('PAYSIM_BKASH_APP_KEY', 'PAYSIM_BKASH_APP_SECRET', 'PAYSIM_BKASH_USERNAME', 'PAYSIM_BKASH_PASSWORD'))
    if gateway == 'nagad':
        return bool(setting('PAYSIM_NAGAD_MERCHANT_ID') and setting('PAYSIM_NAGAD_MERCHANT_PRIVATE_KEY'))
    if gateway == 'rocket':
        return bool(setting('PAYSIM_ROCKET_USERID') and setting('PAYSIM_ROCKET_PWD'))
    if gateway == 'upay':
        return bool(setting('PAYSIM_UPAY_MERCHANT_ID') and setting('PAYSIM_UPAY_MERCHANT_KEY'))
    return False
