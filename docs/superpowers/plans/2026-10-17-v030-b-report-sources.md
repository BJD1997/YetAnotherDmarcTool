# v0.3.0 part B: hosted mailbox over IMAP, Microsoft 365 mailbox for every organization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** The server's hosted reporting mailbox can be read over IMAP as well as from Microsoft 365, and any organization can connect a Microsoft 365 report mailbox, not only those that sign in with Microsoft.

**Spec:** `docs/superpowers/specs/2026-10-16-v030-sign-in-and-report-sources-design.md` sections B and D (the Google parts of D come with part C).

## Decisions

- Hosted mailbox: `HOSTED_REPORTS_SOURCE=microsoft|imap` (default `microsoft`); IMAP settings `HOSTED_REPORTS_IMAP_HOST`, `_PORT` (993), `_SECURITY` (`ssl`|`starttls`), `_USERNAME`, `_PASSWORD`, `_FOLDER` (`INBOX`), `_PROCESSED_FOLDER` (`DMARC processed`), `_AUTHSERV_ID`. The platform admin sets them, so a private address is allowed. Without `_AUTHSERV_ID` nothing is read; the poll records the detected ID in its error so the platform admin can fill it in. Routing by recipient (`To`, then `Delivered-To`/`X-Original-To`) as for Microsoft. Reports are marked read and moved like an organization's IMAP mailbox.
- Microsoft 365 mailbox for an organization without Microsoft sign-in (ruling, security): the org admin proves the tenant with a **test sign-in with Microsoft**; the email must be on one of the organization's verified domains and the tenant comes from the signed token (as for switching sign-in in A2). A tenant already used by another organization (its sign-in or its mailbox) is refused, so no organization can read another's mailbox through a shared tenant consent. Organizations that sign in with Microsoft keep using their own tenant without a test. Needs the server's Microsoft sign-in app (for the test) and mail app.
- Mail-access consent links use the mailbox's tenant when the organization has none of its own.

## Tasks

- [x] **1. Hosted IMAP (backend)**: config fields + `hosted_mailbox_ready` per source; `resolve_address(settings, allow_private=False)`; hosted poll job split into routing/storing helpers with a Microsoft and an IMAP reader; setup checks and tips per source; `.env.example`, Portainer compose. Tests: routing by `To`/`Delivered-To`, unmatched mail only marked read, missing authserv ID reports the detected one, sender check uses the configured ID, half-configured IMAP flagged.
- [x] **2. Microsoft mailbox tenant proof (backend)**: `PendingSignIn.purpose` (`sign_in`|`mailbox`, in the ticket digest); `POST /mailbox-connection/microsoft/test` (org admin) starts the Microsoft test; `PUT /mailbox-connection` takes an optional `ticket` and uses the ticket's tenant for organizations without Microsoft sign-in; tenant uniqueness across organizations' sign-in and mailboxes; `/organizations/current` exposes `microsoft_mailbox_available` and mail-consent links for the mailbox tenant. Tests: an organization without Microsoft sign-in connects after a test; without a ticket it's refused; another organization's tenant is refused; a sign-in ticket doesn't work for the mailbox and vice versa.
- [x] **3. Frontend**: Mailbox section offers Microsoft 365 to every organization when the server has it; for organizations without Microsoft sign-in a "Test sign-in with Microsoft" step before the mailbox address, then the consent links. Tests.
- [x] **4. Verify, docs, release**: full suites; wiki (Configuration Reference hosted IMAP, Connecting an IMAP Mailbox/hosted note, mailbox docs); release v0.3.0-beta14.
