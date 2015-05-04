from __future__ import unicode_literals
import re

from django.core.exceptions import ImproperlyConfigured
from django.http import Http404
from django.utils import six, lru_cache
from django.utils.datastructures import MultiValueDict
from django.utils.encoding import force_text
from django.utils.functional import cached_property
from django.utils.module_loading import import_module
from django.utils.regex_helper import normalize
from django.utils.translation import get_language


@lru_cache.lru_cache(maxsize=None)
def get_resolver(urlconf=None):
    if urlconf is None:
        from django.conf import settings
        urlconf = settings.ROOT_URLCONF
    return import_module(urlconf).patterns


class ResolverMatch(object):
    def __init__(self, func, args, kwargs, url_name=None, app_names=None, namespaces=None, decorators=None):
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.url_name = url_name
        if app_names:
            self.app_names = [x for x in app_names if x]
        else:
            self.app_names = []

        if namespaces:
            self.namespaces = [x for x in namespaces if x]
        else:
            self.namespaces = []

        self.decorators = decorators or []

        func = getattr(func, 'callback', func)

        if not hasattr(func, '__name__'):
            # A class-based view
            self._func_path = '.'.join([func.__class__.__module__, func.__class__.__name__])
        else:
            # A function-based view
            self._func_path = '.'.join([func.__module__, func.__name__])

    @property
    def app_name(self):
        return ':'.join(self.app_names)

    @property
    def namespace(self):
        return ':'.join(self.namespaces)

    @property
    def view_name(self):
        view_path = self.url_name or self._func_path
        return ':'.join(self.namespaces + [view_path])

    @property
    def callback(self):
        callback = self.func
        for decorator in self.decorators:
            callback = decorator(callback)
        return callback

    def __getitem__(self, index):
        return (self.callback, self.args, self.kwargs)[index]

    def __repr__(self):
        return "ResolverMatch(func=%s, args=%s, kwargs=%s, url_name=%s, app_name=%s, namespace=%s)" % (
            self._func_path, self.args, self.kwargs, self.url_name, self.app_name, self.namespace)

    @classmethod
    def from_submatch(cls, sub_match, args, kwargs, app_name=None, name=None, decorators=None):
        if kwargs or sub_match.kwargs:
            kwargs.update(sub_match.kwargs)
            args = ()
        else:
            args += sub_match.args
        return cls(
            func=sub_match.func,
            args=args,
            kwargs=kwargs,
            url_name=sub_match.url_name,
            app_names=[app_name] + sub_match.app_names,
            namespaces=[name] + sub_match.namespaces,
            decorators=(decorators or []) + sub_match.decorators,
        )


@six.python_2_unicode_compatible
class URL(object):
    def __init__(self, request=None, host='', path=''):
        self.request = request
        self.host, self.path = host, path
        if request and not (host or path):
            self.host, self.path = request.get_host(), request.path

    def __str__(self):
        return self.build_path()

    def build_path(self, request=None):
        # build the needed url, including the host if it differs from `request.get_host()`
        if request and request.get_host() != self.host:
            return "%s%s" % (self.host, self.path)
        return self.path

    def clone(self):
        return self.__class__(
            request=self.request,
            host=self.host,
            path=self.path,
        )


class Resolver404(Http404):
    pass


class NoReverseMatch(Exception):
    pass


class Resolver(object):
    def __init__(self, patterns=None, func=None, url_name='', app_name='', decorators=None, constraints=None):
        if patterns and func:
            raise ValueError("Cannot have subpatterns and a view function.")
        self.patterns = patterns
        self.app_name = app_name
        self.func = func
        self.url_name = url_name if url_name or not func else name_from_view(func)
        self.decorators = decorators or []
        self.constraints = constraints or []

    @cached_property
    def namespace_dict(self):
        dict_ = MultiValueDict()
        for k, v in self.patterns:
            if k and getattr(v, 'app_name', None):
                dict_.appendlist(v.app_name, k)
        return dict_

    def resolve_namespace(self, name, current_app):
        if not name:
            if current_app:
                return current_app[0], current_app[1:]
            return name, current_app[1:]
        if current_app:
            if name in self.namespace_dict and current_app[0] in self.namespace_dict.getlist(name):
                return current_app[0], current_app[1:]
        if name in self.namespace_dict:
            return self.namespace_dict[name], current_app[1:]
        else:
            return name, current_app[1:]

    def search(self, lookup, current_app):
        if not lookup:
            return
        if self.func:
            if len(lookup) == 1 and (lookup[0] == self.url_name or lookup[0] is self.func):
                yield self.constraints
            return
        lookup_name, new_app = self.resolve_namespace(lookup[0], current_app)
        lookup_path = lookup[1:]

        if lookup_name:
            for name, pattern in self.patterns:
                if not name or name == lookup_name:
                    path = lookup_path if name else lookup
                    app = new_app if name else current_app
                    for constraints in pattern.search(path, app):
                        yield self.constraints + constraints
        if lookup_path and not lookup[0]:
            for constraints in self.search(lookup_path, new_app):
                yield constraints
            for name, pattern in self.patterns:
                for constraints in pattern.search(lookup, current_app):
                    yield self.constraints + constraints

    def match(self, url):
        new_url = url.clone()
        args, kwargs = (), {}
        for constraint in self.constraints:
            new_url, new_args, new_kwargs = constraint.match(new_url)
            args += new_args
            kwargs.update(new_kwargs)
        return new_url, args, kwargs

    def resolve(self, url):
        new_url, args, kwargs = self.match(url)

        if self.func:
            return ResolverMatch(
                self.func, args, kwargs, self.url_name,
                app_names=[self.app_name], decorators=self.decorators,
            )

        for name, pattern in self.patterns:
            try:
                sub_match = pattern.resolve(new_url)
            except Resolver404 as e:
                # print(e)
                continue
            return ResolverMatch.from_submatch(
                sub_match, args, kwargs, self.app_name,
                name, self.decorators
            )
        raise Resolver404("End of resolve()")


