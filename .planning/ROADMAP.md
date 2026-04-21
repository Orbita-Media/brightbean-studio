# Roadmap: Orbita Social

## Overview

Single-Phase-Projekt zur Transformation der deployten BrightBean-Instanz zur fertig gebrandeten, sicheren, ins Orbita-Dashboard integrierten "Orbita Social"-App inklusive funktionierendem Multi-User-Invite-Flow.

## Phases

- [ ] **Phase 1: Orbita Social Launch** — Rebranding, Domain-Migration, CF-Hardening, Dashboard-Kachel, Admin + Invite-Flow

## Phase Details

### Phase 1: Orbita Social Launch

**Goal**: BrightBean-Deployment ist zu "Orbita Social" umgebrandet, läuft auf `social.orbita-media.de`, ist in das Orbita-Dashboard als Kachel eingebunden, Noah kann sich als Admin einloggen und einen Test-Mitarbeiter einladen der per E-Mail-Invite dazukommen kann.

**Depends on**: Nothing (first phase)

**Requirements**: REBRAND-01, DOMAIN-01, DOMAIN-02, DOMAIN-03, DOMAIN-04, DOMAIN-05, SEC-01, SEC-02, SEC-03, DASH-01, DASH-02, ADMIN-01, INVITE-01, MAIL-01

**Success Criteria** (what must be TRUE):
  1. `https://social.orbita-media.de/` zeigt "Orbita Social"-Branding (Logo + Titel, kein "BrightBean" mehr sichtbar im UI)
  2. Noah kann sich mit `kontakt@orbita-media.de` einloggen und ist Superuser (Django-Admin erreichbar via Tailscale)
  3. Ein neuer Test-User mit fremder E-Mail (z.B. `test+social@orbita-media.de`) wird via UI eingeladen, bekommt E-Mail mit Invite-Link, kann sich registrieren und einloggen
  4. Orbita Media Dashboard zeigt "Orbita Social"-Kachel, Klick öffnet `https://social.orbita-media.de`
  5. Cloudflare WAF: direkter Aufruf von `/django-admin/` von nicht-Tailscale-IP ergibt 403/Block
  6. Alte Domain `brightbean.orbita-media.de` ist weg (oder leitet um)

**Plans**: 1 Plan mit 7 Tasks (coarse granularity, 3 Wellen, 5 parallel ausführbar)

Plans:
- [ ] 01-01-PLAN.md — Orbita Social Launch: Rebranding + Domain-Migration + CF-Hardening + Dashboard-Kachel + Admin + Invite-Flow (7 Tasks, Welle 1/2/3)

## Progress

**Execution Order:**
Phase 1 (single-phase milestone)

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Orbita Social Launch | 0/1 | Not started | - |
