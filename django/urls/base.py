from __future__ import unicode_literals

from collections import defaultdict
from importlib import import_module
from threading import local, Lock

from django.template.context import BaseContext
from django.urls.exceptions import NoReverseMatch
from django.utils import lru_cache, six
from django.utils.datastructures import MultiValueDict
from django.utils.encoding import force_text
from django.utils.functional import lazy
from django.utils.http import RFC3986_SUBDELIMS, urlquote
from .constraints import RegexPattern
from .resolvers import Resolver
from .utils import URL

# SCRIPT_NAME prefixes for each thread are stored here. If there's no entry for
# the current thread (which is the only one we ever access), it is assumed to
# be empty.
_prefixes = local()

# Overridden URLconfs for each thread are stored here.
_urlconfs = local()


@lru_cache.lru_cache(maxsize=None)
def get_resolver(urlconf=None):
    if urlconf is None:
        from django.conf import settings
        urlconf = settings.ROOT_URLCONF
    return Dispatcher(urlconf)


class Dispatcher(object):
    def __init__(self, urlconf):
        self.ready = False
        self.urlconf = urlconf
        self.resolver = Resolver(urlconf, constraints=[RegexPattern('^/')])

        self._namespaces = {}
        self.reverse_dict = MultiValueDict()
        self.app_dict = defaultdict(list)

        self.load_root()

    def load_root(self):
        for name, resolver, constraints, kwargs, decorators in self.resolver.flatten():
            constraints = self.resolver.constraints + constraints
            if name is None:  # A view endpoint
                self.reverse_dict.appendlist((resolver.func,), (constraints, kwargs, decorators))
                if resolver.url_name is not None:
                    self.reverse_dict.appendlist((resolver.url_name,), (constraints, kwargs, decorators))
            else:  # a subnamespace
                self.app_dict[(resolver.app_name,)].append(name)
                self._namespaces[(name,)] = (resolver.app_name,), resolver, constraints, kwargs, decorators
        self.ready = True

    def load_namespace(self, namespace_root):
        namespace_root = tuple(namespace_root)
        root = self._namespaces.pop(namespace_root)
        for name, resolver, constraints, kwargs, decorators in root[1].flatten():
            constraints = root[2] + constraints
            kw = root[3].copy()
            kw.update(kwargs)
            kwargs = kw
            decorators = root[4] + decorators
            if name is None:
                self.reverse_dict.appendlist(namespace_root + (resolver.func,), (constraints, kwargs, decorators))
                if resolver.url_name is not None:
                    self.reverse_dict.appendlist(namespace_root + (resolver.url_name,), (constraints, kwargs, decorators))
            else:
                app_root = root[0] + (resolver.app_name,)
                self.app_dict[app_root + (resolver.app_name,)].append(name)
                self._namespaces[namespace_root + (name,)] = app_root, resolver, constraints, kwargs, decorators

    def resolve(self, path, request=None):
        return self.resolver.resolve(path, request)

    def reverse(self, viewname, *args, **kwargs):
        if isinstance(viewname, (list, tuple)):
            lookup = tuple(viewname)
        elif isinstance(viewname, six.string_types):
            lookup = tuple(viewname.split(':'))
        else:
            lookup = (viewname,)
        for i in range(len(lookup) - 1):
            if lookup[:i+1] in self._namespaces:
                self.load_namespace(lookup[:i+1])

        prefix = get_script_prefix()[:-1]

        patterns = []
        for constraints, default_kwargs, decorators in self.reverse_dict.getlist(lookup):
            url = URL()
            new_args, new_kwargs = args, kwargs
            try:
                for constraint in constraints:
                    url, new_args, new_kwargs = constraint.construct(url, *new_args, **new_kwargs)
                if new_kwargs:
                    if any(name not in default_kwargs for name in new_kwargs):
                        raise NoReverseMatch()
                    for k, v in default_kwargs.items():
                        if kwargs.get(k, v) != v:
                            raise NoReverseMatch()
                if new_args:
                    raise NoReverseMatch()
            except NoReverseMatch:
                # We don't need the leading slash of the root pattern here
                patterns.append(constraints[1:])
            else:
                url.path = urlquote(prefix + force_text(url.path), safe=RFC3986_SUBDELIMS + str('/~:@'))
                if url.path.startswith('//'):
                    url.path = '/%%2F%s' % url.path[2:]
                return force_text(url)

        if isinstance(lookup[-1], six.string_types):
            viewname = ':'.join(lookup)

        raise NoReverseMatch(
            "Reverse for '%s' with arguments '%s' and keyword "
            "arguments '%s' not found. %d pattern(s) tried: %s" %
            (
                viewname, args, kwargs, len(patterns),
                [str('').join(c.describe() for c in constraints) for constraints in patterns],
            )
        )

    def resolve_namespace(self, lookup, current_app=None):
        return self.resolver.resolve_namespace(lookup, current_app)

    @property
    def urlconf_module(self):
        return self.resolver.urlconf_module


