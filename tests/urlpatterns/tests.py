from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from django.test.utils import override_settings
from django.urls import path, resolve, reverse, register_converter, Resolver404
from django.urls.converters import BaseConverter

from .converters import Base64Converter, DynamicConverter


@override_settings(ROOT_URLCONF='urlpatterns.path_urls')
class SimplifiedURLTests(SimpleTestCase):

    def test_path_lookup_without_parameters(self):
        match = resolve('/articles/2003/')
        self.assertEqual(match.url_name, 'articles-2003')
        self.assertEqual(match.args, ())
        self.assertEqual(match.kwargs, {})

    def test_path_lookup_with_typed_parameters(self):
        match = resolve('/articles/2015/')
        self.assertEqual(match.url_name, 'articles-year')
        self.assertEqual(match.args, ())
        self.assertEqual(match.kwargs, {'year': 2015})

    def test_path_lookup_with_multiple_paramaters(self):
        match = resolve('/articles/2015/04/12/')
        self.assertEqual(match.url_name, 'articles-year-month-day')
        self.assertEqual(match.args, ())
        self.assertEqual(match.kwargs, {
            'year': 2015,
            'month': 4,
            'day': 12,
        })

    def test_path_reverse_without_parameter(self):
        url = reverse('articles-2003')
        self.assertEqual(url, '/articles/2003/')

    def test_path_reverse_with_parameter(self):
        url = reverse('articles-year-month-day', kwargs={
            'year': 2015,
            'month': 4,
            'day': 12,
        })
        self.assertEqual(url, '/articles/2015/4/12/')

    @override_settings(
        ROOT_URLCONF='urlpatterns.path_base64_urls',
    )
    def test_non_identical_converter_resolve(self):
        # base64 of 'hello' is 'aGVsbG8=\n'
        match = resolve('/base64/aGVsbG8=/')
        self.assertEqual(match.url_name, 'base64')
        self.assertEqual(match.kwargs, {'value': b'hello'})

    @override_settings(
        ROOT_URLCONF='urlpatterns.path_base64_urls',
    )
    def test_non_identical_converter_reverse(self):
        # base64 of 'hello' is 'aGVsbG8=\n'
        url = reverse('base64', kwargs={'value': b'hello'})
        self.assertEqual(url, '/base64/aGVsbG8=/')

    def test_path_inclusion_is_matchable(self):
        match = resolve('/included_urls/extra/something/')
        self.assertEqual(match.url_name, 'inner-extra')
        self.assertEqual(match.kwargs, {'extra': 'something'})

    def test_path_inclusion_is_reversable(self):
        url = reverse('inner-extra', kwargs={'extra': 'something'})
        self.assertEqual(url, '/included_urls/extra/something/')


class InvalidURLsTests(SimpleTestCase):
    def test_contains_tuple_not_url_instance(self):
        with self.assertRaises(ImproperlyConfigured):
            with override_settings(ROOT_URLCONF='urlpatterns.urls.contains_tuple'):
                resolve('/')


class ParameterRestrictionTests(SimpleTestCase):
    def test_non_identifier_parameter_name_causes_exception(self):
        with self.assertRaises(ImproperlyConfigured):
            p = path(r'hello/<str:1>/', lambda r: None)
            p.resolve('hello/1/')

    def test_allows_non_ascii_but_valid_identifiers(self):
        # \u0394 is "GREEK CAPITAL LETTER DELTA", a valid identifier.
        p = path('hello/<string:\u0394>/', lambda r: None)
        match = p.resolve('hello/1/')
        self.assertEqual(match.kwargs, {'\u0394': '1'})


@override_settings(
    ROOT_URLCONF='urlpatterns.path_dynamic_urls',
)
class ConversionExceptionTests(SimpleTestCase):
    """
    How are conversion-errors dealt with?
    """

    def test_resolve_value_error_means_no_match(self):
        @DynamicConverter.register_to_python
        def raises_value_error(value):
            raise ValueError()
        with self.assertRaises(Resolver404):
            resolve('/dynamic/abc/')

    def test_resolve_type_error_propogates(self):
        @DynamicConverter.register_to_python
        def raises_type_error(value):
            raise TypeError("This type error propagates.")
        with self.assertRaisesMessage(TypeError, "This type error propagates."):
            resolve('/dynamic/abc/')

    def test_reverse_value_error_propagates(self):
        @DynamicConverter.register_to_url
        def raises_value_error(value):
            raise ValueError("This value error propagates.")
        with self.assertRaisesMessage(ValueError, "This value error propagates."):
            reverse('dynamic', kwargs={'value': object()})
