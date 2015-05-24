from __future__ import unicode_literals

import re

from django.core.exceptions import ImproperlyConfigured
from django.http import Http404
from django.utils import lru_cache, six
from django.utils.datastructures import MultiValueDict
from django.utils.encoding import (
    escape_uri_path, force_str, force_text, iri_to_uri,
    python_2_unicode_compatible,
)
from django.utils.functional import cached_property
from django.utils.module_loading import import_module
from django.utils.regex_helper import normalize
from django.utils.six.moves.urllib.parse import urljoin, urlsplit, urlunsplit
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

    @cached_property
    def app_name(self):
        return ':'.join(self.app_names)

    @cached_property
    def namespace(self):
        return ':'.join(self.namespaces)

    @cached_property
    def view_name(self):
        view_path = self.url_name or self._func_path
        return ':'.join(self.namespaces + [view_path])

    @cached_property
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


@python_2_unicode_compatible
class URL(object):
    def __init__(self, scheme='', host='', path='', script_name='', path_info='', query_string='', fragment=''):
        self.scheme = scheme
        self.host = host
        self.path = path
        self.script_name = script_name
        self.path_info = path_info
        self.query_string = query_string
        self.fragment = fragment

    @classmethod
    def from_request(cls, request):
        """
        Build an URLDescriptor from a request.
        """
        return cls(
            scheme=request.scheme, host=request.get_host(), path=request.path,
            script_name=request.META.get('SCRIPT_NAME', ''), path_info=request.path_info,
            query_string=request.META.get('QUERY_STRING', ''), fragment='',  # No fragment
        )

    def __repr__(self):
        return force_str('<URL %r>' % self.absolute_uri)

    def __str__(self):
        return self.relative_uri

    @cached_property
    def relative_uri(self):
        return self.build_relative_uri()

    @cached_property
    def absolute_uri(self):
        return self.build_absolute_uri()

    def get_full_path(self, force_append_slash=False):
        # RFC 3986 requires query string arguments to be in the ASCII range.
        # Rather than crash if this doesn't happen, we encode defensively.
        return '%s%s%s%s' % (
            escape_uri_path(self.path),
            '/' if force_append_slash and not self.path.endswith('/') else '',
            ('?' + iri_to_uri(self.query_string)) if self.query_string else '',
            ('#' + iri_to_uri(self.fragment)) if self.fragment else '',
        )

    def build_relative_uri(self, location=None):
        if isinstance(location, URL):
            location = location.build_absolute_uri()
        scheme, host, path, query_string, fragment = urlsplit(self.build_absolute_uri(location))
        if scheme == self.scheme:
            scheme = ''
        if not scheme and host == self.host:
            host = ''
        return urlunsplit((scheme, host, path, query_string, fragment))

    def build_absolute_uri(self, location=None):
        """
        Builds an absolute URI from the location and the variables available in
        this request. If no ``location`` is specified, the absolute URI is
        built on ``request.get_full_path()``. Anyway, if the location is
        absolute, it is simply converted to an RFC 3987 compliant URI and
        returned and if location is relative or is scheme-relative (i.e.,
        ``//example.com/``), it is urljoined to a base URL constructed from the
        request variables.
        """
        if location is None:
            # Make it an absolute url (but schemeless and domainless) for the
            # edge case that the path starts with '//'.
            location = '//%s' % self.get_full_path()
        bits = urlsplit(location)
        if not (bits.scheme and bits.netloc):
            current_uri = '{scheme}://{host}{path}'.format(scheme=self.scheme,
                                                           host=self.host,
                                                           path=self.path)
            # Join the constructed URL with the provided location, which will
            # allow the provided ``location`` to apply query strings to the
            # base path as well as override the host, if it begins with //
            location = urljoin(current_uri, location)
        return iri_to_uri(location)

    def copy(self):
        return self.__class__(
            scheme=self.scheme,
            host=self.host,
            path=self.path,
            script_name=self.script_name,
            path_info=self.path_info,
            query_string=self.query_string,
            fragment=self.fragment,
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
        if name in self.namespace_dict and name not in self.namespace_dict.get_list(name):
            return self.namespace_dict[name], current_app[1:]
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

    def match(self, request, url):
        new_url = url.copy()
        args, kwargs = (), {}
        for constraint in self.constraints:
            new_url, new_args, new_kwargs = constraint.match(request, new_url)
            args += new_args
            kwargs.update(new_kwargs)
        return new_url, args, kwargs

    def resolve(self, request, url):
        new_url, args, kwargs = self.match(request, url)

        if self.func:
            return ResolverMatch(
                self.func, args, kwargs, self.url_name,
                app_names=[self.app_name], decorators=self.decorators,
            )

        for name, pattern in self.patterns:
            try:
                sub_match = pattern.resolve(request, new_url)
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

    def match(self, request, url):
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
    def match(self, request, url):
        match = self.regex.search(url.path_info)
        if match:
            kwargs = match.groupdict()
            kwargs.update(self.default_kwargs)
            if kwargs:
                args = ()
            else:
                args = match.groups()
            matched_path, extra_path_info = url.path_info[:match.end()], url.path_info[match.end():]
            url.script_name += matched_path
            url.path_info = extra_path_info
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
        # request is the actual path
        url = URL(path=request, path_info=request)
        return get_resolver(urlconf).resolve(None, url)
    url = request.url.copy()
    return get_resolver(urlconf).resolve(request, url)


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
            # print(e)
            continue

    raise NoReverseMatch("End of reverse")
