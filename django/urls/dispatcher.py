from functools import lru_cache

from django.urls.exceptions import Resolver404
from django.urls.conf import URLConf
from django.urls.patterns import RegexPattern


class Dispatcher:
    def __init__(self, urlconf):
        self.urlconf = URLConf(urlconf)
        root_pattern = RegexPattern('^/')
        self.urlpatterns = [pattern.bind_to_pattern(root_pattern) for pattern in self.urlconf.build_patterns()]

    def resolve(self, path):
        for pattern in self.urlpatterns:
            for match in pattern.resolve(path):
                return match
        raise Resolver404


@lru_cache(maxsize=None)
def get_resolver(urlconf):
    if urlconf is None:
        from django.conf import settings
        urlconf = settings.ROOT_URLCONF
    return Dispatcher(urlconf)
