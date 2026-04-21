# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-04-21)

**Core value:** Ein Ort für Social-Media-Management über 10 Plattformen, ohne SaaS-Gebühren, team-fähig
**Current focus:** Phase 1 — Orbita Social Launch (complete)

## Current Position

Phase: 1 of 1 (Orbita Social Launch)
Plan: 1 of 1 complete
Status: Phase 1 abgeschlossen — Orbita Social ist live
Last activity: 2026-04-21 — Plan 01-01 ausgeführt und deployed

Progress: [██████████] 100%

## Performance Metrics

**Velocity:**
- Total plans completed: 1
- Average duration: ~78 min
- Total execution time: ~78 min

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1 | 1 | 78 min | 78 min |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- BrightBean-Fork statt Eigenbau (fertige Provider + RBAC)
- Name "Orbita Social", Domain `social.orbita-media.de`
- Keine CF Access-Wall — BrightBean-Invite reicht für Team
- Subdomain-Deployment (wie DittoFeed), nicht Dashboard-Iframe
- **django-ses statt SMTP**: Existierender Dashboard-AWS-IAM-Key (AKIA2ADFOQAHLIYPXJNC) wird wiederverwendet, kein neuer SES-SMTP-User nötig
- **App-Level Redirect-Middleware**: Statt CF Page Rule (Free-Plan 3-Rules-Limit), robuster + dauerhaft
- **Beide Domains parallel**: social.* + brightbean.* in ALLOWED_HOSTS + docker_compose_domains für sanften Übergang (301-Redirect auf brightbean.* aktiv)
- **Brand-Tokens auf Orbita-Purple**: #635BFF Purple statt #F97316 Orange — vollständiger Rebrand-Scope (nicht nur Templates)

### Pending Todos

- Bot Fight Mode in CF-UI aktivieren (Noah, manueller Klick, API-Zugriff verweigert)
- Noah: Temp-Passwort `lma6HvK_EPDtqhOhrN8-2LDiGT_rd3nz` nach erstem Login ändern
- SES Sandbox-Status in eu-north-1 prüfen (für externe Empfänger)
- brightbean.orbita-media.de DNS-Record nach 7 Tagen Beobachtungszeit entfernen
- Coolify-App umbenennen "BrightBean Studio" → "Orbita Social" (kosmetisch)
- OAuth-Apps für Social-Platforms in /django-admin/ (Meta, TikTok, LinkedIn, Pinterest, X)

### Blockers/Concerns

- Keine aktiven Blocker — Phase 1 ist live und funktional.

## Deferred Items

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| Security | Cloudflare Bot Fight Mode via UI aktivieren | Pending (Noah) | 2026-04-21 |
| Cleanup | DNS-Record brightbean.* löschen | Pending (+7 Tage) | 2026-04-21 |
| Cosmetic | Coolify-App umbenennen | Pending (Noah) | 2026-04-21 |

## Session Continuity

Last session: 2026-04-21
Stopped at: Phase 1 Plan 01 fertig. Orbita Social live unter https://social.orbita-media.de
Resume file: .planning/phases/01-orbita-social-launch/01-01-SUMMARY.md
