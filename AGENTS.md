# AGENTS.md

Guidance for coding agents (and humans) working on this repository.

## What this is

`aprs2netportal` is the sysop portal for the APRS-IS Tier2 network (aprs2.net).
Server operators log in and maintain the records of their servers; admins
manage domains, rotates (DNS round-robin groups) and reserved hostnames through
the Django admin. The portal's machine-readable outputs are consumed by
external systems that generate the aprs2.net DNS zone and monitor the network:

| URL | View | Consumer-facing contract |
|---|---|---|
| `/servers.json` (also `/sysop/servers.json`) | `sysop.views.servers_json` | dict keyed by `server_id`; includes sysop email only for authenticated requests and only when `email_alerts` is on |
| `/rotates.json` | `sysop.views.rotates_json` | dict keyed by rotate FQDN, each with nested `servers` dict |
| `/APRServe2.txt` | `sysop.views.server_list` | legacy text server list for APRS client software; requires a rotate `rotate.aprs2.net` to exist |

**Treat the shape of these three outputs as a stable external API.** Do not
change keys, nesting or text layout without being asked to.

## Current state: legacy, not runnable on a modern system

The code was written in 2014 and has not been touched since. It targets:

- Python 2.7 (implicit relative imports, `print` statements, `unicode()`, `__unicode__`)
- Django 1.6 (`patterns()`, string view references, `MIDDLEWARE_CLASSES`,
  `TEMPLATE_*` settings, function-based auth views, `{% load staticfiles %}`,
  `{% url 'dotted.view.path' %}`, `user.is_authenticated()` as a method,
  `ForeignKey` without `on_delete`, `ModelForm` without `fields`)
- South for schema migrations (`sysop/migrations/*.py` are South migrations,
  not Django migrations)
- PostgreSQL via psycopg2 in production (committed settings say sqlite3 with an
  empty name; production edited `settings.py` in place)
