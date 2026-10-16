# v0.3.0: sign-in with Google and OIDC, report source of your choice

Status: design, waiting for review. Delivered in three betas (A, B, C), each
usable on its own.

## Decisions (2026-10-16)

| Question | Decision |
|---|---|
| Who sets up an organization's own OIDC provider | The platform admin (when creating the org) and the org admin (Settings → Sign-in). Saving a provider only succeeds after a successful test sign-in. |
| Providers on a private address (10.x, 192.168.x, ...) | Blocked by default, like IMAP. `OIDC_ALLOW_PRIVATE_ISSUERS=true` allows it, for servers where every org is trusted. |
| Local accounts after an org gets a provider | Keep working next to the provider. Org admins can add local teammates in every org. |
| Google Workspace mailbox | One service account for the server, with domain-wide delegation granted by the customer's Workspace admin. |
| IMAP with OAuth2 | Not in v0.3.0; app passwords cover it. |

## A. Sign-in

### Per organization

An organization has **local accounts** (always) and at most **one sign-in
provider**:

| Provider | Server setup (env) | Organization setup |
|---|---|---|
| Microsoft | `ENTRA_SSO_CLIENT_ID/SECRET` (as today) | Entra tenant ID (as today) |
| Google Workspace | `GOOGLE_SSO_CLIENT_ID/SECRET` (new) | Workspace domain, e.g. `example.com` |
| Other OIDC | none | issuer URL, client ID, client secret |

Data:

- `organizations.sign_in_provider`: `none | microsoft | google | oidc`.
  Migration: `microsoft` where `entra_tenant_id` is set, else `none`.
- `organizations.google_workspace_domain` (String 253, unique): one
  organization per Workspace. When the org admin sets it, it must be one of
  the organization's verified domains; the platform admin may set it ahead
  of verification.
- `organizations.oidc_issuer` (String 2048), `oidc_client_id` (String 255),
  `oidc_client_secret` (EncryptedSecret, like the IMAP password).
- `organizations.slug` (String 63, unique, lowercase `[a-z0-9-]`): for the
  direct link `/login/<slug>`. Generated from the name, editable by the
  platform admin.
- `users.auth_method` gains `google` and `oidc`; `users.external_subject`
  (String 255): the provider's stable user ID (`sub`). Unique per
  (organization, auth_method, external_subject). Microsoft users keep
  `entra_object_id`.
- `sign_in_events.auth_method` gains the same values.

Places that today use "has an Entra tenant" to mean "not a local org" (Team,
local teammates, hosted mailbox availability, wizard) change to: local
accounts are always possible; Microsoft-specific things (admin consent,
tenant ID) look at `sign_in_provider == microsoft`.

### Flows

All three share one finish step after the token is checked: find the user
by (org, method, subject), or create them (org admin only if the email is
the org's expected admin, else member), refuse suspended orgs and disabled
users, record the sign-in event, create the session. Microsoft moves onto
this shared step; its own token checks stay as they are.

**Google.** Authorization code flow with PKCE, state and nonce cookies (as
Microsoft today), issuer `https://accounts.google.com`, scope
`openid email profile`, `hd=<workspace domain>` as a hint. The ID token is
accepted only when the signature, issuer, audience, expiry and nonce check
out, `email_verified` is true, and the `hd` claim (set by Google, only for
Workspace accounts) matches an organization's `google_workspace_domain`.
Personal Gmail accounts have no `hd` and are refused.

**Other OIDC.** Same flow against the org's issuer, using its discovery
document (`/.well-known/openid-configuration`): authorization, token and
JWKS endpoints. The organization goes into the state cookie when the
sign-in starts; on return the token is checked against **that**
organization's settings, never against something the response says:

- signature from the issuer's JWKS, `iss` equal to the configured issuer,
  `aud` containing the client ID, expiry, nonce;
- an `email` claim, `email_verified` not false, and the email's domain one
  of the organization's **verified** domains (or a subdomain of one).

All outgoing requests to the issuer (discovery, JWKS, token) go through the
same public-address check as IMAP (`resolve_public_address`), unless
`OIDC_ALLOW_PRIVATE_ISSUERS=true`. HTTPS only. Discovery and keys are cached
for an hour.

**Test before save.** Settings → Sign-in (org admin) and the organization
dialog (platform admin) have "Test sign-in": it runs the flow in a popup
with the entered settings (held server-side for 10 minutes, not saved) and
only then allows saving. The test checks everything except creating a
session.

### Login page

- **Sign in with Microsoft** / **Sign in with Google**: shown when the server
  has those apps set.
- **Sign in with your company account**: an email field. `POST
  /api/auth/discover {email}` looks up the email's domain among verified
  domains of organizations that have a provider. Public mailbox domains
  (the same list as the trusted-reporter guard) never match. A match
  returns where to go: the org's OIDC sign-in (with the email as
  `login_hint`), or the Microsoft/Google button's flow with the hint. No
  match: "There's no company sign-in for this email address" plus the other
  options. Rate-limited like the password login; the answer never names the
  organization.
