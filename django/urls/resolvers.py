"""
This module converts requested URLs to callback view functions.

RegexURLResolver is the main class here. Its resolve() method takes a URL (as
a string) and returns a ResolverMatch object which provides access to all
attributes of the resolved URL match.
"""
import functools
import re
import threading
from importlib import import_module
from urllib.parse import quote

from django.conf import settings
from django.core.checks import Warning
from django.core.checks.urls import check_resolver
from django.core.exceptions import ImproperlyConfigured
from django.utils.datastructures import MultiValueDict
from django.utils.functional import cached_property
from django.utils.http import RFC3986_SUBDELIMS
from django.utils.regex_helper import normalize
from django.utils.translation import get_language

from .converters import get_converter
from .exceptions import NoReverseMatch, Resolver404
from .utils import get_callable


class ResolverMatch:
    def __init__(self, func, args, kwargs, url_name=None, app_names=None, namespaces=None):
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.url_name = url_name

        # If a URLRegexResolver doesn't have a namespace or app_name, it passes
        # in an empty value.
        self.app_names = [x for x in app_names if x] if app_names else []
        self.app_name = ':'.join(self.app_names)
        self.namespaces = [x for x in namespaces if x] if namespaces else []
        self.namespace = ':'.join(self.namespaces)

        if not hasattr(func, '__name__'):
            # A class-based view
            self._func_path = '.'.join([func.__class__.__module__, func.__class__.__name__])
        else:
            # A function-based view
            self._func_path = '.'.join([func.__module__, func.__name__])

        view_path = url_name or self._func_path
        self.view_name = ':'.join(self.namespaces + [view_path])

    def __getitem__(self, index):
        return (self.func, self.args, self.kwargs)[index]

    def __repr__(self):
        return "ResolverMatch(func=%s, args=%s, kwargs=%s, url_name=%s, app_names=%s, namespaces=%s)" % (
            self._func_path, self.args, self.kwargs, self.url_name,
            self.app_names, self.namespaces,
        )


@functools.lru_cache(maxsize=None)
def get_resolver(urlconf=None):
    if urlconf is None:
        from django.conf import settings
        urlconf = settings.ROOT_URLCONF
    return RegexURLResolver(r'^/', urlconf)


@functools.lru_cache(maxsize=None)
def get_ns_resolver(ns_pattern, resolver):
    # Build a namespaced resolver for the given parent URLconf pattern.
    # This makes it possible to have captured parameters in the parent
    # URLconf pattern.
    ns_resolver = RegexURLResolver(ns_pattern, resolver.url_patterns)
    return RegexURLResolver(r'^/', [ns_resolver])


class BaseURL:
    """
    Base class for URL instances.
    """
    pass


class CheckURLMixin:
    def describe(self):
        """
        Format the URL pattern for display in warning messages.
        """
        description = "'{}'".format(self.regex.pattern)
        if getattr(self, 'name', False):
            description += " [name='{}']".format(self.name)
        return description

    def _check_pattern_startswith_slash(self):
        """
        Check that the pattern does not begin with a forward slash.
        """
        regex_pattern = self.regex.pattern
        if not settings.APPEND_SLASH:
            # Skip check as it can be useful to start a URL pattern with a slash
            # when APPEND_SLASH=False.
            return []
        if (regex_pattern.startswith('/') or regex_pattern.startswith('^/')) and not regex_pattern.endswith('/'):
            warning = Warning(
                "Your URL pattern {} has a regex beginning with a '/'. Remove this "
                "slash as it is unnecessary. If this pattern is targeted in an "
                "include(), ensure the include() pattern has a trailing '/'.".format(
                    self.describe()
                ),
                id="urls.W002",
            )
            return [warning]
        else:
            return []


class RegexURLMixin:
    def compile(self):
        try:
            return re.compile(str(self.pattern)), {}
        except re.error as e:
            raise ImproperlyConfigured(
                '"%s" is not a valid regular expression: %s' % (self.pattern, e)
            )


