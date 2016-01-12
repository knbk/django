from collections import defaultdict
from importlib import import_module
from threading import Lock

from django.conf import urls
from django.conf.urls import URLConf, URLPattern
from django.template.context import BaseContext
from django.utils import lru_cache, six
from django.utils.datastructures import MultiValueDict
from django.utils.encoding import force_text
from django.utils.functional import cached_property
from django.utils.six.moves.urllib.parse import urlsplit, urlunsplit
from django.utils.translation import override

from .constraints import ScriptPrefix
from .exceptions import NoReverseMatch, Resolver404
from .resolvers import get_resolver
from .utils import URL, describe_constraints, get_callable


@lru_cache.lru_cache(maxsize=None)
def get_dispatcher(urlconf=None):
    if urlconf is None:
        from django.conf import settings
        urlconf = settings.ROOT_URLCONF
    return Dispatcher(urlconf)


class Dispatcher(object):
    def __init__(self, urlconf):
        self.urlconf_name = urlconf
        self.resolver = get_resolver(urlconf)

        self._lock = Lock()
        self._namespaces = {
            (): ((), URLPattern([ScriptPrefix()], URLConf(urlconf))),
        }
        self._loaded = set()
        self._callbacks = set()

        self.reverse_dict = MultiValueDict()
        self.app_dict = defaultdict(list)

    def _load(self, root, namespace_root, app_root, constraints, kwargs):
        for urlpattern in reversed(root.target.urlpatterns):
            constraints += urlpattern.constraints
            kwargs.push(urlpattern.target.kwargs)
            if urlpattern.is_endpoint():
                value = list(constraints), kwargs.flatten()
                self.reverse_dict.appendlist(namespace_root + (urlpattern.target.view,), value)
                if urlpattern.target.url_name:
                    self.reverse_dict.appendlist(namespace_root + (urlpattern.target.url_name,), value)
                self._callbacks.add(urlpattern.target.lookup_str)
            elif not urlpattern.target.namespace and not urlpattern.target.app_name:
                self._load(urlpattern, namespace_root, app_root, list(constraints), kwargs)
            else:
                app_name = app_root + (urlpattern.target.app_name or urlpattern.target.namespace,)
                self.app_dict[app_name].append(urlpattern.target.namespace)
                self._namespaces[namespace_root + (urlpattern.target.namespace,)] = (
                    app_name,
                    URLPattern(
                        list(constraints),
                        URLConf(
                            list(urlpattern.target.urlpatterns), urlpattern.target.app_name,
                            namespace=urlpattern.target.namespace, kwargs=kwargs.flatten(),
                        ),
                    ),
                )
            constraints = constraints[:-len(urlpattern.constraints)]
            kwargs.pop()

    def load_namespace(self, namespace):
        with self._lock:
            if namespace in self._loaded:
                return

            if namespace not in self._namespaces:
                raise NoReverseMatch(
                    "'%s' is not a registered namespace inside '%s'" %
                    (namespace[-1], ':'.join(namespace[:-1]))
                )

            app, urlpattern = self._namespaces.pop(namespace)
            constraints = list(urlpattern.constraints)
            kwargs = BaseContext()
            kwargs.dicts[0] = urlpattern.target.kwargs
            self._load(urlpattern, namespace, app, constraints, kwargs)
            self._loaded.add(namespace)

    def load(self, lookup):
        # The last entry is the view, not a namespace. This loads everything
        # up to namespace[:-1].
        for i, _ in enumerate(lookup):
            if lookup[:i] not in self._loaded:
                self.load_namespace(lookup[:i])

    def resolve(self, path, request=None):
        for match in self.resolver.resolve(path, request):
            try:
                match.preprocess(request)
            except Resolver404:
                continue
            else:
                return match

    def reverse(self, viewname, *args, **kwargs):
        if isinstance(viewname, (list, tuple)):  # Common case: resolved by self.resolve_namespace().
            lookup = tuple(viewname)
        elif isinstance(viewname, six.string_types):
            lookup = tuple(viewname.split(':'))
        elif viewname:
            lookup = (viewname,)
        else:
            raise NoReverseMatch("Cannot reverse empty view name '%s'" % (viewname,))

        text_args = [force_text(x) for x in args]
        text_kwargs = {k: force_text(v) for k, v in kwargs.items()}

        self.load(lookup)

        patterns = []
        for constraints, default_kwargs in self.reverse_dict.getlist(lookup):
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
                return six.text_type(url)

        if lookup and isinstance(lookup[-1], six.string_types):
            viewname = ':'.join(lookup)

        if patterns:
            raise NoReverseMatch(
                "Reverse for '%s' with arguments '%s' and keyword "
                "arguments '%s' not found. %d pattern(s) tried: %s" %
                (
                    viewname, args, kwargs, len(patterns),
                    [str(describe_constraints(constraints)) for constraints in patterns],
                )
            )
        else:
            msg = "'%s' is not a registered view name" % lookup[-1]
            if len(lookup) > 1:
                msg += " inside '%s'" % ':'.join(lookup[:-1])
            raise NoReverseMatch(msg)

    def _resolve_lookup(self, root, lookup, current_app=None):
        if not lookup:
            return lookup

        self.load_namespace(root)

        ns = lookup[0]
        app = current_app[0] if current_app else None
        root = root + (ns,)
        options = self.app_dict[root]
        if app and app in options:
            namespace = app
        elif ns in options:
            namespace = ns
            current_app = []
        elif options:
            namespace = options[0]
            current_app = []
        else:
            namespace = ns
            current_app = []

        return [namespace] + self._resolve_lookup(root, lookup[1:], current_app[1:])

    def resolve_namespace(self, viewname, current_app=None):
        if isinstance(viewname, six.string_types):
            lookup = viewname.split(':')
        elif viewname:
            lookup = [viewname]
        else:
            raise NoReverseMatch("Cannot reverse empty view name '%s'" % (viewname,))

        if current_app:
            current_app = current_app.split(':')

        return self._resolve_lookup((), lookup[:-1], current_app) + lookup[-1:]

    @cached_property
    def urlconf_module(self):
        if isinstance(self.urlconf_name, six.string_types):
            return import_module(self.urlconf_name)
        else:
            return self.urlconf_name

    def _is_callback(self, name):
        return name in self._callbacks

    def resolve_error_handler(self, view_type):
        callback = getattr(self.urlconf_module, 'handler%s' % view_type, None)
        if not callback:
            # No handler specified in file; use default
            callback = getattr(urls, 'handler%s' % view_type)
        return get_callable(callback), {}

    def is_valid_path(self, path, request=None):
        """
        Returns True if the given path resolves against the default URL resolver,
        False otherwise.

        This is a convenience method to make working with "is this a match?" cases
        easier, avoiding unnecessarily indented try...except blocks.
        """
        try:
            self.resolve(path, request)
            return True
        except Resolver404:
            return False

    def translate_url(self, url, lang_code, request=None):
        """
        Given a URL (absolute or relative), try to get its translated version in
        the `lang_code` language (either by i18n_patterns or by translated regex).
        Return the original URL if no translated version is found.
        """
        parsed = urlsplit(url)
        try:
            match = self.resolve(parsed.path, request=request)
        except Resolver404:
            pass
        else:
            to_be_reversed = match.namespaces + [match.url_name]
            with override(lang_code):
                try:
                    url = self.reverse(to_be_reversed, *match.args, **match.kwargs)
                except NoReverseMatch:
                    pass
                else:
                    url = urlunsplit((parsed.scheme, parsed.netloc, url, parsed.query, parsed.fragment))
        return url
