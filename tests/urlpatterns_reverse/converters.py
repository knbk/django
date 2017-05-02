import base64

from django.urls.converters import BaseConverter


class Base64Converter(BaseConverter):
    regex = r'[a-zA-Z0-9+/]*={0,2}'

    def to_python(self, value):
        return base64.b64decode(value)

    def to_url(self, value):
        # b64encode returns bytes, but we need to return a string.
        return base64.b64encode(value).decode('ascii')