- uWSGI + nginx + Upstart on the host (`deploy/`)
- `django_ssl_auth` (https://github.com/HamWAN/django-ssl-client-auth), an
  external, unmaintained package that is *not* in this repo; it was symlinked
  into the project root (see `.gitignore`) and provides TLS client certificate
  login (ARRL LoTW certificates, users auto-created from the cert DN)
- Bootstrap 3.1.1 and jQuery 1.11 loaded from public CDNs

There is no `requirements.txt`, no packaging metadata, no CI and no real tests
(`sysop/tests.py` is the Django 1.x placeholder). The only dependency list is
the `pip install` line in `deploy/README.md`.

An upgrade to current Django/Python, running in a container, is in progress on
the `feature/container` branch. **Update this file as that work lands** so it
keeps describing the code as it is.

## Layout

```
manage.py                    Django entry point
aprs2netportal/              Project package
  settings.py                Single settings module; DEBUG=True, hardcoded
                             SECRET_KEY, no env-based configuration
  urls.py                    Root URLconf: admin, auth/password views,
                             /APRServe2.txt, includes sysop.urls at both
                             /sysop/ and /
  wsgi.py                    WSGI entry point
sysop/                       The one real application
  models.py                  Domain, Server, Rotate, ReservedHostname
  views.py                   JSON/text exports + login-required HTML views
  forms.py                   SysopServerForm (locks admin-only fields), UserForm
  admin.py                   Admin registrations; Rotate<->Server inline
  urls.py                    App URLconf
  countries.py               ISO 3166 code -> name dict used as choices
  templatetags/sysop.py      {% servertable %} inclusion tag
  templates/sysop/           index, edit_server, servertable, APRServe2.txt
  migrations/                South migrations 0001-0008 (legacy format)
  management/commands/
    importt2sysop.py         One-off 2014 importer from the old MySQL T2SYSOP
                             database; dead code
  tests.py                   Placeholder
utils/                       Template helpers only (no models)
  templatetags/addcss.py     |addcss filter: adds a CSS class to a form widget
  templatetags/navigation.py {% active %} tag for sidebar highlighting
templates/                   Project-level templates
  base.html                  Bootstrap 3 dashboard layout, navbar, sidebar
  registration/              Login, password change/reset, profile
  admin/base_site.html       Admin branding override
  403.html
css/                         dashboard.css and favicon.ico; referenced via
                             {% static %} but not wired into STATICFILES_DIRS
                             (production copied/linked them into ./static)
deploy/                      2014 host install notes, nginx vhost, uWSGI ini,
                             Upstart job
```

## Data model

- **Domain** — a DNS domain (`aprs2.net`, `aprs.net`, `firenet.us`).
- **Server** — a Tier2 server or hub. `hostname` + `domain` form the FQDN;
  `server_id` is the APRS-IS server ID (e.g. `T2FINLAND`) and is the key in
  `servers.json`. Has `owner` (FK to `auth.User`) and `authorized_sysops`
  (M2M to `auth.User`), IPv4/IPv6 addresses, location, descriptive fields and
  the flags `deleted`, `out_of_service` and `email_alerts`.
- **Rotate** — a round-robin DNS name with an `eligible` M2M to Server.
  `regional` marks the primary regional rotates listed in `APRServe2.txt`.
- **ReservedHostname** — hostnames that may not be used for servers or rotates.

`Server.clean()` and `Rotate.clean()` enforce that a hostname is not reserved
and that servers and rotates do not share a name. Users are stock
`django.contrib.auth` users; there is no profile model.

## Behaviour worth knowing

- **Permissions:** any logged-in user can view any server record. Only the
  owner or an authorized sysop can save it (`sysop.views.server_detail`).
  `server_id`, `hostname` and `domain` are locked for sysops by
  `SysopServerForm` (disabled widgets plus `clean_*` methods returning the
  stored value); those fields are changed through the admin only. Note that a
  sysop *can* change `owner`, `deleted` and `authorized_sysops`.
- **Caching:** the three export views are cached for 30 minutes with
  `cache_page` and sent with no-cache headers to clients. `Server.save()` and
  `Rotate.save()` call `cache.clear()`. No `CACHES` setting exists, so this is
  the per-process local-memory cache: with several worker processes, the other
  workers keep serving stale output until their own entry expires. M2M changes
  that do not go through `save()` do not clear the cache at all.
- **`servers.json` varies by authentication** via the custom
  `cache_page_vary_authenticated` decorator (separate cache key prefix).
- Soft delete: `deleted` servers stay in `servers.json` (flagged) but are
  excluded from the HTML lists and `APRServe2.txt`.

## Known defects in the legacy code

Found during review; fix them as part of the modernisation rather than
preserving them.

- `settings.py` commits a `SECRET_KEY` and `DEBUG = True`. The committed key
  must be considered public; production needs a fresh one from the environment.
- `server_detail` uses `Server.objects.get()` — an unknown id is a 500, not a 404.
- `RotateAdmin.queryset()` uses the pre-1.6 hook name, so the `server_count`
  annotation is never applied.
- `{% active %}` returns the URL name itself as the CSS class when the link is
  not active, and compares against dotted view paths that never match.
- Redirects and several links are hardcoded paths (`/sysop/`, `/admin/`,
  `/sysop/server/<id>/`) instead of reversed URLs.
- `sysop.urls` is included twice (`/sysop/` and `/`), so every page has two URLs.
- `hostname_validator`'s message says "lowercase" but the regex accepts uppercase.
- `base.html` still carries the Bootstrap example's placeholder dashboard as
  default block content, and links to a Yahoo Group that no longer exists.
- `deploy/portal_nginx.conf` has no client-certificate configuration even
  though the settings enable `django_ssl_auth`.

## Conventions

- Do not add `Co-Authored-By` (or any other AI attribution) trailers to commits.
- Commit messages: short imperative sentence ending with a period, matching
  the existing history (`Add form errors to login page.`).
- 4-space indentation, PEP 8-ish Python; Django templates indented with spaces.
- Keep it a small, boring Django project: prefer stock Django features over
  new dependencies.
