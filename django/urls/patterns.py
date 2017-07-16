import re

from django.core.exceptions import ImproperlyConfigured
from django.urls.converters import get_converter


class RegexPattern:
    def __init__(self, regex):
        self._regex = regex

    def compile(self):
        return re.compile(str(self._regex))

    def get_converters(self):
        p = self.compile()
        args = p.groupindex.keys()
        return dict((arg, 'string') for arg in args)

    def to_python(self, kwargs):
        converters = self.get_converters()
        for key in kwargs:
            if key in converters:
                converter = get_converter(converters[key])
                kwargs[key] = converter.to_python(kwargs[key])
        return kwargs

    def to_url(self, kwargs):
        converters = self.get_converters()
        for key in kwargs:
            if key in converters:
                converter = get_converter(converters[key])
                kwargs[key] = converter.to_url(kwargs[key])
        return kwargs


_PATH_PARAMETER_COMPONENT_RE = re.compile(
    '<(?:(?P<converter>[^:]+):)?(?P<parameter>\w+)>'
)


def _route_to_regex(route):
    parts = ['^']
    converters = {}
    while True:
        match = _PATH_PARAMETER_COMPONENT_RE.search(route)
        if not match:
            parts.append(re.escape(route))
            break

        parts.append(re.escape(route[:match.start()]))
        route = route[match.end():]

        parameter = match.group('parameter')
        if not parameter.isidentifier():
            msg = "Parameter name {!r} is not a valid identifier.".format(
                parameter
            )
            raise ImproperlyConfigured(msg)
        raw_converter = match.group('converter')
        if raw_converter is None:
            # If no converter is specified, the default is ``string``.
            raw_converter = 'string'
        converter = get_converter(raw_converter)
        converters[parameter] = converter
        parts.append('(?P<' + parameter + '>' + converter.regex + ')')
    return ''.join(parts), converters


class RoutePattern:
    def __init__(self, route):
        self._route = route

    def compile(self):
        return re.compile(_route_to_regex(self._route)[0])

    def get_converters(self):
        return _route_to_regex(self._route)[1]

    def to_python(self, kwargs):
        converters = self.get_converters()
        for key in kwargs:
            if key in converters:
                converter = get_converter(converters[key])
                kwargs[key] = converter.to_python(kwargs[key])
        return kwargs

    def to_url(self, kwargs):
        converters = self.get_converters()
        for key in kwargs:
            if key in converters:
                converter = get_converter(converters[key])
                kwargs[key] = converter.to_url(kwargs[key])
        return kwargs


class CombinedPattern:
    def __init__(self, *patterns):
        self._patterns = self.flatten(*patterns)

    def append(self, *patterns):
        self._patterns += self.flatten(*patterns)

    def prepend(self, *patterns):
        self._patterns = self.flatten(*patterns) + self._patterns

    def flatten(self, *patterns):
        flattened = []
        for pattern in patterns:
            if isinstance(pattern, CombinedPattern):
                flattened.extend(pattern._patterns)
            else:
                flattened.append(pattern)
        return flattened

    def compile(self):
        parts = ['^']
        for pattern in self._patterns:
            p = pattern.compile().pattern
            if p.startswith('^'):
                p = p[1:]
            parts.append(p)
        return re.compile(''.join(parts))

    def get_converters(self):
        converters = {}
        for pattern in self._patterns:
            converters.update(pattern.get_converters())
        return converters

    def to_python(self, kwargs):
        converters = self.get_converters()
        for key in kwargs:
            if key in converters:
                converter = get_converter(converters[key])
                kwargs[key] = converter.to_python(kwargs[key])
        return kwargs

    def to_url(self, kwargs):
        converters = self.get_converters()
        for key in kwargs:
            if key in converters:
                converter = get_converter(converters[key])
                kwargs[key] = converter.to_url(kwargs[key])
        return kwargs
