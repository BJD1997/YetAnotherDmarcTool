# Onboarding a new customer

This is the actual current flow, kept in sync with the app rather than a
design-time plan — steps here are what the UI does, not what was originally
scoped.

## 1. Platform admin: create the organization

In the admin console (`/admin`), enter the customer's name and their **Entra
tenant ID** (the GUID for their Microsoft 365 / Azure AD tenant — found in
their Entra admin center under "Tenant properties"). Providing the tenant ID
up front activates the organization immediately.

If you don't have the tenant ID yet, you can still create the organization
with just a name (status stays `pending_setup`) and add the tenant ID later
from the same screen — nothing else is blocked on it except the customer
being able to sign in.

That's the only manual step on the platform-admin side. Everything from here
is self-service from the customer's own dashboard.

## 2. Customer: sign in

The customer's users go to the dashboard and sign in with "Sign in with
Microsoft". Microsoft handles SSO consent automatically inline at first
sign-in — there's no separate consent link to hand out for this part, unless
the customer's tenant is configured to block user consent entirely, in which
case an admin-consent link for the SSO app is available from the mailbox
setup screen (see below) and their Global Admin needs to click it once.

The first user to sign in for a newly-activated organization automatically
becomes that organization's `org_admin`.

## 3. Customer (org_admin): connect the mailbox

On first visit to the dashboard, a banner prompts for the shared mailbox
address that receives DMARC/TLS-RPT reports (e.g.
`dmarc-reports@customerdomain.com`). Entering it and clicking "Save & start
syncing" is enough to kick off the first sync attempt — consent is
self-attested at this point, so the banner will initially show green even
before the underlying Graph permissions are actually in place.

**This step alone is not sufficient for reports to actually flow in.** Two
more things need to happen on the customer's side, both surfaced as links
right there in the banner (click "Entra links" once the banner is up, or —
if the first sync fails — the same links appear automatically under the
sync-error message):

1. **Mail access consent** (required): the customer's Global Admin clicks
   the mail-access admin-consent link and approves it. This grants the
   app's Mail Access application (app-only, `Mail.Read`) permission in
   their tenant.
2. **Exchange Application Access Policy** (required, done outside this app):
   the Global Admin runs, in Exchange Online PowerShell, something like:

   ```powershell
   New-DistributionGroup -Name "DMARC Dashboard Mailbox Access" -Members dmarc-reports@customerdomain.com
   New-ApplicationAccessPolicy -AppId <mail-access-app-id> `
     -PolicyScopeGroupId "DMARC Dashboard Mailbox Access" -AccessRight RestrictAccess `
     -Description "Restrict DMARC Dashboard's Graph token to the DMARC reports mailbox only"
   Test-ApplicationAccessPolicy -AppId <mail-access-app-id> -Identity dmarc-reports@customerdomain.com
   ```

   This is the step that actually matters for security: without it, an
   app-only Graph token for `Mail.Read` can read *any* mailbox in the
   tenant, not just the shared one. `Test-ApplicationAccessPolicy` should
   report access granted for the shared mailbox — that's the sign the
   policy is scoped correctly.

If the customer sets the mailbox address before completing these two steps
(a common ordering, since it's the more discoverable action), the first sync
will fail. That's expected — the dashboard shows the same consent-guidance
links directly next to the sync error, with no dead end.

## 4. Confirm the sync actually worked

Once both consent steps are done, click "Resync now" (or wait for the next
scheduled poll, every 10 minutes). The mailbox banner should turn green with
a recent "last synced" timestamp. As platform admin, `/admin/job-runs` shows
every mailbox-poll attempt across all organizations — a `success` row for
this org's `mailbox_poll` job confirms it end-to-end, and doubles as
confirmation that the Application Access Policy is scoped correctly (a
mis-scoped policy fails here, not silently).

## 5. Customer (org_admin): add domains

Add each domain/subdomain to monitor. Before any best-practice checks run
against it, the domain must prove DNS control:

1. Click "How to verify" on the newly-added domain to get a TXT record name
   (`_dmarc-dashboard-verify.<domain>`) and a token value.
2. Publish that TXT record in the domain's real DNS.
3. Click "Check now" — once the record resolves, the domain flips to
   `verified` and checks start running.

A subdomain of an already-verified apex domain inherits verification
automatically and doesn't need its own token.

## 6. Customer (org_admin): add DKIM selectors

DKIM selectors aren't discoverable via DNS alone, so they have to be added
manually (whatever selector the customer's mail provider uses — check their
provider's DKIM setup docs, e.g. `selector1`/`selector2` for Microsoft 365,
`google` for Google Workspace). Without at least one selector added, the
DKIM check just reports "no selectors registered" rather than a real result.

## 7. Review findings, fix DNS

Once verified (and with selectors added), SPF/DKIM/DMARC/DMARCbis/MX/
MTA-STS/DANE/TLS-RPT checks run automatically on a schedule, or on-demand via
"Recheck". **This app is read-only and advisory toward the customer's DNS —
it never writes records on their behalf.** Every finding is a
recommendation; the customer updates their own authoritative DNS (at their
registrar or DNS host) to act on it.

---

## Platform-admin login, separately

None of the above requires the platform admin to log in more than once (to
create the org). For reference, platform-admin auth itself works two ways:

- **Local email + password** (the break-glass account, bootstrapped at
  deploy time) — has a "Change password" option in the admin console.
- **Via your own operator organization's Entra login**, if that organization
  is flagged as the operator org — sign in normally as an `org_admin` of
  that org and you land in the same admin console.
