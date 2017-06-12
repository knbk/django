from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from django.test.utils import override_settings
from django.urls import resolve


class InvalidURLsTests(SimpleTestCase):
    @override_settings(ROOT_URLCONF='urlpatterns.urls.contains_tuple')
    def test_contains_tuple_not_url_instance(self):
        with self.assertRaises(ImproperlyConfigured):
            with override_settings(ROOT_URLCONF='check_framework.urls.contains_tuple'):
                resolve('/')
