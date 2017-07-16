from functools import lru_cache

from django.urls.resolvers import Namespace
from django.urls.utils import get_callable
from django.urls.exceptions import Resolver404, NoReverseMatch
from django.urls.patterns import RegexPattern
from django.utils.functional import cached_property


class Dispatcher:
    def __init__(self, urlconf):
        from django.urls.conf import URLConf
        self.urlconf = URLConf(urlconf)
        self.root_namespace = Namespace(RegexPattern('^/'), self.urlconf, None, None, {})

    @cached_property
    def urlpatterns(self):
        root_pattern = RegexPattern('^/')
        return [pattern.bind_to_pattern(root_pattern) for pattern in self.urlconf.build_patterns()]

    def resolve(self, path):
        for match in self.root_namespace.resolve(str(path)):
            return match
        raise Resolver404

    def reverse(self, view, args=None, kwargs=None, current_app=None):
        args = args or ()
        kwargs = kwargs or {}
        if isinstance(view, str):
            view = view.split(':')
        else:
            view = [view]

        try:
            endpoints = self.root_namespace.reverse_lookup(view, current_app)
        except NoReverseMatch as e:
            if e.args[0]['resolved_path']:
                raise NoReverseMatch(
                    "%s is not a registered namespace inside '%s'" %
                    (e.args[0]['key'], ':'.join(e.args[0]['resolved_path']))
                )
            else:
                raise NoReverseMatch("%s is not a registered namespace" % e.args[0]['key'])

        patterns = []
        for endpoint in endpoints:
            try:
                return endpoint.reverse(*args, **kwargs)
            except NoReverseMatch as e:
                patterns.extend(e.args[0]['tried'])

        # lookup_view can be URL name or callable, but callables are not
        # friendly in error messages.
        m = getattr(view[-1], '__module__', None)
        n = getattr(view[-1], '__name__', None)
        if m is not None and n is not None:
            lookup_view_s = "%s.%s" % (m, n)
        else:
            lookup_view_s = view[-1]

        if patterns:
            if args:
                arg_msg = "arguments '%s'" % (args,)
            elif kwargs:
                arg_msg = "keyword arguments '%s'" % (kwargs,)
            else:
                arg_msg = "no arguments"
            msg = (
                "Reverse for '%s' with %s not found. %d pattern(s) tried: %s" %
                (lookup_view_s, arg_msg, len(patterns), patterns)
            )
        else:
            msg = (
                "Reverse for '%(view)s' not found. '%(view)s' is not "
                "a valid view function or pattern name." % {'view': lookup_view_s}
            )
        raise NoReverseMatch(msg)

    def resolve_error_handler(self, view_type):
        from django.urls import conf
        callback = getattr(self.urlconf.urlconf_module, 'handler%s' % view_type, None)
        if not callback:
            # No handler specified in file; use default
            callback = getattr(conf, 'handler%s' % view_type)
        return get_callable(callback), {}


@lru_cache(maxsize=None)
def get_resolver(urlconf):
    if urlconf is None:
        from django.conf import settings
        urlconf = settings.ROOT_URLCONF
    return Dispatcher(urlconf)