_PATH_PARAMETER_COMPONENT_RE = re.compile(
    '<(?:(?P<converter>[^:]+):)?(?P<parameter>[A-Za-z0-9_]+)>'
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
        raw_converter = match.group('converter')
        if raw_converter is None:
            # If no converter is specified, the default is ``string``.
            raw_converter = 'string'
        converter = get_converter(raw_converter)
        converters[parameter] = converter
        parts.append('(?P<' + parameter + '>' + converter.regex + ')')
    return ''.join(parts), converters


class PathURLMixin:
    def compile(self):
        # TODO: Wrap in `try/except:raise ImproperlyConfigured`
        regex, converters = _route_to_regex(str(self.pattern))
        if isinstance(self, URLPattern):
            regex += '$'
        return re.compile(regex), converters


class URLPattern(CheckURLMixin, BaseURL):
    def __init__(self, regex, callback, default_args=None, name=None, converters=None):
        self.pattern = regex
        self.callback = callback  # the view
        self.default_args = default_args or {}
        self.name = name
        self._converters = converters or {}
        self._regex_dict = {}
        self._converters_dict = {}
        self._local = threading.local()

    def _populate(self):
        # Short-circuit if called recursively in this thread to prevent
        # infinite recursion. Concurrent threads may call this at the same
        # time and will need to continue, so set 'populating' on a
        # thread-local variable.
        if getattr(self._local, 'populating', False):
            return
        self._local.populating = True
        language_code = get_language()
        regex, converters = self.compile()
        converters = dict(converters, **self._converters)
        self._regex_dict[language_code] = regex
        self._converters_dict[language_code] = converters
        self._populated = True
        self._local.populating = False

    @property
    def regex(self):
        language_code = get_language()
        if language_code not in self._regex_dict:
            self._populate()
        return self._regex_dict[language_code]

    @property
    def converters(self):
        language_code = get_language()
        if language_code not in self._converters_dict:
            self._populate()
        return self._converters_dict[language_code]

    def __repr__(self):
        return '<%s %s %s>' % (self.__class__.__name__, self.name, self.regex.pattern)

    def check(self):
        warnings = self._check_pattern_name()
        if not warnings:
            warnings = self._check_pattern_startswith_slash()
        return warnings

    def _check_pattern_name(self):
        """
        Check that the pattern name does not contain a colon.
        """
        if self.name is not None and ":" in self.name:
            warning = Warning(
                "Your URL pattern {} has a name including a ':'. Remove the colon, to "
                "avoid ambiguous namespace references.".format(self.describe()),
                id="urls.W003",
            )
            return [warning]
        else:
            return []

    def resolve(self, path):
        match = self.regex.search(path)
        if match:
            # If there are any named groups, use those as kwargs, ignoring
            # non-named groups. Otherwise, pass all non-named arguments as
            # positional arguments.
            kwargs = match.groupdict()
            args = () if kwargs else match.groups()
            # In both cases, pass any extra_kwargs as **kwargs.
            kwargs.update(self.default_args)

            # Check if we need to convert any arguments to Python.
            if self.converters:
                for key, value in kwargs.items():
                    try:
                        converter = self.converters[key]
                    except KeyError:
                        pass
                    else:
                        kwargs[key] = converter.to_python(value)

            return ResolverMatch(self.callback, args, kwargs, self.name)

    @cached_property
    def lookup_str(self):
        """
        A string that identifies the view (e.g. 'path.to.view_function' or
        'path.to.ClassBasedView').
        """
        callback = self.callback
        # Python 3.5 collapses nested partials, so can change "while" to "if"
        # when it's the minimum supported version.
        while isinstance(callback, functools.partial):
            callback = callback.func
        if not hasattr(callback, '__name__'):
            return callback.__module__ + "." + callback.__class__.__name__
        return callback.__module__ + "." + callback.__qualname__


class URLResolver(CheckURLMixin, BaseURL):
    def __init__(self, regex, urlconf_name, default_kwargs=None, app_name=None, namespace=None, converters=None):
        self.pattern = regex
        # urlconf_name is the dotted Python path to the module defining
        # urlpatterns. It may also be an object with an urlpatterns attribute
        # or urlpatterns itself.
        self.urlconf_name = urlconf_name
        self.callback = None
        self.default_kwargs = default_kwargs or {}
        self.namespace = namespace
        self.app_name = app_name
        self._converters = converters or {}
        self._regex_dict = {}
        self._converters_dict = {}
        self._reverse_dict = {}
        self._namespace_dict = {}
        self._app_dict = {}
        # set of dotted paths to all functions and classes that are used in
        # urlpatterns
        self._callback_strs = set()
        self._populated = False
        self._local = threading.local()

    def __repr__(self):
        if isinstance(self.urlconf_name, list) and len(self.urlconf_name):
            # Don't bother to output the whole list, it can be huge
            urlconf_repr = '<%s list>' % self.urlconf_name[0].__class__.__name__
        else:
            urlconf_repr = repr(self.urlconf_name)
        return '<%s %s (%s:%s) %s>' % (
            self.__class__.__name__, urlconf_repr, self.app_name,
            self.namespace, self.regex.pattern,
        )

    def check(self):
        warnings = self._check_include_trailing_dollar()
        for pattern in self.url_patterns:
            warnings.extend(check_resolver(pattern))
        if not warnings:
            warnings = self._check_pattern_startswith_slash()
        return warnings

    def _check_include_trailing_dollar(self):
        """
        Check that include is not used with a regex ending with a dollar.
        """
        regex_pattern = self.regex.pattern
        if regex_pattern.endswith('$') and not regex_pattern.endswith(r'\$'):
            warning = Warning(
                "Your URL pattern {} uses include with a regex ending with a '$'. "
                "Remove the dollar from the regex to avoid problems including "
                "URLs.".format(self.describe()),
                id="urls.W001",
            )
            return [warning]
        else:
            return []

    def _populate(self):
        # Short-circuit if called recursively in this thread to prevent
        # infinite recursion. Concurrent threads may call this at the same
        # time and will need to continue, so set 'populating' on a
        # thread-local variable.
        if getattr(self._local, 'populating', False):
            return
        self._local.populating = True
        lookups = MultiValueDict()
        namespaces = {}
        apps = {}
        language_code = get_language()
        regex, converters = self.compile()
        # We need to store these two values *before* inspecting url_patterns,
        # because there might be cyclic dependencies.
        self._regex_dict[language_code] = regex
        self._converters_dict[language_code] = converters
        for pattern in reversed(self.url_patterns):
            if not isinstance(pattern, BaseURL):
                msg = (
                    "Your URL pattern {!r} is invalid. Ensure that urlpatterns "
                    "is a list of url() instances."
                ).format(pattern)
                raise ImproperlyConfigured(msg)
            if isinstance(pattern, URLPattern):
                self._callback_strs.add(pattern.lookup_str)
            p_pattern = pattern.regex.pattern
            if p_pattern.startswith('^'):
                p_pattern = p_pattern[1:]
            if isinstance(pattern, URLResolver):
                if pattern.namespace:
                    namespaces[pattern.namespace] = (p_pattern, pattern)
                    if pattern.app_name:
                        apps.setdefault(pattern.app_name, []).append(pattern.namespace)
                else:
                    parent_pat = pattern.regex.pattern
                    for name in pattern.reverse_dict:
                        for matches, pat, defaults, pat_converters in pattern.reverse_dict.getlist(name):
                            new_matches = normalize(parent_pat + pat)
                            lookups.appendlist(
                                name,
                                (
                                    new_matches,
                                    p_pattern + pat,
                                    dict(defaults, **pattern.default_kwargs),
                                    dict(self.converters, **pat_converters),
                                )
                            )
                    for namespace, (prefix, sub_pattern) in pattern.namespace_dict.items():
                        namespaces[namespace] = (p_pattern + prefix, sub_pattern)
                    for app_name, namespace_list in pattern.app_dict.items():
                        apps.setdefault(app_name, []).extend(namespace_list)
                if not getattr(pattern._local, 'populating', False):
                    pattern._populate()
                self._callback_strs.update(pattern._callback_strs)
            else:
                bits = normalize(p_pattern)
                lookups.appendlist(pattern.callback, (bits, p_pattern, pattern.default_args, pattern.converters))
                if pattern.name is not None:
                    lookups.appendlist(pattern.name, (bits, p_pattern, pattern.default_args, pattern.converters))
        self._reverse_dict[language_code] = lookups
        self._namespace_dict[language_code] = namespaces
        self._app_dict[language_code] = apps
        self._populated = True
        self._local.populating = False

    @property
    def regex(self):
        language_code = get_language()
        if language_code not in self._regex_dict:
            self._populate()
        return self._regex_dict[language_code]

    @property
    def converters(self):
        language_code = get_language()
        if language_code not in self._converters_dict:
            self._populate()
        return self._converters_dict[language_code]

    @property
    def reverse_dict(self):
        language_code = get_language()
        if language_code not in self._reverse_dict:
            self._populate()
        return self._reverse_dict[language_code]

    @property
    def namespace_dict(self):
        language_code = get_language()
        if language_code not in self._namespace_dict:
            self._populate()
        return self._namespace_dict[language_code]

    @property
    def app_dict(self):
        language_code = get_language()
        if language_code not in self._app_dict:
            self._populate()
        return self._app_dict[language_code]

    def _is_callback(self, name):
        if not self._populated:
            self._populate()
        return name in self._callback_strs

    def resolve(self, path):
        path = str(path)  # path may be a reverse_lazy object
        tried = []
        match = self.regex.search(path)
        if match:
            new_path = path[match.end():]
            for pattern in self.url_patterns:
                try:
                    sub_match = pattern.resolve(new_path)
                except Resolver404 as e:
                    sub_tried = e.args[0].get('tried')
                    if sub_tried is not None:
                        tried.extend([pattern] + t for t in sub_tried)
                    else:
                        tried.append([pattern])
                else:
                    if sub_match:
                        # Merge captured arguments in match with submatch
                        sub_match_dict = dict(match.groupdict(), **self.default_kwargs)
                        # Check if we need to convert any arguments to Python.
                        # At this point, sub_match_dict only contains items from this match, and not from any
                        # sub-matches yet.
                        if self.converters:
                            for key, value in sub_match_dict.items():
                                try:
                                    converter = self.converters[key]
                                except KeyError:
                                    pass
                                else:
                                    sub_match_dict[key] = converter.to_python(value)
                        # Update the sub_match_dict with the kwargs from the sub_match.
                        sub_match_dict.update(sub_match.kwargs)

                        # If there are *any* named groups, ignore all non-named groups.
                        # Otherwise, pass all non-named arguments as positional arguments.
                        sub_match_args = sub_match.args
                        if not sub_match_dict:
                            sub_match_args = match.groups() + sub_match.args

                        return ResolverMatch(
                            sub_match.func,
                            sub_match_args,
                            sub_match_dict,
                            sub_match.url_name,
                            [self.app_name] + sub_match.app_names,
                            [self.namespace] + sub_match.namespaces,
                        )
                    tried.append([pattern])
            raise Resolver404({'tried': tried, 'path': new_path})
        raise Resolver404({'path': path})

    @cached_property
    def urlconf_module(self):
        if isinstance(self.urlconf_name, str):
            return import_module(self.urlconf_name)
        else:
            return self.urlconf_name

    @cached_property
    def url_patterns(self):
        # urlconf_module might be a valid set of patterns, so we default to it
        patterns = getattr(self.urlconf_module, "urlpatterns", self.urlconf_module)
        try:
            iter(patterns)
        except TypeError:
            msg = (
                "The included URLconf '{name}' does not appear to have any "
                "patterns in it. If you see valid patterns in the file then "
                "the issue is probably caused by a circular import."
            )
            raise ImproperlyConfigured(msg.format(name=self.urlconf_name))
        return patterns

    def resolve_error_handler(self, view_type):
        callback = getattr(self.urlconf_module, 'handler%s' % view_type, None)
        if not callback:
            # No handler specified in file; use lazy import, since
            # django.conf.urls imports this file.
            from django.conf import urls
            callback = getattr(urls, 'handler%s' % view_type)
        return get_callable(callback), {}

    def reverse(self, lookup_view, *args, **kwargs):
        return self._reverse_with_prefix(lookup_view, '', *args, **kwargs)

    def _reverse_with_prefix(self, lookup_view, _prefix, *args, **kwargs):
        if args and kwargs:
            raise ValueError("Don't mix *args and **kwargs in call to reverse()!")

        if not self._populated:
            self._populate()

        possibilities = self.reverse_dict.getlist(lookup_view)

        for possibility, pattern, defaults, converters in possibilities:
            for result, params in possibility:
                if args:
                    if len(args) != len(params):
                        continue
                    candidate_subs = dict(zip(params, args))
                else:
                    if (set(kwargs.keys()) | set(defaults.keys()) != set(params) |
                            set(defaults.keys())):
                        continue
                    matches = True
                    for k, v in defaults.items():
                        if kwargs.get(k, v) != v:
                            matches = False
                            break
                    if not matches:
                        continue
                    candidate_subs = kwargs

                # We need to convert the candidate subs to text, which is only
                # possible when we know the converters, so that's why we need
                # to do this inside the loop.
                text_candidate_subs = {}
                for k, v in candidate_subs.items():
                    if k in converters:
                        text_candidate_subs[k] = converters[k].to_url(v)
                    else:
                        text_candidate_subs[k] = str(v)
                # WSGI provides decoded URLs, without %xx escapes, and the URL
                # resolver operates on such URLs. First substitute arguments
                # without quoting to build a decoded URL and look for a match.
                # Then, if we have a match, redo the substitution with quoted
                # arguments in order to return a properly encoded URL.
                candidate_pat = _prefix.replace('%', '%%') + result
                if re.search('^%s%s' % (re.escape(_prefix), pattern), candidate_pat % text_candidate_subs):
                    # safe characters from `pchar` definition of RFC 3986
                    url = quote(candidate_pat % text_candidate_subs, safe=RFC3986_SUBDELIMS + '/~:@')
                    # Don't allow construction of scheme relative urls.
                    if url.startswith('//'):
                        url = '/%%2F%s' % url[2:]
                    return url
        # lookup_view can be URL name or callable, but callables are not
        # friendly in error messages.
        m = getattr(lookup_view, '__module__', None)
        n = getattr(lookup_view, '__name__', None)
        if m is not None and n is not None:
            lookup_view_s = "%s.%s" % (m, n)
        else:
            lookup_view_s = lookup_view

        patterns = [pattern for (_, pattern, _, _) in possibilities]
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


class PathURLPattern(PathURLMixin, URLPattern):
    pass


class PathURLResolver(PathURLMixin, URLResolver):
    pass


class RegexURLPattern(RegexURLMixin, URLPattern):
    pass


class RegexURLResolver(RegexURLMixin, URLResolver):
    pass


class LocaleRegexURLResolver(RegexURLResolver):
    """
    A URL resolver that always matches the active language code as URL prefix.

    Rather than taking a regex argument, we just override the ``regex``
    function to always return the active language-code as regex.
    """
    def __init__(
        self, urlconf_name, default_kwargs=None, app_name=None, namespace=None,
        prefix_default_language=True,
    ):
        super().__init__(None, urlconf_name, default_kwargs, app_name, namespace)
        self.prefix_default_language = prefix_default_language
        self._regex_dict = {}

    @property
    def pattern(self):
        language_code = get_language() or settings.LANGUAGE_CODE
        if language_code == settings.LANGUAGE_CODE and not self.prefix_default_language:
            regex_string = ''
        else:
            regex_string = '^%s/' % language_code
        return regex_string

    @pattern.setter
    def pattern(self, value):
        # For the `LocaleRegexURLResolver`, we don't want to get passed a
        # pattern, however `super()` *does* set `pattern`. In `__init__`, we
        # pass in `None`, so let's just verify that that is actually the case.
        assert value is None
