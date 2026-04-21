# Orbita Social

## What This Is

Self-hosted Social-Media-Management-Suite für den Buchverlag Orbita Media. Basiert auf dem Open-Source-Projekt BrightBean Studio (Django 5.1) — gefork't nach `Orbita-Media/brightbean-studio`, gebrandet als "Orbita Social", deployed auf Coolify. Verwaltet Social-Media-Posts, OAuth-verbundene Accounts, Scheduling, Approvals, Inbox und Medien-Library für Instagram, Facebook, LinkedIn, TikTok, YouTube, Pinterest, Threads, Bluesky, Google Business und Mastodon. Zielgruppe: Noah (Admin) + Mitarbeiter/Freelancer (eingeladen per E-Mail).

## Core Value

Ein einziger Ort, an dem Noah und sein Team fertige Social-Media-Posts auf alle relevanten Plattformen schedulen und posten können — ohne pro Plattform manuell zu arbeiten und ohne wiederkehrende SaaS-Gebühren.

## Requirements

### Validated

- ✓ Deployment läuft auf Coolify (App `xos84sccocw488o8kccow88g`) — `/health/` 200, alle Container healthy
- ✓ Cloudflare DNS proxied Domain → Hetzner 5.75.158.30
- ✓ Postgres + django-background-tasks laufen
- ✓ Django-allauth Auth funktioniert (Login-Seite lädt)

### Active

- [ ] **REBRAND-01**: UI-Branding ist "Orbita Social" statt "BrightBean" (Logo, Page-Titles, Email-Templates, Base-Template)
- [ ] **DOMAIN-01**: Produktiv-Domain ist `https://social.orbita-media.de` statt `brightbean.orbita-media.de`
- [ ] **SEC-01**: Cloudflare Bot-Fight-Mode + Security-Level medium für die neue Domain
- [ ] **SEC-02**: Cloudflare WAF-Rule blockt Zugriffe auf Django-Admin (`/django-admin/*`) außer von Tailscale-Netz 100.64.0.0/10
- [ ] **DASH-01**: Orbita Media Dashboard zeigt eine Kachel/Link zu `https://social.orbita-media.de`
- [ ] **ADMIN-01**: Noahs Admin-Account existiert (Django-User, `is_superuser=true`, `is_staff=true`), Signup ist zugeschwiegen weiterhin offen
- [ ] **INVITE-01**: Mitarbeiter können mit beliebiger E-Mail-Adresse eingeladen werden, bekommen Invite-Mail (via SES) und loggen sich via django-allauth ein (Verify-Flow funktioniert)
- [ ] **CLEAN-01**: Alter DNS-Record `brightbean.orbita-media.de` bleibt vorerst als Redirect/301 auf `social.orbita-media.de`, wird nach 7 Tagen entfernt

### Out of Scope

- Rename des internen Django-Package `brightbean/`/`config/`-Strukturen — nur UI-Branding, nicht Code-Refactoring (Risiko/Nutzen stimmt nicht)
- Eigene Authentifizierung statt django-allauth — das eingebaute System reicht für Team-Betrieb
- Cloudflare Access Zero Trust vor allem — wurde verworfen wegen externen Mitarbeitern
- Next.js-Integration als Iframe/Route — BrightBean bleibt eigenständige Subdomain (wie DittoFeed)

## Context

- **Tech-Stack**: Django 5.1, django-allauth, django-htmx, Alpine.js, Tailwind 4, Postgres 16, Gunicorn, Whitenoise, django-background-tasks (kein Redis)
- **Deployment**: Coolify auf Hetzner (5.75.158.30), Docker-Compose mit 5 Services (app, worker, migrate, maintenance, postgres), Traefik als Reverse-Proxy
- **Repo**: `Orbita-Media/brightbean-studio` (Fork von `brightbeanxyz/brightbean-studio`), main-Branch, Auto-Deploy via GitHub-Webhook
- **Secrets**: SECRET_KEY, ENCRYPTION_KEY_SALT, POSTGRES_PASSWORD in Coolify ENVs (is_literal=false)
- **Mail**: Amazon SES eu-north-1 über `noreply@orbita-media.de` — SMTP-User/Passwort noch nicht gesetzt
- **Dashboard**: Das Orbita Media Dashboard ist ein separates Tool (Coolify-App `qok404c8oowc8gc4gwgw4kw0`) — ich muss dort eine Kachel einfügen, Code liegt lokal in `C:\Users\nmdar\Nextcloud\TOOLS\Dashboard Orbita Media\` (zu prüfen)

## Constraints

- **Tech**: Django darf nicht tief gepatched werden — wir bleiben nah am Upstream für einfache Updates. Branding nur über Templates + Config + Assets, nicht via Code-Rename.
- **Domain**: Muss public HTTPS bleiben für OAuth-Callbacks von Meta/LinkedIn/TikTok/Google/Pinterest — Tailscale-only nicht möglich
- **Secrets**: Keine OAuth-App-Credentials hardcoden — werden im Admin-UI von BrightBean gesetzt (pro Plattform)
- **Mail**: SES SMTP-Credentials müssen gesetzt sein BEVOR Invite-Flow getestet wird

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| BrightBean Fork statt Eigenbau | Fertige Provider für 10 Plattformen, RBAC, Invites, Inbox — wäre sonst viel Eigenentwicklung | ✓ Good (deployed, läuft) |
| Name "Orbita Social" | Konsistent mit "Orbita Media Dashboard", beschreibt die Funktion | — Pending (vom User noch zu bestätigen) |
| Eigene Subdomain `social.orbita-media.de` | Django-Auth braucht eigene Session-Cookies, Iframe in Next.js wäre fragil | — Pending |
| Kein CF Access | Externe Mitarbeiter sollen eingeladen werden — BrightBean-Invite reicht | — Pending |

---
*Last updated: 2026-04-21 after initialization*

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state
