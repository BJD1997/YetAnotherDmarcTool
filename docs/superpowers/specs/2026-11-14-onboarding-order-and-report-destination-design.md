# Onboarding in dependency order, report destination you choose

Status: design approved in chat on 2026-11-14, waiting for spec review.
Delivered as one pass in v0.3.0-beta21. No database changes.

## Why

Found while testing Google sign-in and a Google Workspace mailbox on a fresh
organization (break-glass admin, Settings first, wizard later as a second
admin):

1. Settings → Domains only has an add form: a new domain seems to vanish and
   its verification record is only on the Domains page.
2. Settings → Mailbox refuses to save without a verified domain, and only
   says so after Save.
3. The wizard created a hosted address on its own, and the backend then
   preferred it over the organization's own (Google) mailbox. The DMARC
   builder showed that hosted address twice.
4. The wizard still assumes "no Microsoft sign-in means hosted mailbox":
   resume logic, mailbox step text (IMAP only, no Google), `has_mailbox`.
5. The wizard order (Mailbox before Domain) doesn't match the real
   dependencies: a mailbox and every provider's test sign-in need a verified
   domain (`sign_in_settings.py`, `mailbox_connections.py`).
6. The verification host name and TXT value have no copy buttons.

## Decisions (2026-11-14)

| Question | Decision |
|---|---|
| Wizard order | Domain + verify before sign-in, since every provider's test sign-in needs a verified domain. |
| Sign-in step skippable | Yes: "Only local accounts for now". A provider can be added later in Settings → Sign-in. |
| Mailbox step | Two explicit choices: own mailbox (Microsoft 365, Google Workspace, IMAP) or a hosted address. Nothing is created until one is picked; the step can't be passed without one. Hosted is only offered when the server has a hosted mailbox and the org may use it (today's `_hosted_mailbox_available` rule, unchanged: always without a Microsoft tenant, opt-in with one). |
| Own mailbox and hosted address both present | The own mailbox is the report destination. |
| Removing a hosted address | Possible, but only while an own mailbox is connected. |

## A. Wizard

New order:

| # | Step | Done when |
|---|---|---|
| 1 | Welcome | (always shown first for an org that has done nothing) |
| 2 | Domain + verify | a verified apex domain exists |
| 3 | Sign-in | `sign_in_provider != none`, or step 4 is done (covers "Only local accounts for now" without storing the skip) |
| 4 | Report mailbox | a mailbox connection exists, or any domain has a hosted address |
| 5 | DNS baseline | a DNS check exists |
| 6 | Reporting (rua=) | (informational) |
| 7 | AI help | (informational) |
| 8 | Done | reaching it completes the wizard, as today |

- Step 2 merges today's Domain and Verify steps: add form, then the TXT
  record with copy buttons and "Verify now", live-updating while pending.
- Step 3 reuses the Settings → Sign-in provider UI (Microsoft, Google
  Workspace, OIDC: test sign-in, then save) plus "Only local accounts for
  now", which just moves on.
- Step 4 shows the two choices. "Use my own mailbox" opens the same
  `MailboxConnectionSection` as Settings (Microsoft 365 / Google Workspace /
  IMAP). "Use a hosted address" calls the existing
  `POST /domains/{id}/hosted-report-address` for the focus domain and shows
  the address. `HostedAddressPreview`'s automatic generation is removed.
- Resume: `deriveStep` uses the "Done when" column only; `entra_tenant_id`
  no longer decides anything. Whoever opens the wizard (break-glass or a
  later admin) lands on the first unfinished step; earlier steps show as
  done.
- Backend `/onboarding/status`: `has_mailbox` becomes "connection exists
  or any domain has a hosted address" (no longer true just because the org
  has no Entra tenant); adds `has_sign_in_provider`.
- Copy no longer says IMAP is the only own-mailbox option, and no longer
  promises an automatic hosted address.

## B. Report destination

One helper, `report_destination(domain, connection) -> str | None`: the
connected mailbox's address if the org has one, else the domain's hosted
address, else none. Used by all four places that copy the rule today:
`dmarc_reports.py` (DMARC builder), `dns_checks.py` (rua check and TLS-RPT
builder), `scheduled_recheck.py`.

- rua check: `correct` when the published `rua=` includes the destination
  or the domain's hosted address (YADT processes both).
- DMARC and TLS-RPT builders: the destination is shown once. A hosted
  address that isn't the destination is shown once more as an optional,
  unticked extra ("also processed by YADT"). No duplicate row.
- `DELETE /domains/{id}/hosted-report-address` (org admin): 409 unless the
  org has a mailbox connection; clears `hosted_report_address` and calls
  `release_authorization_records({domain.name})`. Reports already received
  stay; later mail to the old address is skipped by the hosted poll (as
  for any unknown recipient). The builders show "Remove hosted address"
  only in that situation.

## C. Settings fixes

- Settings → Domains: under the add form, every domain still pending
  verification is listed with its host name and TXT value (copy buttons)
  and "Verify now"; a link to the Domains page for the full list.
- Settings → Mailbox: with no verified domain, the forms are replaced by
  "Verify a domain first" with a link to Settings → Domains.
- Copy buttons (existing `useClipboardFeedback`) for the verification host
  name and value in the wizard, Settings → Domains and the Domains page.

## Testing

- Backend: `report_destination` (own wins, hosted fallback, none); rua
  check accepts either; DELETE hosted address (409 without a connection,
  clears the field, releases the record); `/onboarding/status` fields.
- Frontend: `deriveStep` for each state (incl. skipped sign-in, Settings
  done first); mailbox step creates nothing until a choice; builder shows
  no duplicate and the remove button only with a connection.
- Live check on the local stack with test data, desktop and phone width:
  fresh org through the wizard; and the reported path (Settings first,
  then the wizard as a second admin).
