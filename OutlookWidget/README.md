# Outlook widgets for Tesserae

Your Outlook calendar on an e-ink panel, read live from Microsoft Graph.

Tesserae's bundled `calendar_*` widgets can already read an Outlook **published
iCal URL**, but Microsoft caches those feeds (updates lag hours) and iCal carries
no response status, no free/busy, and no categories. These plugins sign in to
Graph instead, so the panel shows the private calendar as it actually is.

| Folder | Kind | What it is |
| --- | --- | --- |
| `outlook_core/` | `data` | Sign-in, token handling, calendar list, caching. No widget of its own. |
| `outlook_week/` | `widget` | The next N days grouped by day. |

Everything else at the repo root is development scaffolding: `devserver.py`,
`conftest.py`, `_devsupport.py`, `_tests/`, `_docs/`. The underscore prefixes are
load-bearing — Tesserae treats every other folder here as a plugin.

## Status

Structure and data layer are done and tested. `outlook_week`'s `client.js` is
still a placeholder list: the real per-size layout is the next step.

## Setup

### 1. Register an app in Microsoft Entra

You need your own app registration; there is no shared client ID to ship.

In the [Entra admin center](https://entra.microsoft.com/), go to **Identity →
Applications → App registrations → New registration**. Give it a name, pick the
account types you sign in with, and leave the redirect URI empty.

Creating the registration is not enough on its own. The next two settings are
**not** part of the New registration form — you set them afterwards, from the
left-hand menu of the registration you just created:

| Setting | Where to find it | Value |
| --- | --- | --- |
| Public client flows | **Authentication** → *Advanced settings* → "Allow public client flows" | **Yes**. The device-code flow will not start without it. |
| Graph permissions | **API permissions** → *Add a permission* → **Microsoft Graph** → **Delegated permissions** | `Calendars.Read`, `offline_access`, `User.Read` |
| Shared calendars (optional) | same page | `Calendars.Read.Shared` — only if you want calendars **other people** own; see step 2 |

`Calendars.ReadBasic` is not enough — it omits location, categories, `showAs`
and `responseStatus`. If your tenant requires admin consent, an administrator
has to press **Grant admin consent** on that same page before sign-in works.

Then open **Overview** and copy two values from the *Essentials* panel at the
top. Neither is a secret:

- **Application (client) ID**
- **Directory (tenant) ID** — needed in step 2 if the registration is single-tenant.

### 2. Configure Tesserae

In Tesserae, open **Settings → Plugins → Outlook Core**
(`/settings/plugins#outlook_core`):

| Field | What to enter |
| --- | --- |
| Entra application (client) ID | The **Application (client) ID** from the registration's Overview page. |
| Tenant | Has to match the registration's account types — see below. |
| Read calendars shared with me | Leave **off** unless you need calendars someone else owns. See below. |

*Tenant* must agree with the **Supported account types** you chose when
registering (app registration → **Authentication** → *Supported account types*):

| Supported account types | Tenant |
| --- | --- |
| This organizational directory only (single tenant) | The **Directory (tenant) ID** GUID, or a verified domain such as `contoso.com` |
| Any organizational directory | `organizations` |
| Any organizational directory + personal Microsoft accounts | `common` |
| Personal Microsoft accounts only | `consumers` |

Leaving *Tenant* at its `common` default against a **single-tenant**
registration fails at sign-in with `AADSTS50059: No tenant-identifying
information found` — Entra has no way to tell which directory to authenticate
against. Pin the GUID instead.

#### Calendars other people own

Your own calendars — including ones you created and shared *outward* — work with
`Calendars.Read` alone. Reading a calendar **someone else owns** and shared with
you additionally needs `Calendars.Read.Shared`, which is what the *Read calendars
shared with me* setting requests.

It is off by default on purpose. The Microsoft v2.0 endpoint has no partial
consent: if a tenant won't grant a requested scope, the **entire sign-in fails**
rather than returning a narrower token. Asking for `Calendars.Read.Shared`
unconditionally would therefore lock out anyone whose organisation requires
administrator approval for it, to give a capability most installs never use.

So: turn it on only if you need it, and grant the matching permission in Entra.
If sign-in then fails with `AADSTS65001` or `AADSTS90094`, either turn it back
off or ask an administrator to consent. The **Plugins → Outlook** page reports
which of the two states you are in under *Shared calendars*.

The setting takes effect at the next **sign-in**, not immediately — a token
carries the scopes it was issued with, so toggling it on means signing out and
back in. (Refreshes deliberately re-request the scopes the stored token already
has, so a widened setting can never invalidate a working sign-in.)

### 3. Sign in

1. Open **Plugins → Outlook** (`/plugins/outlook_core/`) and press *Sign in with
   Microsoft*. Enter the code it shows at <https://microsoft.com/devicelogin>,
   then come back and press *Check sign-in*.
2. Drop **Outlook, Week Ahead** onto a dashboard and pick which calendars it
   should show (empty = your default calendar).

Only the refresh token is stored, in `data/plugins/outlook_core/token.json`.
*Sign out* deletes it.

## Development

Requires a clone of [dmellok/tesserae](https://github.com/dmellok/tesserae). Set
`TESSERAE_REPO` if it is not at `C:\Users\mjean\Documents\Sources\Forks\tesserae`.

One-time setup, in the clone:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
.\.venv\Scripts\python -m playwright install chromium   # panel renders / screenshots only
```

Run the dev server from this folder. It starts the clone's server with this repo
added as a plugin scan dir, so nothing is copied or linked into the clone:

```powershell
<tesserae-clone>\.venv\Scripts\python devserver.py
```

Then sign in at <http://127.0.0.1:8765/> and iterate at
<http://127.0.0.1:8765/_test/render?plugin=outlook_week&size=md>
(`size=xs|sm|lg` for the other cell sizes).

Tests and lint — every Python module here is covered, and nothing reaches the
network (`conftest.py` swaps out `urllib.request.urlopen` for every test):

```powershell
<tesserae-clone>\.venv\Scripts\python -m pytest . -q
<tesserae-clone>\.venv\Scripts\ruff check .
```

## How the data flows

```
outlook_week.fetch()  →  PLUGIN_REGISTRY  →  outlook_core.load_events_detailed()
                                                   ↓
                                       token.json → access token (refreshed as needed)
                                                   ↓
                                  GET /me/calendars/{id}/calendarView   (10-min disk cache,
                                  ?startDateTime=…&endDateTime=…         stale served on outage)
                                                   ↓
                              normalised events → grouped by day → ctx.data → client.js
```

`calendarView` expands recurrences server-side, so there is no RRULE handling
here. Cancelled events and events you declined are filtered out in the core, so
every future `outlook_*` widget agrees about what a panel should show.

## Publishing

Ships through the [community catalog](https://docs.tesserae.ink/dev/publishing-a-widget/)
as a bundle (`folders: ["outlook_core", "outlook_week"]`): tag a release, take the
tarball's sha256, and PR the entry to `dmellok/tesserae-widgets`. Note the tarball
must expose the plugin folders as direct children.
