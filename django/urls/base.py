from __future__ import unicode_literals
from contextlib import contextmanager
from importlib import import_module
from itertools import zip_longest
from threading import local

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
    return Resolver(urlconf, constraints=[RegexPattern(r'^/')])


class Dispatcher(object):
    def __init__(self, urlconf_name):
        self.urlconf_name = urlconf_name
        if isinstance(urlconf_name, six.string_types):
            self.urlconf_module = import_module(urlconf_name)
        else:
            self.urlconf_module = urlconf_name
        self.resolver = Resolver(self.urlconf_module, None, [RegexPattern('^/')])
        self.reverse_dict = None

        self.build_tree()

    def build_tree(self):
        reverse_dict = MultiValueDict()
        app_names, namespaces, constraints, decorators, kwargs = [], [], [RegexPattern('^/')], [], BaseContext()
        kwargs.dicts[0] = {}

        def recurse_resolvers(resolvers):
            for name, resolver in resolvers:
                if resolver.app_name is not None:
                    app_names.append(resolver.app_name)
                    namespaces.append(name or resolver.app_name)
                constraints.extend(resolver.constraints)
                decorators.extend(resolver.decorators)
                kwargs.push(resolver.kwargs)

                try:
                    if hasattr(resolver, "resolvers"):
                        recurse_resolvers(resolver.resolvers)
                    else:
                        func_key = tuple(namespaces) + (resolver.func,)
                        value = (list(constraints), kwargs.flatten())
                        reverse_dict.appendlist(func_key, value)
                        if getattr(resolver, "url_name", None):
                            name_key = tuple(namespaces) + (resolver.url_name,)
                            reverse_dict.appendlist(name_key, value)
                finally:
                    if resolver.app_name is not None:
                        app_names.pop()
                        namespaces.pop()
                    # We need to modify these in place because of scope issues
                    [constraints.pop() for _ in resolver.constraints]
                    [decorators.pop() for _ in resolver.decorators]
                    kwargs.pop()

        recurse_resolvers(self.resolver.resolvers)

        self.reverse_dict = reverse_dict

    def resolve(self, path, request=None):
        return self.resolver.resolve(path, request)

    def resolve_namespace(self, lookup, current_app):
        return tuple(self.resolver.resolve_namespace(lookup, current_app))

    def search(self, lookup):
        if lookup in self.reverse_dict:
            for constraints, kwargs in self.reverse_dict.getlist(lookup):
                yield constraints, kwargs


def resolve(path, urlconf=None, request=None):
    path = force_text(path)
    if urlconf is None:
        urlconf = get_urlconf()
    return get_resolver(urlconf).resolve(path, request)


def reverse(viewname, urlconf=None, args=None, kwargs=None, current_app=None, strings_only=True):
    if urlconf is None:
        urlconf = get_urlconf()

    resolver = get_resolver(urlconf)
    args = args or ()
    text_args = [force_text(x) for x in args]
    kwargs = kwargs or {}
    text_kwargs = {k: force_text(v) for k, v in kwargs.items()}

    prefix = get_script_prefix()[:-1]  # Trailing slash is already there

    if isinstance(viewname, six.string_types):
        lookup = viewname.split(':')
    elif viewname:
        lookup = [viewname]
    else:
        raise NoReverseMatch()

    current_app = current_app.split(':') if current_app else []

    lookup = resolver.resolve_namespace(lookup, current_app)

    patterns = []
    for constraints, default_kwargs in resolver.search(lookup):
        url = URL()
        new_args, new_kwargs = text_args, text_kwargs
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
            return force_text(url) if strings_only else url

    raise NoReverseMatch(
        "Reverse for '%s' with arguments '%s' and keyword "
        "arguments '%s' not found. %d pattern(s) tried: %s" %
        (
            viewname, args, kwargs, len(patterns),
            [str('').join(c.describe() for c in constraints) for constraints in patterns],
        )
    )


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
