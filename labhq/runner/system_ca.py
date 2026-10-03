"""The OS trust store for staff tools on Windows (9th mock trial, 2026-10-03).

An institution's TLS inspection appliance signs sites with a root that Windows trusts and certifi does not, so
`requests` in a staff venv failed with CERTIFICATE_VERIFY_FAILED and the step stopped. The runner writes certifi's
roots plus the Windows ROOT and CA stores into one PEM and points SSL_CERT_FILE and REQUESTS_CA_BUNDLE at it.
Verification stays on: the bundle trusts only what the OS or certifi already trusts. certifi stays in because
Windows fetches missing public roots on demand, so its local store alone can lack one.
"""

from __future__ import annotations

import os
import ssl
from pathlib import Path

CA_ENV = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")
WINDOWS = os.name == "nt"


def system_ca_pem() -> str | None:
    """certifi's roots and the Windows trust store as one PEM, or None off Windows or when the store is unreadable."""
    if not WINDOWS:
        return None
    try:
        der = ssl.create_default_context().get_ca_certs(binary_form=True)
    except (ssl.SSLError, OSError, ValueError):
        return None
    if not der:
        return None
    try:
        import certifi

        base = Path(certifi.where()).read_text(encoding="ascii").rstrip("\n") + "\n"
    except (ImportError, OSError, UnicodeDecodeError):
        base = ""
    return base + "".join(ssl.DER_cert_to_PEM_cert(cert) for cert in der)
