from django.urls.converters import BaseConverter


class Base64Converter(BaseConverter):
    regex = r'[a-zA-Z0-9+/]*={0,2}'

    def to_python(self, value):
        return value.decode('base64')

    def to_url(self, value):
        return value.encode('base64').replace('\n', '')
