import json

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from .models import Domain, ReservedHostname, Rotate, Server

User = get_user_model()


class PortalTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.aprs2 = Domain.objects.create(domain='aprs2.net')
        cls.firenet = Domain.objects.create(domain='firenet.us')

        cls.owner = User.objects.create_user(
            'owner', 'owner@example.com', 'pw', first_name='Olivia')
        cls.helper = User.objects.create_user('helper', 'helper@example.com', 'pw')
        cls.stranger = User.objects.create_user('stranger', 'stranger@example.com', 'pw')

        cls.finland = Server.objects.create(
            owner=cls.owner, server_id='T2FINLAND', hostname='finland',
            domain=cls.aprs2, ipv4='192.0.2.1', ipv6='2001:db8::1',
            city='Helsinki', country='FI', email_alerts=True)
        cls.finland.authorized_sysops.add(cls.helper)
        cls.brazil = Server.objects.create(
            owner=cls.stranger, server_id='T2BRAZIL', hostname='brazil',
            domain=cls.aprs2, ipv4='192.0.2.2', city="Sao Paulo & 'co'",
            country='BR', out_of_service=True)
        cls.gone = Server.objects.create(
            server_id='T2GONE', hostname='gone', domain=cls.aprs2,
            ipv4='192.0.2.3', deleted=True)

        cls.rotate = Rotate.objects.create(
            name='Worldwide', description='Worldwide rotate',
            hostname='rotate', domain=cls.aprs2)
        cls.rotate.eligible.set([cls.finland, cls.brazil, cls.gone])
        cls.euro = Rotate.objects.create(
            name='Europe', description='Europe & Africa', hostname='euro',
            domain=cls.aprs2, regional=True)
        cls.euro.eligible.set([cls.finland])

    def setUp(self):
        cache.clear()


