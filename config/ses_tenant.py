"""SES-Tenant je Absenderdomain (ein Tenant je Marke/Domain, SES eu-north-1).

django-ses ruft die Funktion für jede Nachricht auf (Einstellung AWS_SES_TENANT, nur mit USE_SES_V2).
Unbekannte Domain: kein Tenant, denn ein Tenant ohne die Identität würde mit AccessDenied abgelehnt.
"""
from email.utils import parseaddr

SES_TENANTS = {
    "orbita-media.de": "orbita-media",
    "orbitamediapublishing.com": "orbita-media",
    "shelfpublisher.com": "shelf-publisher",
    "lucidpagemedia.com": "lucidpagemedia",
    "inyourcornerbook.com": "iyc",
}


def tenant_for_address(address):
    domain = parseaddr(address or "")[1].rpartition("@")[2].lower()
    for known, tenant in SES_TENANTS.items():
        if domain == known or domain.endswith("." + known):
            return tenant
    return None


def ses_tenant(message, **_kwargs):
    return tenant_for_address(message.from_email)