def name_from_view(callback):
    if hasattr(callback, 'name'):
        return callback.name
    if not hasattr(callback, '__name__'):
        # A class-based view
        return '.'.join([callback.__class__.__module__, callback.__class__.__name__])
    else:
        # A function-based view
        return '.'.join([callback.__module__, callback.__name__])


class Constraint(object):
    def __init__(self, default_kwargs=None):
        self.default_kwargs = default_kwargs or {}

    def match(self, url):
        raise NotImplemented()

    def construct(self, url, *args, **kwargs):
        raise NotImplemented()


class LocaleRegexProvider(Constraint):
    """
    A mixin to provide a default regex property which can vary by active
    language.

    """
    def __init__(self, regex, *args, **kwargs):
        # regex is either a string representing a regular expression, or a
        # translatable string (using ugettext_lazy) representing a regular
        # expression.
        super(LocaleRegexProvider, self).__init__(*args, **kwargs)
        self._regex = regex
        self._regex_dict = {}

    @property
    def regex(self):
        """
        Returns a compiled regular expression, depending upon the activated
        language-code.
        """
        language_code = get_language()
        if language_code not in self._regex_dict:
            if isinstance(self._regex, six.string_types):
                regex = self._regex
            else:
                regex = force_text(self._regex)
            try:
                compiled_regex = re.compile(regex, re.UNICODE)
            except re.error as e:
                raise ImproperlyConfigured(
                    '"%s" is not a valid regular expression: %s' %
                    (regex, six.text_type(e)))

            self._regex_dict[language_code] = compiled_regex
        return self._regex_dict[language_code]


class RegexPattern(LocaleRegexProvider):
    def match(self, url):
        match = self.regex.search(url.path)
        if match:
            kwargs = match.groupdict()
            kwargs.update(self.default_kwargs)
            if kwargs:
                args = ()
            else:
                args = match.groups()
            url.path = url.path[match.end():]
            return url, args, kwargs
        raise Resolver404("No match for %s" % self.regex.pattern)

    @cached_property
    def normalized_regex(self):
        return normalize(self.regex.pattern)

    def construct(self, url, *args, **kwargs):
        patterns = self.normalized_regex
        # need a good long look at _reverse_with_prefix to see what's exactly needed here
        for pattern, params in patterns:
            if not kwargs:
                p_args = dict(zip(params, args))
                path = pattern % p_args
                new_args = args[len(p_args):]
                new_kwargs = {}
            else:
                path = pattern % kwargs
                new_args = ()
                new_kwargs = {k: v for k, v in kwargs.items() if k not in params}
            if self.regex.search(path):
                url.path += path
                return url, new_args, new_kwargs
        raise NoReverseMatch(
            "Failed to reverse %s with arguments %s and "
            "keyword arguments %s" % (self.regex, args, kwargs)
        )

    def __repr__(self):
        return "<RegexPattern: %r>" % self.regex.pattern


def resolve(request, urlconf=None):
    if isinstance(request, six.string_types):
        url = URL(path=request)
    else:
        url = URL(request)
    return get_resolver(urlconf).resolve(url)


def reverse(view, urlconf=None, args=None, kwargs=None, prefix=None, current_app=None):
    resolver = get_resolver(urlconf)
    original_args = args or ()
    original_kwargs = kwargs or {}

    if isinstance(view, six.string_types):
        view_path = view.split(':')
    elif isinstance(view, (list, tuple)):
        view_path = view
    elif callable(view):
        view_path = name_from_view(view)
    else:
        raise TypeError("'view' is not a string, a list, a tuple or a callable.")

    if current_app and isinstance(current_app, six.string_types):
        current_app = current_app.split(':')
    elif not current_app:
        current_app = []

    for constraints in resolver.search(view_path, current_app):
        url = URL()
        args = original_args[:]
        kwargs = original_kwargs.copy()
        try:
            for constraint in constraints:
                url, args, kwargs = constraint.construct(url, *args, **kwargs)
            if args or kwargs:
                raise NoReverseMatch("Tried %s with arguments %s and keyword arguments %s" % (constraints, args, kwargs))
            return url
        except NoReverseMatch as e:
            print(e)
            continue

    raise NoReverseMatch("End of reverse")
