# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""The web listener's certificate (WCON-070, SDD §3.2 ``CertificateStore``).

Separate from Connect Link's own: that one identifies this Master to its peers and its key never leaves the
secret store, whereas this one is shown to a browser and its key is handed to the web process once, at start.
A self-signed EC P-256 certificate is made on first use; a user-supplied pair replaces it. The private key lives
in the core secret store, the (public) certificate in the plugin's settings.
"""

from __future__ import annotations

import datetime
import hashlib
import ipaddress
import socket

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

_KEY = "webconnect.tls.key"
_CERT_SETTING = "tls_cert"
_VALID_DAYS = 825


class TlsError(Exception):
    """The certificate could not be made or read; the message is shown to the person."""


def fingerprint_of(cert_pem: str) -> str:
    der = x509.load_pem_x509_certificate(cert_pem.encode("ascii")).public_bytes(serialization.Encoding.DER)
    return hashlib.sha256(der).hexdigest()


class CertificateStore:
    def __init__(self, secrets, settings, names: list[str] | None = None) -> None:
        self._secrets, self._settings = secrets, settings
        self._names = names or [socket.gethostname(), "localhost"]

    def ensure(self) -> tuple[str, str]:
        """``(certificate PEM, private key PEM)``, making a pair if there is none."""
        cert, key = self._settings.get(_CERT_SETTING, ""), self._secrets.get(_KEY)
        if cert and key:
            return cert, key
        return self._generate()

    def fingerprint(self) -> str:
        return fingerprint_of(self.ensure()[0])

    def _generate(self) -> tuple[str, str]:
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, self._names[0])])
        sans: list[x509.GeneralName] = []
        for n in self._names:
            try:
                sans.append(x509.IPAddress(ipaddress.ip_address(n)))
            except ValueError:
                sans.append(x509.DNSName(n))
        now = datetime.datetime.now(datetime.UTC)
        cert = (x509.CertificateBuilder()
                .subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=_VALID_DAYS))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName(sans), critical=False)
                .sign(key, hashes.SHA256()))
        return self._store(cert.public_bytes(serialization.Encoding.PEM).decode("ascii"), key)

    def _store(self, cert_pem: str, key) -> tuple[str, str]:
        key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()).decode("ascii")
        self._secrets.set(_KEY, key_pem)
        self._settings.set(_CERT_SETTING, cert_pem)
        return cert_pem, key_pem

    def install_user_pair(self, cert_pem: str, key_pem: str) -> str:
        """Replace the certificate with the user's own; refuses a key that does not match it."""
        try:
            cert = x509.load_pem_x509_certificate(cert_pem.encode("ascii"))
            key = serialization.load_pem_private_key(key_pem.encode("ascii"), password=None)
        except Exception as exc:
            raise TlsError(f"could not read the certificate and key: {exc}") from exc
        spki, pem = serialization.PublicFormat.SubjectPublicKeyInfo, serialization.Encoding.PEM
        if cert.public_key().public_bytes(pem, spki) != key.public_key().public_bytes(pem, spki):
            raise TlsError("the private key does not match the certificate")
        self._store(cert_pem, key)
        return fingerprint_of(cert_pem)
