from django.urls.patterns import CombinedPattern
from django.urls.utils import get_lookup_string
from django.utils.functional import cached_property


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
    def __init__(self, pattern, kwargs):
        self.pattern = CombinedPattern(pattern)
        self.kwargs = kwargs

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
    def __init__(self, pattern, view, name, kwargs):
        super().__init__(pattern, kwargs)
        self.view = view
        self.name = name

    def resolve(self, path):
        match = self.regex.search(path)
        if match:
            kwargs = match.groupdict()
            kwargs = self.pattern.convert(kwargs)
            if kwargs:
                args = ()
            else:
                args = match.groups()
            kwargs.update(self.kwargs)
            yield ResolverMatch(self.view, args, kwargs, self.name)

    def copy(self):
        return View(
            self.pattern,
            self.view,
            self.name,
            self.kwargs.copy(),
        )


class Namespace(BaseResolver):
    def __init__(self, pattern, urlconf, app_name, namespace, kwargs):
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

    def resolve(self, path):
        match = self.regex.search(path)
        if match:
            for endpoint in self.endpoints:
                for match in endpoint.resolve(path):
                    yield ResolverMatch(
                        match.view, match.args, match.kwargs, match.url_name,
                        [self.app_name] + match.app_names,
                        [self.namespace] + match.namespaces,
                    )

    def copy(self):
        return Namespace(
            self.pattern,
            self.urlconf,
            self.app_name,
            self.namespace,
            self.kwargs.copy(),
        )
