from importlib import import_module
from types import ModuleType

from django.core.exceptions import ImproperlyConfigured
from django.urls.resolvers import LocalePrefixPattern


class URLConf:
    def __init__(self, urlconf, app_name=None, namespace=None):
        self.urlconf_name = urlconf
        self.app_name = app_name
        self.namespace = namespace

    def __repr__(self):
        urlconf_name = self.urlconf_name
        if isinstance(urlconf_name, (list, tuple)) and len(urlconf_name):
            urlconf_repr = '<%s list>' % self.urlpatterns[0].__class__.__name__
        elif isinstance(urlconf_name, ModuleType):
            urlconf_repr = repr(urlconf_name.__name__)
        else:
            urlconf_repr = repr(urlconf_name)
        return '<URLConf %s (%s)>' % (urlconf_repr, self.app_name)

    def __iter__(self):
        yield from self.urlpatterns

    def __getitem__(self, index):
        return self.urlpatterns[index]

    @property
    def urlconf_module(self):
        if isinstance(self.urlconf_name, str):
            return import_module(self.urlconf_name)
        else:
            return self.urlconf_name

    @property
    def urlpatterns(self):
        urlpatterns = getattr(self.urlconf_module, 'urlpatterns', self.urlconf_module)
        try:
            iter(urlpatterns)
        except TypeError:
            msg = (
                "The included URLconf '{name}' does not appear to have any "
                "patterns in it. If you see valid patterns in the file then "
                "the issue is probably caused by a circular import."
            )
            raise ImproperlyConfigured(msg.format(name=self.urlconf_name))
        return urlpatterns


class Endpoint:
    def __init__(self, pattern, view, kwargs=None, name=None, converters=None):
        self.pattern = pattern
        self.view = view
        self.kwargs = kwargs or {}
        self.name = name
        self.converters = converters or {}

    def __repr__(self):
        return "<Endpoint '%s'%s>" % (self.lookup_str, " [name='%s']" % self.name if self.name else '')

    @property
    def lookup_str(self):
        from django.urls.utils import get_lookup_string
        return get_lookup_string(self.view)


class Include:
    def __init__(self, pattern, urlconf, kwargs=None, converters=None):
        self.pattern = pattern
        self.urlconf = urlconf
        self.kwargs = kwargs or {}
        self.converters = converters or {}

    def __repr__(self):
        return "<Include '%r'>" % self.urlconf


def include(arg, namespace=None):
    app_name = None
    if isinstance(arg, tuple):
        # callable returning a namespace hint
        try:
            urlconf_module, app_name = arg
        except ValueError:
            if namespace:
                raise ImproperlyConfigured(
                    'Cannot override the namespace for a dynamic module that '
                    'provides a namespace.'
                )
            raise ImproperlyConfigured(
                'Passing a %d-tuple to django.conf.urls.include() is not supported. '
                'Pass a 2-tuple containing the list of patterns and app_name, '
                'and provide the namespace argument to include() instead.' % len(arg)
            )
    else:
        # No namespace hint - use manually provided namespace
        urlconf_module = arg

    if isinstance(urlconf_module, str):
        urlconf_module = import_module(urlconf_module)
    patterns = getattr(urlconf_module, 'urlpatterns', urlconf_module)
    app_name = getattr(urlconf_module, 'app_name', app_name)
    if namespace and not app_name:
        raise ImproperlyConfigured(
            'Specifying a namespace in django.conf.urls.include() without '
            'providing an app_name is not supported. Set the app_name attribute '
            'in the included module, or pass a 2-tuple containing the list of '
            'patterns and app_name instead.',
        )

    namespace = namespace or app_name

    # Make sure we can iterate through the patterns (without this, some
    # testcases will break).
    if isinstance(patterns, (list, tuple)):
        for url_pattern in patterns:
            # Test if the LocaleRegexURLResolver is used within the include;
            # this should throw an error since this is not allowed!
            if isinstance(url_pattern.pattern, LocalePrefixPattern):
                raise ImproperlyConfigured(
                    'Using i18n_patterns in an included URLconf is not allowed.')

    return URLConf(urlconf_module, app_name, namespace)
