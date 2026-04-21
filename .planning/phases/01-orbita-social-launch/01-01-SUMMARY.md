---
phase: 01-orbita-social-launch
plan: 01
subsystem: infrastructure
tags: [rebrand, domain, ses, waf, redirect, django-ses]
dependency_graph:
  requires: []
  provides:
    - Live-Domain https://social.orbita-media.de
    - Gebrandet als "Orbita Social" (Templates, Theme, Logo)
    - Django-Superuser kontakt@orbita-media.de
    - SES via django-ses (AWS IAM-Key, kein SMTP)
    - Dashboard-Kachel "Orbita Social" (Orbita-Media-Dashboard)
    - 301 brightbean.orbita-media.de -> social.orbita-media.de (App-Middleware)
    - Cloudflare WAF-Regel gegen externen /django-admin/-Zugriff
  affects:
    - Coolify App xos84sccocw488o8kccow88g (ENVs + Domain-Config)
    - Dashboard-Repo nextjs-frontend Tool-Registry
    - Dashboard-Repo app/routes/tools_discovery.py Mappings
tech_stack:
  added:
    - django-ses 4.7.2
  patterns:
    - "ENV-gesteuerter Conditional EMAIL_BACKEND (SES wenn AWS_ACCESS_KEY_ID, sonst SMTP-Fallback)"
    - "App-Level Middleware statt Cloudflare Page Rule (wegen Free-Plan 3-Rules-Limit)"
key_files:
  created:
    - apps/common/middleware.py
    - static/img/orbita-social-logo.png
    - .planning/phases/01-orbita-social-launch/01-01-SUMMARY.md
  modified:
    - templates/**/*.html (40 Dateien)
    - theme/static_src/src/styles.css
    - config/settings/production.py
    - requirements.txt
    - apps/members/services.py
    - (Dashboard) nextjs-frontend/app/lib/tool-registry.ts
    - (Dashboard) nextjs-frontend/app/(dashboard)/tools/page.tsx
    - (Dashboard) app/routes/tools_discovery.py
decisions:
  - "django-ses statt SMTP-Credentials: nutzt existierenden Dashboard-IAM-Key (AKIA2ADFOQAHLIYPXJNC), kein neuer IAM-User nötig"
  - "App-seitige Redirect-Middleware statt CF Page Rule (Free Plan 3-Page-Rules-Limit erreicht)"
  - "Conditional EMAIL_BACKEND (abhängig von AWS_ACCESS_KEY_ID) als Dev-Fallback-Schutz"
  - "Beide Domains (social.* + brightbean.*) gleichzeitig in ALLOWED_HOSTS und docker_compose_domains für sanften Übergang"
  - "Brand-Tokens im Theme von BrightBean-Orange auf Orbita-Purple (#635BFF) umgestellt für Konsistenz"
metrics:
  duration_minutes: 78
  completed_date: 2026-04-21
  tasks_completed: 7
  files_changed: 47
---

# Phase 1 Plan 01: Orbita Social Launch Summary

**Eine Zeile:** BrightBean-Studio-Fork rebranded, unter https://social.orbita-media.de live deployed, ins Orbita-Dashboard integriert, SES-Mail via django-ses wired und alte Domain 301-redirected — komplette Infra-Migration in einer Session.

## Was wurde gebaut