class ServersJsonTests(PortalTestCase):
    def test_anonymous(self):
        response = self.client.get('/servers.json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertIn('no-cache', response['Cache-Control'])
        self.assertEqual(json.loads(response.content), {
            'T2FINLAND': {
                'host': 'finland',
                'domain': 'aprs2.net',
                'fqdn': 'finland.aprs2.net',
                'ipv4': '192.0.2.1',
                'ipv6': '2001:db8::1',
                'deleted': False,
                'out_of_service': False,
            },
            'T2BRAZIL': {
                'host': 'brazil',
                'domain': 'aprs2.net',
                'fqdn': 'brazil.aprs2.net',
                'ipv4': '192.0.2.2',
                'ipv6': None,
                'deleted': False,
                'out_of_service': True,
            },
            'T2GONE': {
                'host': 'gone',
                'domain': 'aprs2.net',
                'fqdn': 'gone.aprs2.net',
                'ipv4': '192.0.2.3',
                'ipv6': None,
                'deleted': True,
                'out_of_service': False,
            },
        })

    def test_authenticated_gets_alert_emails(self):
        self.client.force_login(self.stranger)
        data = json.loads(self.client.get('/servers.json').content)
        self.assertEqual(data['T2FINLAND']['email_alerts'], True)
        self.assertEqual(data['T2FINLAND']['email'], 'owner@example.com')
        # no address unless the sysop has enabled alerts
        self.assertEqual(data['T2BRAZIL']['email_alerts'], False)
        self.assertNotIn('email', data['T2BRAZIL'])
        self.assertNotIn('email', data['T2GONE'])

    def test_cache_does_not_leak_emails_to_anonymous(self):
        self.client.force_login(self.stranger)
        self.assertIn(b'owner@example.com', self.client.get('/servers.json').content)
        self.client.logout()
        self.assertNotIn(b'owner@example.com', self.client.get('/servers.json').content)
        self.assertNotIn(b'email', self.client.get('/sysop/servers.json').content)

    def test_also_served_under_sysop(self):
        self.assertEqual(
            self.client.get('/sysop/servers.json').content,
            self.client.get('/servers.json').content)

    def test_cached_and_invalidated_on_save(self):
        self.client.get('/servers.json')
        with self.assertNumQueries(0):
            self.client.get('/servers.json')

        self.finland.ipv4 = '192.0.2.99'
        self.finland.save()
        data = json.loads(self.client.get('/servers.json').content)
        self.assertEqual(data['T2FINLAND']['ipv4'], '192.0.2.99')

    def test_invalidated_on_owner_email_change(self):
        self.client.force_login(self.stranger)
        self.client.get('/servers.json')
        self.owner.email = 'new@example.com'
        self.owner.save()
        data = json.loads(self.client.get('/servers.json').content)
        self.assertEqual(data['T2FINLAND']['email'], 'new@example.com')


class RotatesJsonTests(PortalTestCase):
    def test_content(self):
        response = self.client.get('/rotates.json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        data = json.loads(response.content)
        self.assertEqual(sorted(data), ['euro.aprs2.net', 'rotate.aprs2.net'])
        self.assertEqual(data['euro.aprs2.net'], {
            'name': 'Europe',
            'description': 'Europe & Africa',
            'host': 'euro',
            'domain': 'aprs2.net',
            'fqdn': 'euro.aprs2.net',
            'servers': {
                'T2FINLAND': {
                    'host': 'finland',
                    'domain': 'aprs2.net',
                    'fqdn': 'finland.aprs2.net',
                    'ipv4': '192.0.2.1',
                    'ipv6': '2001:db8::1',
                    'deleted': False,
                    'out_of_service': False,
                },
            },
        })
        self.assertEqual(
            sorted(data['rotate.aprs2.net']['servers']),
            ['T2BRAZIL', 'T2FINLAND', 'T2GONE'])

    def test_never_contains_emails(self):
        self.client.force_login(self.owner)
        self.assertNotIn(b'email', self.client.get('/rotates.json').content)

    def test_invalidated_on_membership_change(self):
        self.client.get('/rotates.json')
        self.euro.eligible.add(self.brazil)
        data = json.loads(self.client.get('/rotates.json').content)
        self.assertEqual(
            sorted(data['euro.aprs2.net']['servers']), ['T2BRAZIL', 'T2FINLAND'])


class ServerListTests(PortalTestCase):
    def test_content(self):
        response = self.client.get('/APRServe2.txt')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/plain')
        text = response.content.decode()
        self.assertTrue(text.startswith(';\r\n; This file lists the Tier2 servers'))
        # Regional rotates, then a separator, then the non-deleted servers of
        # rotate.aprs2.net ordered by hostname. Nothing is HTML-escaped.
        self.assertTrue(text.endswith(
            "; Please use the regional Tier2 DNS addresses as described in the Wiki.\r\n"
            ";\r\n"
            "euro.aprs2.net:14580;Europe & Africa\r\n"
            ";\r\n"
            "brazil.aprs2.net:14580;User Definable Filter Port: T2BRAZIL Sao Paulo & 'co' BR\r\n"
            "finland.aprs2.net:14580;User Definable Filter Port: T2FINLAND Helsinki FI\r\n"
        ), text[-400:])

    def test_also_served_under_sysop(self):
        self.assertEqual(self.client.get('/sysop/APRServe2.txt').status_code, 200)

    def test_missing_main_rotate_is_404(self):
        self.rotate.delete()
        self.assertEqual(self.client.get('/APRServe2.txt').status_code, 404)


class ServerPageTests(PortalTestCase):
    def test_login_required(self):
        for url in ('/sysop/', '/sysop/all', '/sysop/server/%d/' % self.finland.id,
                    '/accounts/profile/'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertRedirects(
                    response, '/accounts/login/?next=' + url,
                    fetch_redirect_response=False)

    def test_own_servers(self):
        self.client.force_login(self.owner)
        response = self.client.get('/sysop/')
        self.assertContains(response, 'Your servers')
        self.assertContains(response, 'T2FINLAND')
        self.assertNotContains(response, 'T2BRAZIL')
        self.assertNotContains(response, 'Your other authorized servers')

    def test_authorized_servers(self):
        self.client.force_login(self.helper)
        response = self.client.get('/sysop/')
        self.assertContains(response, 'Your other authorized servers')
        self.assertContains(response, 'T2FINLAND')

    def test_all_servers_excludes_deleted(self):
        self.client.force_login(self.stranger)
        response = self.client.get('/sysop/all')
        self.assertContains(response, 'T2FINLAND')
        self.assertContains(response, 'T2BRAZIL')
        self.assertContains(response, 'Out of service')
        self.assertNotContains(response, 'T2GONE')

    def test_legacy_root_urls(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(self.client.get('/all').status_code, 200)
        self.assertEqual(reverse('own_servers'), '/sysop/')

    def test_unknown_server_is_404(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get('/sysop/server/999999/').status_code, 404)

    def form_data(self, server, **changes):
        data = {
            'owner': server.owner_id or '',
            'server_id': server.server_id,
            'hostname': server.hostname,
            'domain': server.domain_id,
            'ipv4': server.ipv4 or '',
            'ipv6': server.ipv6 or '',
            'city': server.city or '',
            'country': server.country or '',
            'authorized_sysops': [u.pk for u in server.authorized_sysops.all()],
        }
        if server.email_alerts:
            data['email_alerts'] = 'on'
        data.update(changes)
        return data

    def test_owner_can_edit(self):
        self.client.force_login(self.owner)
        url = reverse('server_detail', args=[self.finland.id])
        self.assertContains(self.client.get(url), 'Submit')
        response = self.client.post(url, self.form_data(
            self.finland, ipv4='198.51.100.7', out_of_service='on'))
        self.assertRedirects(response, '/sysop/')
        self.finland.refresh_from_db()
        self.assertEqual(self.finland.ipv4, '198.51.100.7')
        self.assertTrue(self.finland.out_of_service)
        self.assertEqual(list(self.finland.authorized_sysops.all()), [self.helper])

    def test_authorized_sysop_can_edit(self):
        self.client.force_login(self.helper)
        url = reverse('server_detail', args=[self.finland.id])
        response = self.client.post(url, self.form_data(self.finland, city='Tampere'))
        self.assertRedirects(response, '/sysop/')
        self.finland.refresh_from_db()
        self.assertEqual(self.finland.city, 'Tampere')

    def test_other_user_can_view_but_not_edit(self):
        self.client.force_login(self.stranger)
        url = reverse('server_detail', args=[self.finland.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Submit')
        response = self.client.post(url, self.form_data(self.finland, city='Hacked'))
        self.assertEqual(response.status_code, 200)
        self.finland.refresh_from_db()
        self.assertEqual(self.finland.city, 'Helsinki')

    def test_admin_only_fields_are_locked(self):
        self.client.force_login(self.owner)
        url = reverse('server_detail', args=[self.finland.id])
        response = self.client.post(url, self.form_data(
            self.finland, server_id='T2EVIL', hostname='evil',
            domain=self.firenet.id, city='Espoo'))
        self.assertRedirects(response, '/sysop/')
        self.finland.refresh_from_db()
        self.assertEqual(self.finland.server_id, 'T2FINLAND')
        self.assertEqual(self.finland.hostname, 'finland')
        self.assertEqual(self.finland.domain, self.aprs2)
        self.assertEqual(self.finland.city, 'Espoo')

    def test_invalid_input(self):
        self.client.force_login(self.owner)
        url = reverse('server_detail', args=[self.finland.id])
        response = self.client.post(url, self.form_data(self.finland, ipv4='not-an-ip'))
        self.assertContains(response, 'Enter a valid IPv4 address')
        self.finland.refresh_from_db()
        self.assertEqual(self.finland.ipv4, '192.0.2.1')


class AccountTests(PortalTestCase):
    def test_login_and_logout(self):
        response = self.client.post(
            '/accounts/login/', {'username': 'owner', 'password': 'pw'})
        self.assertRedirects(response, '/sysop/')
        self.assertContains(self.client.get('/sysop/'), 'Welcome, owner!')

        # logging out takes a POST
        self.assertEqual(self.client.get('/accounts/logout/').status_code, 405)
        response = self.client.post('/accounts/logout/')
        self.assertRedirects(response, '/accounts/login/')
        self.assertEqual(self.client.get('/sysop/').status_code, 302)

    def test_bad_login(self):
        response = self.client.post(
            '/login/', {'username': 'owner', 'password': 'wrong'})
        self.assertContains(response, 'Please enter a correct username and password')

    def test_profile(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get('/accounts/profile/'), 'Olivia')
        response = self.client.post('/accounts/profile/', {
            'first_name': 'Olga', 'last_name': 'Owner', 'email': 'olga@example.com'})
        self.assertRedirects(response, '/sysop/')
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.first_name, 'Olga')
        self.assertEqual(self.owner.email, 'olga@example.com')

    def test_password_change(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get('/accounts/password/change/').status_code, 200)
        response = self.client.post('/accounts/password/change/', {
            'old_password': 'pw',
            'new_password1': 'correct horse battery',
            'new_password2': 'correct horse battery',
        })
        self.assertRedirects(response, '/accounts/password/change/done/')
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password('correct horse battery'))

    def test_password_reset(self):
        self.assertEqual(self.client.get('/accounts/password/reset/').status_code, 200)
        response = self.client.post(
            '/accounts/password/reset/', {'email': 'owner@example.com'})
        self.assertRedirects(response, '/accounts/password/reset/done/')
        self.assertEqual(len(mail.outbox), 1)
        link = [line for line in mail.outbox[0].body.splitlines()
                if '/accounts/password/reset/' in line][0]
        path = '/' + link.split('/', 3)[3]

        response = self.client.get(path, follow=True)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(response.request['PATH_INFO'], {
            'new_password1': 'correct horse battery',
            'new_password2': 'correct horse battery',
        })
        self.assertRedirects(response, '/accounts/password/done/')
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password('correct horse battery'))


class ValidationTests(PortalTestCase):
    def test_reserved_hostname(self):
        ReservedHostname.objects.create(hostname='ns1', domain=self.aprs2)
        with self.assertRaisesMessage(ValidationError, 'ns1.aprs2.net is a reserved hostname.'):
            Server(hostname='ns1', domain=self.aprs2).full_clean()
        with self.assertRaisesMessage(ValidationError, 'ns1.aprs2.net is a reserved hostname.'):
            Rotate(hostname='ns1', domain=self.aprs2).full_clean()
        # reserved in one domain only
        Server(hostname='ns1', domain=self.firenet).full_clean()

    def test_server_cannot_shadow_rotate(self):
        with self.assertRaisesMessage(ValidationError, 'euro.aprs2.net is a rotate.'):
            Server(hostname='euro', domain=self.aprs2).full_clean()

    def test_rotate_cannot_shadow_server(self):
        with self.assertRaisesMessage(ValidationError, 'finland.aprs2.net is a server.'):
            Rotate(hostname='finland', domain=self.aprs2).full_clean()

    def test_hostname_syntax(self):
        for hostname in ('-leading', 'trailing-', '12345', 'under_score', 'dot.ted', 'a' * 64):
            with self.subTest(hostname=hostname):
                with self.assertRaises(ValidationError):
                    Server(hostname=hostname, domain=self.aprs2).full_clean()
        Server(hostname='ok-name2', domain=self.aprs2).full_clean()

    def test_domain_syntax(self):
        for domain in ('nodot', '-bad.net', 'bad-.net', 'x.y_z'):
            with self.subTest(domain=domain):
                with self.assertRaises(ValidationError):
                    Domain(domain=domain).full_clean()
        Domain(domain='example.org').full_clean()

    def test_deleting_owner_keeps_server(self):
        self.owner.delete()
        self.finland.refresh_from_db()
        self.assertIsNone(self.finland.owner)


class AdminTests(PortalTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin = User.objects.create_superuser('admin', 'admin@example.com', 'pw')

    def test_changelists_and_forms(self):
        self.client.force_login(self.admin)
        for url in (
            '/admin/',
            '/admin/sysop/server/',
            '/admin/sysop/server/?q=finland',
            '/admin/sysop/server/%d/change/' % self.finland.id,
            '/admin/sysop/rotate/',
            '/admin/sysop/rotate/?o=3',
            '/admin/sysop/rotate/%d/change/' % self.rotate.id,
            '/admin/sysop/domain/',
            '/admin/sysop/reservedhostname/',
            '/admin/auth/user/',
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_rotate_server_count(self):
        self.client.force_login(self.admin)
        response = self.client.get('/admin/sysop/rotate/')
        self.assertContains(response, '<td class="field-server_count">3</td>', html=True)

    def test_non_staff_has_no_admin_link(self):
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get('/sysop/'), '/admin/')
        self.client.force_login(self.admin)
        self.assertContains(self.client.get('/sysop/'), '/admin/')
