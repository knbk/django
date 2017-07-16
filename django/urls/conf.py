from importlib import import_module
from types import ModuleType

from django.core.exceptions import ImproperlyConfigured
from django.urls.patterns import RegexPattern, RoutePattern
from django.urls.resolvers import View, Namespace
from django.views import defaults


handler400 = defaults.bad_request
handler403 = defaults.permission_denied
handler404 = defaults.page_not_found
handler500 = defaults.server_error


def include(arg, namespace=None):
    app_name = None
    if isinstance(arg, tuple):
        # callable returning a namespace hint
        try:
            urlconf, app_name = arg
        except ValueError:
            raise ImproperlyConfigured(
                'Passing a %d-tuple to django.conf.urls.include() is not supported. '
                'Pass a 2-tuple containing the list of patterns and app_name, '
                'and provide the namespace argument to include() instead.' % len(arg)
            )
    else:
        # No namespace hint - use manually provided namespace
        urlconf = arg

    if isinstance(urlconf, str):
        urlconf_module = import_module(urlconf)
    else:
        urlconf_module = urlconf

    app_name = app_name or getattr(urlconf_module, 'app_name', None)
    if namespace and not app_name:
        raise ImproperlyConfigured(
            'Specifying a namespace in django.conf.urls.include() without '
            'providing an app_name is not supported. Set the app_name attribute '
            'in the included module, or pass a 2-tuple containing the list of '
            'patterns and app_name instead.',
        )

    namespace = namespace or app_name

    return URLConf(urlconf_module, app_name, namespace)


def _url(pattern, view, kwargs=None, name=None):
    if callable(view):
        return Endpoint(pattern, view, kwargs, name)
    elif isinstance(view, URLConf):
        return Include(pattern, view, kwargs)
    else:
        raise TypeError('view must be a callable or an URLConf instance in the case of include().')


def url(regex, view, kwargs=None, name=None):
    return _url(RegexPattern(regex), view, kwargs, name)


def path(route, view, kwargs=None, name=None):
    return _url(RoutePattern(route), view, kwargs, name)


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

    def build_patterns(self):
        for pattern in self.urlpatterns:
            yield from pattern.build_patterns()

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
    def __init__(self, pattern, view, kwargs=None, name=None):
        self.pattern = pattern
        self.view = view
        self.kwargs = {}
        if kwargs is not None:
            self.kwargs.update(kwargs)
        self.name = name

    def __repr__(self):
        return "<Endpoint '%s'%s>" % (self.lookup_str, " [name='%s']" % self.name if self.name else '')

    @property
    def lookup_str(self):
        from django.urls.utils import get_lookup_string
        return get_lookup_string(self.view)

    def build_patterns(self):
        yield View(self.pattern, self.view, self.name, self.kwargs.copy())


class Include:
    def __init__(self, pattern, urlconf, kwargs=None):
        self.pattern = pattern
        self.urlconf = urlconf
        self.kwargs = {}
        if kwargs is not None:
            self.kwargs.update(kwargs)

    def __repr__(self):
        return "<Include '%r'>" % self.urlconf

    def build_patterns(self):
        if self.urlconf.app_name is None:
            for endpoint in self.urlconf.build_patterns():
                yield endpoint.bind_to_pattern(self.pattern, self.kwargs)
        else:
            yield Namespace(
                self.pattern,
                self.urlconf,
                self.urlconf.app_name,
                self.urlconf.namespace,
                self.kwargs.copy(),
            )
