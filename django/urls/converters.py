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


REGISTERED_CONVERTERS = {}


def register_converter(converter, typename):
    REGISTERED_CONVERTERS[typename] = converter()
    get_converters.cache_clear()


@lru_cache.lru_cache(maxsize=None)
def get_converters():
    converters = {}
    converters.update(DEFAULT_CONVERTERS)
    converters.update(REGISTERED_CONVERTERS)

    return converters


def get_converter(raw_converter):
    return get_converters()[raw_converter]
