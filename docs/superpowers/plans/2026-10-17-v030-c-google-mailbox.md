# v0.3.0 part C: Google Workspace mailbox (organizations and the hosted mailbox) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Reports can be read from a Google Workspace mailbox through one service account for the server: as an organization's report mailbox, and as the hosted mailbox.

**Spec:** `docs/superpowers/specs/2026-10-16-v030-sign-in-and-report-sources-design.md` sections C and D.

## Decisions

- Server: `GOOGLE_MAIL_SERVICE_ACCOUNT_FILE` (path to the service account's JSON key) or `GOOGLE_MAIL_SERVICE_ACCOUNT_JSON` (its contents, for Azure/Key Vault and Portainer). The customer's Workspace admin grants domain-wide delegation to the service account's client ID with scope `https://www.googleapis.com/auth/gmail.modify`.
- No new dependencies: the token request is a JWT signed with PyJWT (RS256, `sub` = the mailbox) posted with httpx to the key's `token_uri`; Gmail REST API with httpx.
- **Ruling (security):** an organization's Google mailbox address must be on one of its **verified** domains (or a subdomain). Domain-wide delegation lets the service account act as any user of a Workspace that granted it; requiring a verified domain means no organization can point it at another organization's mailbox. No test sign-in needed.
- Reading: unread messages in the inbox (`q=is:unread in:inbox`, up to 500 per poll); raw message; after a report is stored: remove `UNREAD` and `INBOX`, add the label "DMARC processed" (created if missing); a non-report is only marked read. Sender check: Gmail's `Authentication-Results` ID is fixed, `mx.google.com`.
- Hosted mailbox: `HOSTED_REPORTS_SOURCE=google` reads `HOSTED_REPORTS_MAILBOX_ADDRESS` with the same service account; routed by recipient like IMAP.
- Migration 0045: `mailbox_kind` gains `google`.
- Azure template: Google mail service account JSON (Key Vault secret) and the hosted IMAP settings (password in Key Vault); one rebuild of `azuredeploy.json` with Bicep 0.47.16.

## Tasks

- [ ] **1. Gmail client (backend)**: `app/services/gmail/client.py` (service-account token with caching per mailbox, list unread, fetch raw, mark read, mark read and move with label creation, inbox count for the test); settings and parsing of the key (file or JSON); tests against an httpx MockTransport and a locally generated RSA key.
- [ ] **2. Organization Google mailbox (backend)**: migration 0045; `PUT /mailbox-connection/google {mailbox_address}` (tests the connection first, address on a verified domain), `POST /mailbox-connection/google/test`; poll job dispatch for `kind=google`; `/organizations/current` exposes `google_mailbox` (available + client ID for the delegation instructions); tests.
- [ ] **3. Hosted mailbox from Google (backend)**: `HOSTED_REPORTS_SOURCE=google`, readiness, poll path sharing `_store`, setup checks/tips; tests.
- [ ] **4. Frontend**: third mailbox choice "Google Workspace mailbox" with the delegation steps (client ID, scope), Test connection, Save; status shows the kind. Tests.
- [ ] **5. Deployment**: `.env.example`, Portainer compose, Azure template (Google mail key + hosted IMAP), rebuild `azuredeploy.json`.
- [ ] **6. Verify, docs, release**: full suites; wiki (Connecting a Google Workspace Mailbox page, Configuration Reference); release v0.3.0-beta15.
