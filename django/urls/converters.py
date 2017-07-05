import uuid

from django.utils import lru_cache


class BaseConverter:
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


class UUIDConverter(BaseConverter):
    regex = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'

    def to_python(self, value):
        return uuid.UUID(value)

    def to_url(self, value):
        return str(value)


class SlugConverter(BaseConverter):

    # Django has both `slug_re` and `slug_unicode_re`. In the case of URLs, the
    # non-unicode variant seems to make more sense.
    regex = '[-a-zA-Z0-9_]+'

    def to_python(self, value):
        return value

    def to_url(self, value):
        return value


class PathConverter(BaseConverter):

    regex = '.+'

    def to_python(self, value):
        return value

    def to_url(self, value):
        return value


DEFAULT_CONVERTERS = {
    'int': IntConverter(),
    'path': PathConverter(),
    'slug': SlugConverter(),
    'string': StringConverter(),
    'uuid': UUIDConverter(),
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
