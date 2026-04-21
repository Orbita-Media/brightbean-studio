# Requirements: Orbita Social

## v1 Requirements

### Rebranding

- [ ] **REBRAND-01**: Alle UI-Texte, Page-Titles, Navigation, Email-Templates zeigen "Orbita Social" statt "BrightBean Studio"
  - `templates/base.html` (title, logo, nav)
  - `templates/account/email/*.html` (allauth templates)
  - `templates/**/*.html` (search für "BrightBean", "brightbean")
  - Logo-Asset `theme/static/img/` ersetzt durch Orbita-Logo
  - `theme/templates/theme/meta.html` (OG-Tags, Favicon)

### Domain & Deployment

- [ ] **DOMAIN-01**: Coolify-App-Domain ist `https://social.orbita-media.de`
- [ ] **DOMAIN-02**: ENV-Variable `APP_URL` ist `https://social.orbita-media.de`
- [ ] **DOMAIN-03**: ENV-Variable `ALLOWED_HOSTS` enthält `social.orbita-media.de`
- [ ] **DOMAIN-04**: Cloudflare DNS: A-Record `social.orbita-media.de` → 5.75.158.30 (proxied)
- [ ] **DOMAIN-05**: Alter DNS-Record `brightbean.orbita-media.de` löschen (nachdem neue Domain verifiziert)

### Sicherheit

- [ ] **SEC-01**: Cloudflare Bot Fight Mode aktivieren für Zone `orbita-media.de` (falls nicht schon an)
- [ ] **SEC-02**: Cloudflare WAF Custom Rule: `/django-admin/*` auf `social.orbita-media.de` blockt außer IP in `100.64.0.0/10` (Tailscale CGNAT-Range)
- [ ] **SEC-03**: Security Level für Zone bleibt/wird auf "medium"

### Dashboard-Integration

- [ ] **DASH-01**: Orbita Media Dashboard zeigt Orbita-Social-Kachel mit Link auf `https://social.orbita-media.de`
- [ ] **DASH-02**: Kachel nutzt Orbita-Branding (konsistent mit anderen Tools wie Cover, Keyword, etc.)

### Admin & User-Management

- [ ] **ADMIN-01**: Noahs Admin-Account existiert in Production-DB — E-Mail `kontakt@orbita-media.de`, `is_superuser=true`, `is_staff=true`
- [ ] **INVITE-01**: Ein Test-Mitarbeiter kann über `/members/invite/` (oder äquivalenten Flow) eingeladen werden, bekommt E-Mail, kann sich registrieren und einloggen
- [ ] **MAIL-01**: Amazon SES SMTP-Credentials sind in Coolify ENVs gesetzt (`EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`) und Test-Mail wird ausgeliefert

## v2 Requirements (deferred)

- Mehrsprachigkeit (DE/EN UI) — Django-i18n einrichten für Kunden-Portal
- Orbita-Branding auf Email-Footer (Logo + Signatur)
- Custom Onboarding-Flow für neu eingeladene Mitarbeiter

## Out of Scope

- Rename des internen Django-Package `brightbean/` / `config/` — nur Präsentations-Schicht, kein Code-Refactoring
- CF Zero Trust Access-Wall — Externe Mitarbeiter brauchen Invite, nicht Google-SSO-Zwang
- S3-Storage-Migration — local storage reicht erstmal
- Sentry-Integration — später wenn Traffic da ist
- OAuth-App-Registrierung bei Meta/TikTok/Google — das macht Noah selbst im BrightBean Admin-UI nach Launch

## Traceability

| Requirement | Phase |
|-------------|-------|
| REBRAND-01 | Phase 1 |
| DOMAIN-01..05 | Phase 1 |
| SEC-01..03 | Phase 1 |
| DASH-01..02 | Phase 1 |
| ADMIN-01 | Phase 1 |
| INVITE-01 | Phase 1 |
| MAIL-01 | Phase 1 |
