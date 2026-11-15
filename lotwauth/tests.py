from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, modify_settings, override_settings

from .dn import InvalidDN, parse_dn, user_data_from_dn

User = get_user_model()

# PrintableString "N0CALL"
RFC_DN = ('emailAddress=n0call@example.com,CN=Test Middle Operator,'
          '1.3.6.1.4.1.12348.1.1=#13064E3043414C4C')
LEGACY_DN = ('/1.3.6.1.4.1.12348.1.1=N0CALL/CN=Test Middle Operator'
             '/emailAddress=n0call@example.com')


class DNParsingTests(SimpleTestCase):
    def test_rfc_form(self):
        self.assertEqual(user_data_from_dn(RFC_DN), {
            'username': 'N0CALL',
            'first_name': 'Test',
            'last_name': 'Operator',
            'email': 'n0call@example.com',
        })

    def test_legacy_form_is_refused(self):
        with self.assertRaises(InvalidDN):
            user_data_from_dn(LEGACY_DN)

    def test_duplicate_callsign_is_refused(self):
        with self.assertRaises(InvalidDN):
            user_data_from_dn(RFC_DN + ',1.3.6.1.4.1.12348.1.1=N1CALL')

    def test_utf8string_callsign(self):
        dn = 'CN=X,1.3.6.1.4.1.12348.1.1=#0C064E3043414C4C'
        self.assertEqual(user_data_from_dn(dn)['username'], 'N0CALL')

    def test_plain_callsign_is_uppercased(self):
        dn = 'CN=X,1.3.6.1.4.1.12348.1.1=n0call'
        self.assertEqual(user_data_from_dn(dn)['username'], 'N0CALL')

    def test_escapes(self):
        attrs = parse_dn(r'CN=Last\, First,O=J\C3\A4rvi+OU=a\+b')
        self.assertEqual(attrs['cn'], 'Last, First')
        self.assertEqual(attrs['o'], 'Järvi')
        self.assertEqual(attrs['ou'], 'a+b')

    def test_escaped_comma_cannot_inject_callsign(self):
        dn = r'CN=Evil\,1.3.6.1.4.1.12348.1.1=N0CALL,emailAddress=e@example.com'
        with self.assertRaises(InvalidDN):
            user_data_from_dn(dn)

    def test_single_word_name(self):
        data = user_data_from_dn('CN=Club,1.3.6.1.4.1.12348.1.1=N0CALL')
        self.assertEqual((data['first_name'], data['last_name']), ('Club', ''))

    def test_rejects_garbage(self):
        for dn in ('', '   ', 'CN=No Callsign', 'CN=X,1.3.6.1.4.1.12348.1.1=#zz',
                   'CN=X,1.3.6.1.4.1.12348.1.1=#1306', 'CN=X,1.3.6.1.4.1.12348.1.1=a b',
                   'no-equals-sign'):
            with self.subTest(dn=dn):
                with self.assertRaises(InvalidDN):
                    user_data_from_dn(dn)


CERT_HEADERS = {
    'HTTP_X_SSL_CLIENT_VERIFY': 'SUCCESS',
    'HTTP_X_SSL_CLIENT_S_DN': RFC_DN,
}


@override_settings(LOTW_AUTOCREATE_USERS=True)
@modify_settings(
    MIDDLEWARE={
        'append': 'lotwauth.middleware.LoTWCertificateMiddleware',
    },
    AUTHENTICATION_BACKENDS={
        'prepend': 'lotwauth.backends.LoTWCertificateBackend',
    },
)
class CertificateLoginTests(TestCase):
    def get(self, **extra):
        return self.client.get('/sysop/', secure=True, **extra)

    def test_no_certificate(self):
        self.assertRedirects(self.get(), '/accounts/login/?next=/sysop/',
                             fetch_redirect_response=False)
        self.assertFalse(User.objects.exists())

    def test_creates_user_and_logs_in(self):
        response = self.get(**CERT_HEADERS)
        self.assertEqual(response.status_code, 200)
        user = User.objects.get()
        self.assertEqual(user.username, 'N0CALL')
        self.assertEqual(user.email, 'n0call@example.com')
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff)
        self.assertEqual(response.wsgi_request.user, user)
        # the session persists without the certificate
        self.assertEqual(self.get().status_code, 200)

    def test_matches_existing_user_case_insensitively(self):
        existing = User.objects.create_user('n0call', 'old@example.com', 'pw')
        response = self.get(**CERT_HEADERS)
        self.assertEqual(response.wsgi_request.user, existing)
        existing.refresh_from_db()
        self.assertEqual(existing.email, 'old@example.com')
        self.assertEqual(User.objects.count(), 1)

    @override_settings(LOTW_AUTOCREATE_USERS=False)
    def test_autocreate_disabled(self):
        self.assertEqual(self.get(**CERT_HEADERS).status_code, 302)
        self.assertFalse(User.objects.exists())

    def test_inactive_user(self):
        User.objects.create_user('N0CALL', is_active=False)
        self.assertEqual(self.get(**CERT_HEADERS).status_code, 302)

    def test_verification_failure(self):
        headers = dict(CERT_HEADERS, HTTP_X_SSL_CLIENT_VERIFY='FAILED:certificate has expired')
        self.assertEqual(self.get(**headers).status_code, 302)
        headers = dict(CERT_HEADERS, HTTP_X_SSL_CLIENT_VERIFY='NONE')
        self.assertEqual(self.get(**headers).status_code, 302)
        self.assertFalse(User.objects.exists())

    def test_requires_https(self):
        response = self.client.get('/sysop/', **CERT_HEADERS)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.exists())

    def test_bad_dn(self):
        headers = dict(CERT_HEADERS, HTTP_X_SSL_CLIENT_S_DN='CN=No Callsign')
        self.assertEqual(self.get(**headers).status_code, 302)
        self.assertFalse(User.objects.exists())

    def test_password_login_still_works(self):
        User.objects.create_user('other', password='pw')
        self.assertTrue(self.client.login(username='other', password='pw'))
        self.assertEqual(self.get().status_code, 200)


class DisabledByDefaultTests(TestCase):
    def test_headers_ignored_when_disabled(self):
        response = self.client.get('/sysop/', secure=True, **CERT_HEADERS)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.exists())
