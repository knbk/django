import re
from contextlib import suppress
from urllib.parse import quote

from django.urls.exceptions import NoReverseMatch
from django.urls.patterns import CombinedPattern
from django.urls.utils import get_lookup_string
from django.utils.datastructures import MultiValueDict
from django.utils.functional import cached_property
from django.utils.http import RFC3986_SUBDELIMS
from django.utils.regex_helper import normalize


class ResolverMatch(object):
    def __init__(self, view, args, kwargs, url_name=None, app_names=None, namespaces=None):
        self.func = self.view = view
        self.args = args
        self.kwargs = kwargs
        self.url_name = url_name
        self.app_names = app_names or []
        self.namespaces = namespaces or []

    @cached_property
    def app_name(self):
        """
        Return the fully qualified application namespace.
        """
        return ':'.join(self.app_names)

    @cached_property
    def namespace(self):
        """
        Return the fully qualified instance namespace.
        """
        return ':'.join(self.namespaces)

    @cached_property
    def view_name(self):
        """
        Return the fully qualified view name, consisting of the instance
        namespace and the view's name.
        """
        view_name = self.url_name or get_lookup_string(self.view)
        return ':'.join(self.namespaces + [view_name])

    def __getitem__(self, index):
        return (self.view, self.args, self.kwargs)[index]

    def __repr__(self):
        return "ResolverMatch(func=%s, args=%s, kwargs=%s, url_name=%s, app_names=%s, namespaces=%s)" % (
            get_lookup_string(self.view), self.args, self.kwargs, self.url_name,
            self.app_names, self.namespaces,
        )


class BaseResolver:
    def __init__(self, pattern, kwargs=None):
        self.pattern = CombinedPattern(pattern)
        self.kwargs = kwargs or {}

    @property
    def regex(self):
        return self.pattern.compile()

    def bind_to_pattern(self, pattern, kwargs=None):
        new = self.copy()
        new.pattern = CombinedPattern(pattern, self.pattern)
        if kwargs is not None:
            new.kwargs.update(kwargs)
        return new

    def copy(self):
        raise NotImplementedError("Subclasses of BaseResolver must implement `copy()`.")


class View(BaseResolver):
    def __init__(self, pattern, view, name=None, kwargs=None):
        super().__init__(pattern, kwargs)
        self.view = view
        self.name = name

    def resolve(self, path):
        match = self.regex.search(path)
        if match:
            kwargs = match.groupdict()
            kwargs = self.pattern.to_python(kwargs)
            if kwargs:
                args = ()
            else:
                args = match.groups()
            kwargs.update(self.kwargs)
            yield ResolverMatch(self.view, args, kwargs, self.name)

    def reverse(self, *args, **kwargs):
        text_args = [str(arg) for arg in args]
        text_kwargs = self.pattern.to_url(kwargs)

        from django.urls import get_script_prefix
        prefix = get_script_prefix()

        for result, params in normalize(self.regex.pattern[2:]):
            if args:
                if len(args) != len(params):
                    continue
                candidate_subs = dict(zip(params, text_args))
            else:
                if set(kwargs) | set(self.kwargs) != set(params) | set(self.kwargs):
                    continue
                matches = True
                for k, v in self.kwargs.items():
                    if kwargs.get(k, v) != v:
                        matches = False
                        break
                if not matches:
                    continue
                candidate_subs = text_kwargs
            # WSGI provides decoded URLs, without %xx escapes, and the URL
            # resolver operates on such URLs. First substitute arguments
            # without quoting to build a decoded URL and look for a match.
            # Then, if we have a match, redo the substitution with quoted
            # arguments in order to return a properly encoded URL.
            candidate_pat = prefix.replace('%', '%%') + result
            if re.search('^%s%s' % (re.escape(prefix), self.regex.pattern[2:]), candidate_pat % candidate_subs):
                # safe characters from `pchar` definition of RFC 3986
                url = quote(candidate_pat % candidate_subs, safe=RFC3986_SUBDELIMS + '/~:@')
                # Don't allow construction of scheme relative urls.
                if url.startswith('//'):
                    url = '/%%2F%s' % url[2:]
                return url

        raise NoReverseMatch({'tried': [self.regex.pattern[2:]]})

    def copy(self):
        return View(
            self.pattern,
            self.view,
            self.name,
            self.kwargs.copy(),
        )


class Namespace(BaseResolver):
    def __init__(self, pattern, urlconf, app_name=None, namespace=None, kwargs=None):
        super().__init__(pattern, kwargs)
        self.urlconf = urlconf
        self.app_name = app_name
        self.namespace = namespace

    @cached_property
    def endpoints(self):
        endpoints = []
        for pattern in self.urlconf.urlpatterns:
            for endpoint in pattern.build_patterns():
                endpoints.append(endpoint.bind_to_pattern(self.pattern, self.kwargs))
        return endpoints

    @cached_property
    def view_dict(self):
        views = MultiValueDict()
        for endpoint in reversed(self.endpoints):
            if hasattr(endpoint, 'view'):
                views.appendlist(endpoint.view, endpoint)
                if endpoint.name:
                    views.appendlist(endpoint.name, endpoint)
        return views

    @cached_property
    def namespace_dict(self):
        namespaces = {}
        for endpoint in reversed(self.endpoints):
            if hasattr(endpoint, 'namespace'):
                namespaces[endpoint.namespace] = endpoint
        return namespaces

    @cached_property
    def app_dict(self):
        app_names = {}
        for endpoint in reversed(self.endpoints):
            if hasattr(endpoint, 'namespace'):
                app_names.setdefault(endpoint.app_name, []).append(endpoint.namespace)
        return app_names

    def resolve(self, path):
        match = self.regex.search(path)
        if match:
            for endpoint in self.endpoints:
                for match in endpoint.resolve(path):
                    yield ResolverMatch(
                        match.view, match.args, match.kwargs, match.url_name,
                        ([self.app_name] if self.app_name else []) + match.app_names,
                        ([self.namespace] if self.namespace else []) + match.namespaces,
                    )

    def reverse_lookup(self, lookup, current_app=None):
        if len(lookup) == 1:
            return self.view_dict.getlist(lookup[0])

        ns = lookup[0]
        current_ns = current_app[0] if current_app else None
        with suppress(KeyError):
            app_list = self.app_dict[ns]
            # Yes! Path part matches an app in the current Resolver.
            if current_ns and current_ns in app_list:
                # If we are reversing for a particular app, use that
                # namespace.
                ns = current_ns
            elif ns not in app_list:
                # The name isn't shared by one of the instances (i.e.,
                # the default) so pick the first instance as the default.
                ns = app_list[0]

        if ns != current_ns:
            current_app = None

        try:
            return self.namespace_dict[ns].reverse_lookup(lookup[1:], current_app[1:] if current_app else None)
        except KeyError:
            raise NoReverseMatch({
                'key': ns,
                'resolved_path': [],
            })
        except NoReverseMatch as e:
            if self.namespace:
                e.args[0]['resolved_path'] = [self.namespace] + e.args[0]['resolved_path']
            raise e

    def copy(self):
        return Namespace(
            self.pattern,
            self.urlconf,
            self.app_name,
            self.namespace,
            self.kwargs.copy(),
        )