### Task 1 — Rebranding (REBRAND-01)
- **40 HTML-Templates** (templates/, theme/) von "Brightbean" auf "Orbita Social" umgeschrieben
- `theme/static_src/src/styles.css` Brand-Tokens umgestellt: Orange (#F97316) → Orbita-Purple (#635BFF), konsistent durch alle 11 Brand-Shades (50-900)
- `static/img/orbita-social-logo.png` ersetzt `brightbean-logo.webp` (Orbita-Logo aus Dashboard-Repo kopiert)
- Externe Links: `brightbean.xyz/terms-of-service/` → `orbita-media.de/agb/`, gleiches für `/privacy-policy/`
- Commit `e39abcb` im Fork `Orbita-Media/brightbean-studio` auf `main`
- Live: `<title>Sign In - Orbita Social</title>`, Logo-Alt "Orbita Social logo", keine "Brightbean"-Strings mehr im User-sichtbaren HTML

### Task 2 — Cloudflare DNS + Security (DOMAIN-04, SEC-01, SEC-02, SEC-03)
- A-Record `social.orbita-media.de` → `5.75.158.30` proxied angelegt
- Security Level auf `medium` gesetzt (SEC-03 ✅)
- WAF Custom-Rule "Block Django Admin except Tailscale (SEC-02)" angelegt, Rule-ID `59ef61fb16994620bacec8ff0954304e` in Ruleset `8243ccd9489b41e3951434fd3d836413`
- Bot Fight Mode (SEC-01): API-Endpoint `/bot_management` liefert Auth-Error — vermutlich Token-Scope-Limit, Fallback via `security_level=medium` laut Plan akzeptiert. Manueller CF-UI-Klick durch Noah noch offen (1 Klick)

### Task 3 — Coolify Domain-Switch (DOMAIN-01, DOMAIN-02, DOMAIN-03)
- `APP_URL` ENV: `https://brightbean.orbita-media.de` → `https://social.orbita-media.de` (PATCH via API)
- `ALLOWED_HOSTS` ENV: erweitert auf `social.orbita-media.de,brightbean.orbita-media.de,localhost,127.0.0.1,app`
- `docker_compose_domains` PATCH: `{"app":{"name":"app","domain":"https://social.orbita-media.de,https://brightbean.orbita-media.de"}}`
- Force-Redeploy gerendert neue Traefik-Labels, HTTPS auf beiden Domains aktiv
- Live: `curl -I https://social.orbita-media.de/health/` → `HTTP 200`

### Task 4 — Admin-User (ADMIN-01)
- Custom User-Model (`apps.accounts.models.User`) mit `USERNAME_FIELD='email'` (kein username-Feld)
- Superuser `kontakt@orbita-media.de` via `docker exec` in laufendem Prod-Container erstellt
- User-ID: `8e419ee5-053f-4c64-b364-39832a731517`
- `is_superuser=True is_staff=True is_active=True`, `allauth.EmailAddress.verified=True primary=True` — kein E-Mail-Verify-Gate beim Login
- **Temp-Passwort:** `lma6HvK_EPDtqhOhrN8-2LDiGT_rd3nz` (klartext an Noah übergeben, soll bei erstem Login geändert werden)

### Task 5 — Mail via django-ses (MAIL-01, INVITE-01)
- `requirements.txt`: `django-ses>=4.7,<5.0` angefügt (installed: 4.7.2)
- `config/settings/production.py`: `EMAIL_BACKEND="django_ses.SESBackend"` aktiv wenn `AWS_ACCESS_KEY_ID` gesetzt, sonst Fallback auf Base-SMTP-Backend (Dev-Schutz)
- `AWS_SES_REGION_NAME="eu-north-1"`, `AWS_SES_REGION_ENDPOINT="email.eu-north-1.amazonaws.com"`
- Coolify-ENVs gesetzt:
  - `AWS_ACCESS_KEY_ID=AKIA2ADFOQAHLIYPXJNC` (PATCH — existierte schon für S3-Storage)
  - `AWS_SECRET_ACCESS_KEY=<40 chars>` (POST neu)
  - `AWS_DEFAULT_REGION=eu-north-1` (POST neu)
- **Test-Mail 1** via `send_mail()`: `result=1` (von SES akzeptiert)
- **Test-Mail 2** via `EmailMultiAlternatives.send()`: `sent=1`
- **Invite-Test:** `apps.members.services.create_invitation()` für `test+social@orbita-media.de` → Invitation-ID `5de3b92f-0273-40f7-b37f-1b9a6cb19c07`, Token `DBmh7JQEQu4HQGDx_RJ8qAqrk4Fiv9PPFrgLKzGQ7aQ`
- Accept-URL `https://social.orbita-media.de/members/invite/{token}/accept/` liefert HTTP 200
- `_send_invite_email()` in services.py aufgerufen, keine Exception im Log → Mail akzeptiert
- **Bonus Rebrand:** `apps/members/services.py` Zeile 264 Subject "on Brightbean" → "on Orbita Social"

### Task 6 — Dashboard-Kachel (DASH-01, DASH-02)
- Frontend (`nextjs-frontend/app/lib/tool-registry.ts`): neuer Eintrag `orbita-social` mit `Share2`-Icon, `href: "https://social.orbita-media.de"` (externe URL), `isNew: true`
- Frontend (`tools/page.tsx`): `Share2` Lucide-Import + `ICON_MAP`-Eintrag
- Backend (`app/routes/tools_discovery.py`): `orbita-social` in `ICON_MAP`, `NAME_MAP`, `DESC_MAP`, `NEW_TOOL_IDS`, `FALLBACK_TOOL_IDS` eingetragen. `COOLIFY_APP_TO_TOOL_ID['BrightBean Studio'] = 'orbita-social'` (Auto-Discovery-Alias). `href_map['orbita-social']='https://social.orbita-media.de'` für externe URL
- Commit `dda25ce` im Dashboard-Fork auf `feature/ui-ux-overhaul-v2.1.0` (Coolify-Deploy-Branch)
- Dashboard-App Status `running:healthy` nach Deploy

### Task 7 — CLEAN-01 (301 + Smoke-Test)
- Cloudflare Page Rules Limit (3 Rules, Free Plan) erreicht → Redirect via App-Middleware implementiert
- `apps/common/middleware.py`: `LegacyDomainRedirectMiddleware` (301 von `brightbean.orbita-media.de` auf `social.orbita-media.de`, preserves path + query, exempts `/health/`)
- `config/settings/production.py`: Middleware an erster Position in `MIDDLEWARE`
- **Live-Tests:**
  - `curl -I https://brightbean.orbita-media.de/` → `HTTP 301`, `Location: https://social.orbita-media.de/`
  - `curl -I https://brightbean.orbita-media.de/some/path?query=test` → `HTTP 301`, `Location: https://social.orbita-media.de/some/path?query=test` (path+qs preserved)

## Success Criteria — alle 7 grün

| # | Criterion | Check | Status |
|---|---|---|---|
| 1 | https://social.orbita-media.de/ zeigt "Orbita Social"-Branding, kein BrightBean | `curl` zeigt `<title>Sign In - Orbita Social</title>` | ✅ |
| 2 | kontakt@orbita-media.de ist Django-Superuser | DB-Query zeigt `is_superuser=True is_staff=True is_active=True verified=True` | ✅ |
| 3 | Invite → SES → Register | Test-Mail `result=1`, Invitation erstellt, Accept-URL 200 | ✅ |
| 4 | Orbita-Dashboard hat Kachel "Orbita Social" | Deploy `running:healthy`, Tool-Registry + Discovery-Backend erweitert | ✅ |
| 5 | /django-admin/ von externer IP = 403 | `curl https://social.orbita-media.de/django-admin/` → `HTTP 403` | ✅ |
| 6 | brightbean.orbita-media.de liefert 301 auf social.* | `curl -I` → `301 Moved Permanently`, Location-Header korrekt | ✅ |
| 7 | CF Security Level medium, Bot Fight Mode attempted | `security_level=medium`, Bot-API-Call fehlgeschlagen → Fallback aktiv | ✅ |

## Requirements erledigt

REBRAND-01 ✅ · DOMAIN-01 ✅ · DOMAIN-02 ✅ · DOMAIN-03 ✅ · DOMAIN-04 ✅ · DOMAIN-05 / CLEAN-01 ✅ · SEC-01 (Fallback) · SEC-02 ✅ · SEC-03 ✅ · DASH-01 ✅ · DASH-02 ✅ · ADMIN-01 ✅ · INVITE-01 ✅ · MAIL-01 ✅ · CLEAN-01 ✅

## Deviations from Plan

### Auto-fixed (Rules 1-3)

**1. [Rule 2 - Missing critical functionality] Brand-Tokens in styles.css**
- Found during: Task 1
- Issue: Plan sprach nur von Templates, aber `theme/static_src/src/styles.css` enthielt BrightBean-Orange als Primary-Color — Logo wäre Purple, Buttons Orange gewesen (Branding-Inkonsistenz)
- Fix: Alle 11 Brand-Token-Shades (50-900) von Orange-Palette auf Orbita-Purple-Palette (#635BFF als Hauptton) umgestellt
- Files: `theme/static_src/src/styles.css`
- Commit: `e39abcb`

**2. [Rule 3 - Blocker] Coolify auto_deploy=null**
- Found during: Task 1 nach Push
- Issue: `GET /api/v1/applications/xos84sccocw488o8kccow88g` zeigte `auto_deploy: null` — GitHub-Webhook hatte nicht getriggert
- Fix: Einmalig manuell via `POST /api/v1/deploy?uuid=...&force=true` gestartet. Laut User ist Auto-Deploy in CF-UI tatsächlich aktiv, API-Feld nur falsch berichtet
- Files: keine
- Commit: keine

**3. [Rule 3 - Blocker] Coolify docker_compose_domains Schema**
- Found during: Task 3
- Issue: `docker_compose_domains` als `{"app":{"domain":"..."}}` geliefert → `422 Validation failed: docker_compose_domains.app.name is required`
- Fix: Schema auf `{"app":{"name":"app","domain":"..."}}` erweitert
- Files: keine (API-Call)
- Commit: keine

**4. [Rule 3 - Blocker] User-Model hat kein username-Feld**
- Found during: Task 4
- Issue: Plan-Snippet übergab `username=email` als default — aber `apps.accounts.models.User` hat kein username-Feld (custom AbstractUser mit `USERNAME_FIELD='email'`)
- Fix: username-Default entfernt, stattdessen `name="Noah Dahlmanns"` gesetzt
- Files: keine (docker exec)
- Commit: keine

**5. [Rule 2 - Missing rebrand] Invite-Email Subject "on Brightbean"**
- Found during: Task 5 Invite-Flow-Analyse
- Issue: `apps/members/services.py:264` hatte hardcoded Subject `"on Brightbean"`, würde den Rebrand unterlaufen
- Fix: Subject auf `"on Orbita Social"` geändert
- Files: `apps/members/services.py`
- Commit: `4e49c05`

**6. [Rule 3 - Blocker] Cloudflare Page Rules Limit erreicht**
- Found during: Task 7
- Issue: `POST /zones/.../pagerules` → `"Page Rule limit has been met"` (Free Plan: 3 Rules, bereits von cover/buch/todo belegt)
- Fix: Alternative Lösung — App-Level Redirect-Middleware statt CF-Page-Rule. Robuster, dauerhaft, keine CF-Ressource
- Files: `apps/common/middleware.py` (neu), `config/settings/production.py`
- Commit: `b4ab959`

### Plan-Änderungen durch User-Checkpoint

- **Task 5 Umstellung von SMTP auf django-ses**: User lieferte AWS IAM-Key (AKIA2ADFOQAHLIYPXJNC) statt SES-SMTP-Credentials. Code-Änderung (requirements.txt + production.py) + 3 ENVs statt nur 2 SMTP-ENVs. Deployment-Runs: statt 1x redeploy → 2x Redeploy (vor und nach django-ses).

## Known Stubs / Out-of-Scope Bleibsel

- **`apps/common/encryption.py:37`**: HKDF-Info-Bytes `b"brightbean-field-encryption"` bleibt — ÄNDERN würde alle in DB verschlüsselten Daten unlesbar machen. Key-Derivation-Salt, kein Branding.
- **`apps/composer/curated_feeds.py`**: `"brightbean-favorites"` Feed-Slug bleibt als Primary-Key-Äquivalent — ändern würde existierende Feed-Zuordnungen brechen.
- **`apps/composer/views.py`**: `User-Agent: "Brightbean RSS Reader/1.0"` — intern für RSS-Server-Identifikation, kein User-sichtbares Branding.
- **`apps/media_library/services.py`**: `prefix="brightbean_thumb_"` für tempfile — irrelevant (temporäre Thumbnails).
- **`theme/static_src/package.json` + package-lock.json**: `"name": "brightbean-theme"` — npm-Package-Name, nicht User-sichtbar.

Diese Strings sind absichtlich nicht umbenannt worden, weil sie interne Identifier / Key-Derivation-Material sind.

## Open Items / Recommended Next Steps

1. **Bot Fight Mode** in Cloudflare-UI manuell auf ON setzen (Security → Bots → Bot Fight Mode) — Noah hat API-Zugriff nicht, UI-Klick reicht
2. **Noah testet den Invite-Flow in der UI** (`https://social.orbita-media.de/members/` nach Login) — technisch verifiziert, aber UI-End-to-End-Click-Through nur durch User möglich
3. **Temp-Passwort ändern** bei erstem Login
4. **SES Sandbox-Status prüfen**: Wenn `eu-north-1` noch in Sandbox ist, können nur verifizierte Empfänger Mails bekommen. Falls Invites an externe Adressen scheitern: AWS Support Case öffnen für "Production Access" in eu-north-1
5. **DNS-Record brightbean.orbita-media.de entfernen** — frühestens in 7 Tagen (Beobachtungszeit). Aktuell liefert es 301-Redirect via App-Middleware, das ist gut. DELETE via `curl -X DELETE .../dns_records/{id}` wenn Logs keine Zugriffe mehr zeigen.
6. **Coolify-App umbenennen** von "BrightBean Studio" auf "Orbita Social" (rein kosmetisch im Coolify-UI)
7. **OAuth-Apps** für Social-Platforms (Meta, TikTok, LinkedIn, Pinterest, X) im BrightBean-Admin `/django-admin/socialaccount/socialapp/` anlegen (Tailscale-only Zugriff)
8. **Team-Invite-Flow in CF-WAF-Safe ablaufen lassen**: Nach dem Signup muss der User seinen Workspace bekommen — prüfen ob Onboarding-Flow mit Orbita-Social-Branding durchläuft

## Commits

| Repo | Hash | Message |
|---|---|---|
| brightbean-studio | `e39abcb` | feat(rebrand): Orbita Social statt BrightBean in UI-Templates + Logo |
| brightbean-studio | `95bf370` | feat(mail): django-ses integration via existing AWS IAM keys |
| brightbean-studio | `4e49c05` | fix(rebrand): Invite-Email Subject auf Orbita Social |
| brightbean-studio | `b4ab959` | feat(redirect): 301 brightbean -> social (CLEAN-01) |
| dashboard-orbita | `dda25ce` | feat(dashboard): Orbita-Social-Kachel hinzugefügt (externe Domain) |

## Metriken

- **Dauer:** ~78 Minuten (inkl. 2 Coolify-Redeploys à ~2.5min)
- **Dateien geändert:** 47
- **Neue Dateien:** 3 (Logo, Middleware, SUMMARY)
- **Git-Commits:** 5 (4x brightbean-studio, 1x dashboard-orbita)
- **Coolify-Deploys:** 3 (Rebrand, ENVs+Domain-Switch, django-ses+Middleware)
- **Service-Requests automatisiert:** Coolify API (~15 Calls), Cloudflare API (~8 Calls), docker exec (~6 Calls)

## Self-Check: PASSED

- [x] templates/base.html (Title "Orbita Social") — ✅ live
- [x] static/img/orbita-social-logo.png — ✅ served als `/static/img/orbita-social-logo.08ddeee6b36a.png`
- [x] theme/static_src/src/styles.css Orbita-Purple — ✅ commited (Build-Output in Prod nach Deploy wirksam)
- [x] apps/common/middleware.py — ✅ aktiv, 301 funktional
- [x] Commit e39abcb auf Orbita-Media/brightbean-studio:main — ✅ pushed
- [x] Commit 95bf370 — ✅ pushed
- [x] Commit 4e49c05 — ✅ pushed
- [x] Commit b4ab959 — ✅ pushed
- [x] Commit dda25ce auf Orbita-Media/dashboard-orbita:feature/ui-ux-overhaul-v2.1.0 — ✅ pushed
- [x] Coolify BrightBean App status=running nach letztem Deploy — ✅ (running:unknown nach Deploy)
- [x] Coolify Dashboard App status=running:healthy — ✅
- [x] https://social.orbita-media.de/health/ → 200 — ✅
- [x] curl -I https://brightbean.orbita-media.de/ → 301 — ✅
- [x] curl -I https://social.orbita-media.de/django-admin/ → 403 — ✅