# PUSH IT DOWN A NOTCH
# LAZILY LOAD NAMESPACES, NOT VIEWS
# urls.py:
# app_name = "polls"
# decorators = [login_required()]
# kwargs = {"something": "yes"}
# urlpatterns = [
#     url(r'comments/', include(comments_urls)),
#     url(r'', poll_archive, name='archive'),
# ]
#
# views.py:
# @viewspec('name', kwargs={})
# def my_view(request):
#     return HttpResponse()
#
# or
#
# def my_view(request):
#     return HttpResponse()
#
# view1 = viewspec('name', my_view, kwargs={'arg': 'yes'})
# view2 = viewspec('name2', my_view, kwargs={'arg': 'no'})

def resolve(path, urlconf=None, request=None):
    path = force_text(path)
    if urlconf is None:
        urlconf = get_urlconf()
    return get_resolver(urlconf).resolve(path, request)


def reverse(viewname, urlconf=None, args=None, kwargs=None, current_app=None):
    if urlconf is None:
        urlconf = get_urlconf()

    resolver = get_resolver(urlconf)
    # TODO: raise nice exception for circular imports caused by reverse()
    if not resolver.ready:
        raise Exception("Can't reverse urls when the resolver hasn't been loaded. Use reverse_lazy() instead.")
    args = args or ()
    text_args = [force_text(x) for x in args]
    kwargs = kwargs or {}
    text_kwargs = {k: force_text(v) for k, v in kwargs.items()}

    if isinstance(viewname, six.string_types):
        lookup = viewname.split(':')
    elif viewname:
        lookup = [viewname]
    else:
        raise NoReverseMatch()

    current_app = current_app.split(':') if current_app else []

    lookup = resolver.resolve_namespace(lookup, current_app)

    return resolver.reverse(lookup, *text_args, **text_kwargs)


reverse_lazy = lazy(reverse, URL, six.text_type)


def set_script_prefix(prefix):
    """
    Sets the script prefix for the current thread.
    """
    if not prefix.endswith('/'):
        prefix += '/'
    _prefixes.value = prefix


def get_script_prefix():
    """
    Returns the currently active script prefix. Useful for client code that
    wishes to construct their own URLs manually (although accessing the request
    instance is normally going to be a lot cleaner).
    """
    return getattr(_prefixes, "value", '/')


def clear_script_prefix():
    """
    Unsets the script prefix for the current thread.
    """
    try:
        del _prefixes.value
    except AttributeError:
        pass


def set_urlconf(urlconf_name):
    """
    Sets the URLconf for the current thread (overriding the default one in
    settings). Set to None to revert back to the default.
    """
    if urlconf_name:
        _urlconfs.value = urlconf_name
    else:
        if hasattr(_urlconfs, "value"):
            del _urlconfs.value


def get_urlconf(default=None):
    """
    Returns the root URLconf to use for the current thread if it has been
    changed from the default one.
    """
    return getattr(_urlconfs, "value", default)
