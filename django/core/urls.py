from __future__ import unicode_literals
from importlib import import_module
import re
import itertools

from django.core.exceptions import ImproperlyConfigured
from django.http import Http404
from django.utils import six, lru_cache
from django.utils.datastructures import MultiValueDict
from django.utils.encoding import force_text
from django.utils.functional import cached_property
from django.utils.regex_helper import normalize
from django.utils.translation import get_language


@lru_cache.lru_cache(maxsize=None)
def get_resolver(urlconf=None):
    if urlconf is None:
        from django.conf import settings
        urlconf = settings.ROOT_URLCONF
    return import_module(urlconf).patterns


class URL(object):
    def __init__(self, request=None, host='', path='', constraints=None):
        self.request = request
        self.host, self.path = host, path
        if request and not (host or path):
            self.host, self.path = request.get_host(), request.path
        self.constraints = constraints or []

    def __str__(self):
        return self.build_path()

    def prepend_constraints(self, constraints):
        self.constraints = [(constraint, (), {}) for constraint in constraints] + self.constraints

    def add_constraint(self, constraint):
        args, kwargs = constraint.match(self)
        self.constraints.append((constraint, args, kwargs))

    def reconstruct(self):
        # reconstruct the original url from the saved constraints and arguments
        while self.constraints:
            constraint, args, kwargs = self.constraints.pop(0)
            constraint.construct(self, *args, **kwargs)
        return self

    def construct(self, *args, **kwargs):
        # construct an url from the constraints and an external set of arguments
        while self.constraints:
            constraint, _, _ = self.constraints.pop(0)
            args, kwargs = constraint.construct(self, *args, **kwargs)
        if args or kwargs:
            raise NoReverseMatch("Leftover arguments")
        return self

    def build_path(self, request=None):
        # build the needed url, including the host if it differs from `request.get_host()`
        url = self.clone().reconstruct()
        if request and request.get_host() != url.host:
            return "%s%s" % (url.host, url.path)
        return url.path

    @property
    def args(self):
        if self.kwargs:
            return ()
        return list(itertools.chain(args for _, args, _ in self.constraints))

    @property
    def kwargs(self):
        kwargs = {}
        for _, _, kw in self.constraints:
            kwargs.update(kw)
        return kwargs

    def clone(self):
        return self.__class__(
            request=self.request,
            host=self.host,
            path=self.path,
            constraints=list(self.constraints)
        )


class Resolver404(Http404):
    pass


class NoReverseMatch(Exception):
    pass


class Resolver(object):
    def __init__(self, patterns, constraints=None, app_name=''):
        self.patterns = patterns
        self.constraints = constraints
        self.app_name = app_name

    @property
    def namespace_dict(self):
        dict_ = MultiValueDict()
        for k, v in self.patterns:
            if k is not None and getattr(v, 'app_name', None):
                dict_.appendlist(v.app_name, k)
        return dict_

    def resolve_namespace(self, name, current_app):
        if current_app:
            if name in self.namespace_dict and current_app[0] in self.namespace_dict.getlist(name):
                return current_app[0], current_app[1:]
        if name in self.namespace_dict:
            return self.namespace_dict[name], current_app[1:]
        else:
            return name, current_app[1:]

    def search(self, lookup, current_app=None):
        lookup_name = lookup[0]
        lookup_path = lookup[1:]

        if lookup_name:
            # need to resolve app names to namespaces here first
            for name, pattern in self.patterns:
                if not name or name == lookup_name:
                    path = lookup_path if name else lookup
                    try:
                        for constraints in pattern.search(path, current_app):
                            yield self.constraints + constraints
                    except NoReverseMatch as e:
                        # print(e)  # add to tried patterns
                        continue
        elif lookup_path:
            for name, pattern in self.patterns:
                try:
                    for constraints in pattern.search(lookup_path, current_app):
                        yield self.constraints + constraints
                except NoReverseMatch as e:
                    # print(e)  # add to tried patterns
                    continue

        raise NoReverseMatch("End of search(): %r" % self.patterns)

    def resolve(self, url):
        new_url = url.clone()
        for constraint in self.constraints:
            try:
                new_url.add_constraint(constraint)
            except Resolver404 as e:
                raise Resolver404("Constraint failed: %s" % e)

        tried = new_url.constraints
        for name, pattern in self.patterns:
            try:
                view, constraints = pattern.resolve(new_url)
            except Resolver404 as e:
                # print(e)
                pass
            else:
                return view, constraints
        raise Resolver404("End of resolve()")


