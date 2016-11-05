from django.conf import settings
from django.utils import lru_cache


class BaseConverter(object):
    pass


class IntConverter(BaseConverter):
    regex = '[0-9]+'

    def to_python(self, value):
        return int(value)

    def to_url(self, value):
        return str(value)


class StringConverter(BaseConverter):
    regex = '[^/]+'

    def to_python(self, value):
        return value

    def to_url(self, value):
        return value

DEFAULT_CONVERTERS = {
    'int': IntConverter(),
    'string': StringConverter(),
}


@lru_cache.lru_cache(maxsize=None)
def get_converters():
    converters = DEFAULT_CONVERTERS.copy()
    try:
        converters.update(settings.CUSTOM_URL_CONVERTERS)
    except AttributeError:
        # No custom converters configured. Not a problem.
        pass
    return converters


@lru_cache.lru_cache(maxsize=None)
def get_converter(raw_converter):
    return get_converters()[raw_converter]
