from importlib import import_module

from django.core.exceptions import ImproperlyConfigured
from django.urls.conf import include, url
from django.views import defaults

__all__ = ['handler400', 'handler403', 'handler404', 'handler500', 'include', 'url']

handler400 = defaults.bad_request
handler403 = defaults.permission_denied
handler404 = defaults.page_not_found
handler500 = defaults.server_error