def name_from_view(callback):
    if not hasattr(callback, '__name__'):
        # A class-based view
        return '.'.join([callback.__class__.__module__, callback.__class__.__name__])
    else:
        # A function-based view
        return '.'.join([callback.__module__, callback.__name__])


class View(object):
    def __init__(self, constraints, callback, name=None, default_kwargs=None, decorators=None):
        self.constraints = constraints
        self.callback = callback
        self.name = name if name else name_from_view(callback)
        self.decorators = decorators or []

    def search(self, lookup, current_app=None):
        if len(lookup) != 1 or lookup[0] != self.name:
            raise NoReverseMatch("View doesn't match: %s" % self.name)
        yield self.constraints

    def resolve(self, url):
        new_url = url.clone()
        for constraints in self.constraints:
            try:
                new_url.add_constraint(constraints)
            except Resolver404 as e:
                raise Resolver404("Constraint failed: %s" % e)
        return self, new_url

    @property
    def decorated_callback(self):
        callback = self.callback
        for decorator in self.decorators:
            callback = decorator(callback)
        return callback

    def __call__(self, request, *args, **kwargs):
        return self.decorated_callback(request, *args, **kwargs)


class Constraint(object):
    def __init__(self, default_kwargs=None):
        self.default_kwargs = default_kwargs or {}

    def match(self, url_description):
        return (), {}

    def construct(self, url_description, *args, **kwargs):
        return args, kwargs


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
            if kwargs:
                args = ()
                kwargs.update(self.default_kwargs)
            else:
                args = match.groups()
            url.path = url.path[match.end():]
            return args, kwargs
        raise Resolver404("No match for %s" % self.regex.pattern)

    @cached_property
    def normalized_regex(self):
        return normalize(self.regex.pattern)

    def construct(self, url, *args, **kwargs):
        patterns = self.normalized_regex
        # need a good long look at _reverse_with_prefix to see what's exactly needed here
        # doesn't work yet for args (format needs a mapping)
        for pattern, params in patterns:
            if not kwargs:
                p_args = dict(zip(params, args))
                path = pattern % p_args
                new_args = args[len(p_args):]
                new_kwargs = {}
            else:
                p_kwargs = {k: v for k, v in kwargs.items() if k in params}
                path = pattern % p_kwargs
                new_args = ()
                new_kwargs = {k: v for k, v in kwargs.items() if k not in params}
            if self.regex.search(path):
                url.path += path
                return new_args, new_kwargs
        raise NoReverseMatch(
            "Failed to reverse %s with arguments %s and "
            "keyword arguments %s" % (self.regex, args, kwargs)
        )


def resolve(request, urlconf=None):
    if isinstance(request, six.string_types):
        # RemovedInDjangoXXWarning
        url = URL(path=request)
    else:
        url = URL(request)
    return get_resolver(urlconf).resolve(url)


def reverse(view, urlconf=None, args=None, kwargs=None, prefix=None, current_app=None):
    resolver = get_resolver(urlconf)
    args = args or ()
    kwargs = kwargs or {}

    if isinstance(view, six.string_types):
        view_path = view.split(':')
    elif isinstance(view, (list, tuple)):
        view_path = view
    elif callable(view):
        view_path = name_from_view(view)
    else:
        raise TypeError("view is not a string, a list, a tuple or a callable.")

    if current_app and isinstance(current_app, six.string_types):
        current_app = current_app.split(':')

    for constraints in resolver.search(view_path, current_app):
        url = URL(constraints=[(constraint, (), {}) for constraint in constraints])
        try:
            return url.construct(*args, **kwargs)
        except NoReverseMatch:
            continue

    raise NoReverseMatch("End of reverse")
