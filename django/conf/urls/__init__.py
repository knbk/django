import warnings
from importlib import import_module

from django.core.exceptions import ImproperlyConfigured
from django.urls import (
    LocalePrefix, LocalizedRegexPattern, RegexPattern, Resolver,
    ResolverEndpoint,
)
from django.utils import six
from django.utils.deprecation import RemovedInDjango20Warning
from django.utils.functional import Promise

__all__ = ['handler400', 'handler403', 'handler404', 'handler500', 'include', 'url']

handler400 = 'django.views.defaults.bad_request'
handler403 = 'django.views.defaults.permission_denied'
handler404 = 'django.views.defaults.page_not_found'
handler500 = 'django.views.defaults.server_error'


def include(arg, namespace=None, app_name=None):
    if app_name and not namespace:
        raise ValueError('Must specify a namespace if specifying app_name.')
    if app_name:
        warnings.warn(
            'The app_name argument to django.conf.urls.include() is deprecated. '
            'Set the app_name in the included URLconf instead.',
            RemovedInDjango20Warning, stacklevel=2
        )

    if isinstance(arg, tuple):
        # callable returning a namespace hint
        try:
            urlconf_module, app_name = arg
        except ValueError:
            if namespace:
                raise ImproperlyConfigured(
                    'Cannot override the namespace for a dynamic module that provides a namespace'
                )
            warnings.warn(
                'Passing a 3-tuple to django.conf.urls.include() is deprecated. '
                'Pass a 2-tuple containing the list of patterns and app_name, '
                'and provide the namespace argument to include() instead.',
                RemovedInDjango20Warning, stacklevel=2
            )
            urlconf_module, app_name, namespace = arg
    else:
        # No namespace hint - use manually provided namespace
        urlconf_module = arg

    if isinstance(urlconf_module, six.string_types):
        urlconf_module = import_module(urlconf_module)
    patterns = getattr(urlconf_module, 'urlpatterns', urlconf_module)
    app_name = getattr(urlconf_module, 'app_name', app_name)
    if namespace and not app_name:
        warnings.warn(
            'Specifying a namespace in django.conf.urls.include() without '
            'providing an app_name is deprecated. Set the app_name attribute '
            'in the included module, or pass a 2-tuple containing the list of '
            'patterns and app_name instead.',
            RemovedInDjango20Warning, stacklevel=2
        )

    namespace = namespace or app_name

    # Make sure we can iterate through the patterns (without this, some
    # testcases will break).
    if isinstance(patterns, (list, tuple)):
        for name, resolver in patterns:
            # Test if the LocaleRegexURLResolver is used within the include;
            # this should throw an error since this is not allowed!
            if any(isinstance(constraint, LocalePrefix) for constraint in resolver.constraints):
                raise ImproperlyConfigured(
                    'Using i18n_patterns in an included URLconf is not allowed.')

    return (urlconf_module, app_name, namespace)


def url(constraints, view, kwargs=None, name=None, decorators=None):
    if isinstance(constraints, six.string_types):
        constraints = RegexPattern(constraints)
    elif isinstance(constraints, Promise):
        constraints = LocalizedRegexPattern(constraints)
    if not isinstance(constraints, (list, tuple)):
        constraints = [constraints]

    if isinstance(view, (list, tuple)):
        resolvers, app_name, namespace = view
        if namespace and app_name is None:
            app_name = namespace
        return namespace, Resolver(resolvers, app_name, constraints=constraints, kwargs=kwargs, decorators=decorators)
    elif callable(view):
        return None, ResolverEndpoint(view, name, constraints=constraints, kwargs=kwargs, decorators=decorators)
    else:
        raise TypeError('view must be a callable or a list/tuple in the case of include().')
