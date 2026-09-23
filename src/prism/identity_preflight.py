"""Fail-closed local configuration checks for the named identity service."""

import ipaddress
import os
import socket
import ssl
import stat
import time
import urllib.parse
from pathlib import Path

from prism.identity import OIDCConfig, read_client_secret
from prism.projects import ProjectSource
from prism.sharing import Denied


def _certificate_matches(decoded, hostname):
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    for kind, value in decoded.get("subjectAltName", ()):
        if address is not None and kind == "IP Address":
            try:
                if ipaddress.ip_address(value) == address:
                    return True
            except ValueError:
                continue
        if address is None and kind == "DNS":
            pattern = value.rstrip(".").casefold()
            host = hostname.rstrip(".").casefold()
            if pattern == host or (
                pattern.startswith("*.")
                and host.count(".") == pattern.count(".")
                and host.endswith(pattern[1:])
            ):
                return True
    return False


def _regular_file(path, *, private=False):
    candidate = Path(path)
    try:
        info = candidate.lstat()
    except OSError as exc:
        raise ValueError("A configured TLS file is unavailable.") from exc
    if candidate.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise ValueError("Use explicit regular TLS certificate and key files.")
    if private and (
        info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077
    ):
        raise ValueError("Use an owner-only (0600) single-link TLS private key file.")
    return candidate


def validate_tls_identity(cert_file, key_file, hostname, *, now=None):
    """Validate certificate parsing, key pairing, hostname and validity locally."""
    certificate = _regular_file(cert_file)
    private_key = _regular_file(key_file, private=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        context.load_cert_chain(certificate, private_key)
        decoded = ssl._ssl._test_decode_cert(str(certificate))
        if not _certificate_matches(decoded, hostname):
            raise ValueError("The TLS certificate does not cover the public hostname.")
        current = time.time() if now is None else now
        if ssl.cert_time_to_seconds(decoded["notBefore"]) > current:
            raise ValueError("The TLS certificate is not valid yet.")
        if ssl.cert_time_to_seconds(decoded["notAfter"]) <= current:
            raise ValueError("The TLS certificate has expired.")
    except (OSError, ssl.SSLError, ssl.CertificateError, KeyError) as exc:
        raise ValueError(
            "The TLS certificate, private key, hostname, or validity period is invalid."
        ) from exc


def _network_probe(urls, *, timeout=5):
    """Perform TLS handshakes only; never send an OAuth or token HTTP request."""
    checked = set()
    for value in urls:
        parsed = urllib.parse.urlsplit(value)
        destination = (parsed.hostname, parsed.port or 443)
        if destination in checked:
            continue
        checked.add(destination)
        context = ssl.create_default_context()
        try:
            with (
                socket.create_connection(destination, timeout=timeout) as plain,
                context.wrap_socket(plain, server_hostname=parsed.hostname) as secured,
            ):
                secured.getpeercert()
        except (OSError, ssl.SSLError) as exc:
            raise ValueError(
                "A configured identity-provider TLS endpoint is unreachable or untrusted."
            ) from exc


def identity_preflight(
    *,
    oidc_config,
    bind_host,
    port,
    tls_cert_file,
    tls_key_file,
    oidc_client_secret_file=None,
    projects=(),
    runtime_profile="development",
    network=False,
):
    """Return a secret-free readiness report without creating service state."""
    checks = []
    config = OIDCConfig.from_file(oidc_config)
    checks.append({"id": "oidc_config", "status": "pass"})
    configured_port = urllib.parse.urlsplit(config.public_origin).port or 443
    if port != configured_port or not bind_host:
        raise ValueError(
            "The bind host and listening port must match the intended canonical origin."
        )
    checks.append({"id": "canonical_origin", "status": "pass"})
    validate_tls_identity(
        tls_cert_file,
        tls_key_file,
        urllib.parse.urlsplit(config.public_origin).hostname,
    )
    checks.append({"id": "tls_identity", "status": "pass"})
    if oidc_client_secret_file:
        read_client_secret(oidc_client_secret_file)
        checks.append({"id": "oidc_client_secret", "status": "pass"})
    else:
        checks.append({"id": "oidc_client_secret", "status": "operator_confirm"})
    try:
        for manifest in projects:
            ProjectSource.from_manifest(Path(manifest), action_profile=runtime_profile)
    except Denied as exc:
        raise ValueError(exc.message) from exc
    checks.append({"id": "projects", "status": "pass"})
    if network:
        _network_probe(
            (
                config.issuer,
                config.authorization_endpoint,
                config.token_endpoint,
                config.jwks_uri,
            )
        )
        checks.append({"id": "idp_tls_reachability", "status": "pass"})
    else:
        checks.append({"id": "idp_tls_reachability", "status": "not_checked"})
    checks.extend(
        [
            {"id": "owner_exact_subject", "status": "operator_confirm"},
            {"id": "recipient_exact_subject", "status": "operator_confirm"},
            {"id": "browser_oidc_flow", "status": "not_checked"},
            {"id": "private_reachability", "status": "not_checked"},
            {"id": "model_route", "status": "not_checked"},
            {"id": "kata_runtime", "status": "not_checked"},
        ]
    )
    return {
        "kind": "identity_service_preflight",
        "local_inputs_valid": True,
        "network_probe_enabled": network,
        "pilot_ready": False,
        "checks": checks,
    }
