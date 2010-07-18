"""
Amazon Product Advertising API

[Product Advertising API]
https://affiliate-program.amazon.com/gp/advertising/api/detail/main.html
[Product Advertising API Developer Guide]
http://docs.amazonwebservices.com/AWSECommerceService/latest/DG/
"""

import base64
import datetime
import hashlib
import hmac
import urllib
from xml.etree import ElementTree


class AWSError(Exception):

    def __init__(self, code, message):
        self.code = code
        self.message = message

    def __str__(self):
        return unicode(self).encode("utf-8")

    def __unicode__(self):
        return u"%s: %s" % (self.code, self.message)


class Client(object):
    REQUEST_URI = '/onca/xml'
    REQUEST_METHOD = 'GET'
    SERVICE = 'AWSECommerceService'

    # http://docs.amazonwebservices.com/AWSECommerceService/latest/DG/
    LOCALE_HOST = {
        'ca': 'ecs.amazonaws.ca',
        'de': 'ecs.amazonaws.de',
        'fr': 'ecs.amazonaws.fr',
        'jp': 'ecs.amazonaws.jp',
        'uk': 'ecs.amazonaws.co.uk',
        'us': 'ecs.amazonaws.com',
    }

    def __init__(self, access_key, secret_key, locale):
        if locale not in self.LOCALE_HOST:
            raise ValueError("locale not supported")
        self.access_key = access_key
        self.secret_key = secret_key
        self.host = self.LOCALE_HOST[locale]

    def sign(self, params):
        params['AWSAccessKeyId'] = self.access_key
        params['Timestamp'] = self.timestamp()
        canonical_qs = self.canonicalize(params)
        to_sign = self.REQUEST_METHOD + "\n" \
                + self.host + "\n" \
                + self.REQUEST_URI + "\n" \
                + canonical_qs
        signature = self.hmac(to_sign)
        sig = self.urlencode_rfc3986({"Signature": signature})
        endpoint = "http://" + self.host + self.REQUEST_URI
        url = endpoint + "?" + canonical_qs + "&" + sig
        return url

    def hmac(self, to_sign):
        mac = hmac.new(self.secret_key, to_sign, hashlib.sha256)
        raw_hmac = mac.digest()
        signature = base64.b64encode(raw_hmac)
        return signature

    def timestamp(self):
        return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

    def canonicalize(self, params):
        return self.urlencode_rfc3986(
                [(key, params[key]) for key in sorted(params)])

    def urlencode_rfc3986(self, params):
        out = urllib.urlencode(params)
        out = out.replace("+", "%20")
        out = out.replace("*", "%2A")
        out = out.replace("%7E", "~")
        return out

    def request(self, params):
        params['Service'] = self.SERVICE
        url = self.sign(params)
        xml = urllib.urlopen(url).read()
        # XXX: ignore namespace for convenience
        xml = xml.replace("xmlns", "xmlns:ignore")
        root = ElementTree.fromstring(xml)
        for error_path in ['OperationRequest/Errors/Error',
                           'Items/Request/Errors/Error']:
            error = root.find(error_path)
            if error is not None:
                code = error.findtext("Code")
                message = error.findtext("Message")
                raise AWSError(code, message)
        return root
