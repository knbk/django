from django.conf import settings
from django.core.checks.messages import Warning
from django.core.checks.urls import (
    E006, check_url_config, check_url_namespaces_unique, check_url_settings,
    get_warning_for_invalid_pattern,
)
from django.test import SimpleTestCase
from django.test.utils import override_settings


class CheckUrlConfigTests(SimpleTestCase):
    @override_settings(ROOT_URLCONF='check_framework.urls.no_warnings')
    def test_no_warnings(self):
        result = check_url_config(None)
        self.assertEqual(result, [])

    @override_settings(ROOT_URLCONF='check_framework.urls.warning_in_include')
    def test_check_resolver_recursive(self):
        # The resolver is checked recursively (examining url()s in include()).
        result = check_url_config(None)
        self.assertEqual(len(result), 1)
        warning = result[0]
        self.assertEqual(warning.id, 'urls.W001')

    @override_settings(ROOT_URLCONF='check_framework.urls.include_with_dollar')
    def test_include_with_dollar(self):
        result = check_url_config(None)
        self.assertEqual(len(result), 1)
        warning = result[0]
        self.assertEqual(warning.id, 'urls.W001')
        expected_msg = "Your URL pattern '^include-with-dollar$' uses include with a regex ending with a '$'."
        self.assertIn(expected_msg, warning.msg)

    @override_settings(ROOT_URLCONF='check_framework.urls.beginning_with_slash')
    def test_beginning_with_slash(self):
        result = check_url_config(None)
        self.assertEqual(len(result), 1)
        warning = result[0]
        self.assertEqual(warning.id, 'urls.W002')
        expected_msg = (
            "Your URL pattern '/starting-with-slash/$' has a regex beginning "
            "with a '/'. Remove this slash as it is unnecessary. If this "
            "pattern is targeted in an include(), ensure the include() pattern "
            "has a trailing '/'."
        )

        self.assertIn(expected_msg, warning.msg)

    @override_settings(
        ROOT_URLCONF='check_framework.urls.beginning_with_slash',
        APPEND_SLASH=False,
    )
    def test_beginning_with_slash_append_slash(self):
        # It can be useful to start a URL pattern with a slash when
        # APPEND_SLASH=False (#27238).
        result = check_url_config(None)
        self.assertEqual(result, [])

    @override_settings(ROOT_URLCONF='check_framework.urls.name_with_colon')
    def test_name_with_colon(self):
        result = check_url_config(None)
        self.assertEqual(len(result), 1)
        warning = result[0]
        self.assertEqual(warning.id, 'urls.W003')
        expected_msg = "Your URL pattern '^$' [name='name_with:colon'] has a name including a ':'."
        self.assertIn(expected_msg, warning.msg)

    @override_settings(ROOT_URLCONF=None)
    def test_no_root_urlconf_in_settings(self):
        delattr(settings, 'ROOT_URLCONF')
        result = check_url_config(None)
        self.assertEqual(result, [])

    def test_get_warning_for_invalid_pattern_string(self):
        warning = get_warning_for_invalid_pattern('')[0]
        self.assertEqual(
            warning.hint,
            "Try removing the string ''. The list of urlpatterns should "
            "not have a prefix string as the first element.",
        )

    def test_get_warning_for_invalid_pattern_tuple(self):
        warning = get_warning_for_invalid_pattern((r'^$', lambda x: x))[0]
        self.assertEqual(warning.hint, "Try using url() instead of a tuple.")

    def test_get_warning_for_invalid_pattern_other(self):
        warning = get_warning_for_invalid_pattern(object())[0]
        self.assertIsNone(warning.hint)

    @override_settings(ROOT_URLCONF='check_framework.urls.non_unique_namespaces')
    def test_check_non_unique_namespaces(self):
        result = check_url_namespaces_unique(None)
        self.assertEqual(len(result), 2)
        non_unique_namespaces = ['app-ns1', 'app-1']
        warning_messages = [
            "URL namespace '{}' isn't unique. You may not be able to reverse "
            "all URLs in this namespace".format(namespace)
            for namespace in non_unique_namespaces
        ]
        for warning in result:
            self.assertIsInstance(warning, Warning)
            self.assertEqual('urls.W005', warning.id)
            self.assertIn(warning.msg, warning_messages)

    @override_settings(ROOT_URLCONF='check_framework.urls.unique_namespaces')
    def test_check_unique_namespaces(self):
        result = check_url_namespaces_unique(None)
        self.assertEqual(result, [])


class CheckURLSettingsTests(SimpleTestCase):

    @override_settings(STATIC_URL='a/', MEDIA_URL='b/')
    def test_slash_no_errors(self):
        self.assertEqual(check_url_settings(None), [])

    @override_settings(STATIC_URL='', MEDIA_URL='')
    def test_empty_string_no_errors(self):
        self.assertEqual(check_url_settings(None), [])

    @override_settings(STATIC_URL='noslash')
    def test_static_url_no_slash(self):
        self.assertEqual(check_url_settings(None), [E006('STATIC_URL')])

    @override_settings(STATIC_URL='slashes//')
    def test_static_url_double_slash_allowed(self):
        # The check allows for a double slash, presuming the user knows what
        # they are doing.
        self.assertEqual(check_url_settings(None), [])

    @override_settings(MEDIA_URL='noslash')
    def test_media_url_no_slash(self):
        self.assertEqual(check_url_settings(None), [E006('MEDIA_URL')])