- **Local accounts**: email + password + authenticator, as today.
- `/login/<slug>` skips the email step for that organization's provider.

## B. Microsoft 365 mailbox for every organization

The Microsoft mailbox no longer requires Microsoft sign-in.

- `mailbox_connections.ms_tenant_id` (UUID). Migration copies the org's
  `entra_tenant_id` into existing Microsoft connections.
- Polling, admin consent links and the sender check use the connection's
  tenant.
- Settings → Mailbox: "Microsoft 365" is offered to every org. Without a
  Microsoft sign-in, the org admin enters the tenant ID (or the tenant's
  domain, resolved through Microsoft's public discovery document) and then
  grants admin consent as today.

## C. Google Workspace mailbox

- Server: `GOOGLE_MAIL_SERVICE_ACCOUNT_FILE` (the service account's JSON key,
  mounted read-only, like other secrets). Its client ID is shown in
  Settings with the exact steps for the customer's Workspace admin: Security
  → API controls → Domain-wide delegation → add the client ID with scope
  `https://www.googleapis.com/auth/gmail.modify`.
- No new dependencies: the service account's token request is a signed JWT
  (PyJWT, already used) sent with httpx, impersonating the report mailbox.
- `mailbox_connections.kind` gains `google`; the mailbox address is the
  connection's address.
- Polling: unread messages in the inbox (Gmail API `users.messages.list`,
  `q=is:unread in:inbox`), fetched raw, processed like IMAP. After a report
  is stored: remove `UNREAD` and `INBOX`, add the label "DMARC processed"
  (created if missing) — the Gmail version of mark read and move. A
  non-report is only marked read.
- Sender check: Gmail stamps every incoming message with
  `Authentication-Results: mx.google.com`, so that ID is fixed; no
  confirmation step.
- "Test connection" lists the inbox once and shows the count, or the exact
  error (delegation not granted, wrong mailbox).

## Security notes

- Tokens are always checked against the organization chosen before the
  redirect, so a provider of organization X can never sign anyone into
  organization Y.
- Email-domain routing only uses verified domains, so nobody can route a
  domain they don't own to their provider.
- OIDC client secrets and the Google service account key never reach the
  browser; secrets are encrypted at rest (FERNET_KEY).
- New sign-in methods show up in the sign-in log with their method and
  failure reason (`token_invalid`, `email_domain_not_verified`,
  `hd_mismatch`, ...).

## Testing

- Backend: token checks with locally signed test tokens and a fake
  discovery/JWKS server (no real provider needed): wrong issuer, wrong
  audience, expired, nonce mismatch, unverified email, email on another
  domain, `hd` missing or wrong, org switched in the cookie. Routing: verified
  vs unverified domain, public domains, suspended org. Expected admin for
  each method. Private-address issuer refused unless allowed.
- Gmail: the poll against a fake Gmail API (labels, raw messages).
- Live: you test Google sign-in and an Authentik/Keycloak provider on the
  Azure walkthrough instance; I verify the flows in a browser against a
  local Keycloak container before each beta.

## Out of scope

IMAP OAuth2. Passkeys and the platform settings page (later versions).
