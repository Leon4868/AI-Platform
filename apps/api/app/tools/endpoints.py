"""What can be decided about an outbound URL before anyone dials it.

Two separate jobs live here.

**Keeping secrets out of the database.** A URL has places to hide one: the
`user:password@` userinfo, and the fragment. Both would be stored verbatim in a
business table, so a URL carrying either is refused rather than sanitised —
silently stripping it would leave the caller believing a credential was
configured.

**Refusing the obviously unreachable targets.** TLS, literal IP addresses and
internal names can be judged from the string alone.

What cannot be judged here is left to the egress boundary, and this module makes
no attempt to imply otherwise: a hostname that resolves to a private address, a
second lookup that answers differently, and a redirect to somewhere unreviewed
all require actually resolving DNS at call time.
"""

from ipaddress import ip_address
from urllib.parse import urlparse

_FORBIDDEN_HOST_SUFFIXES = (".local", ".internal", ".localdomain", ".home.arpa")
_FORBIDDEN_HOSTS = frozenset(
    {"localhost", "metadata", "metadata.google.internal", "instance-data"}
)


def validated_host(url: str) -> str:
    """Return the hostname of an acceptable https URL, or raise `ValueError`."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("the endpoint must use https")
    if parsed.username or parsed.password:
        raise ValueError(
            "the endpoint must not embed credentials; use a credential binding instead"
        )
    if parsed.fragment:
        raise ValueError("the endpoint must not carry a fragment")

    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("the endpoint must name a host")
    try:
        ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("the endpoint must use a hostname, not a literal IP address")
    if host in _FORBIDDEN_HOSTS or host.endswith(_FORBIDDEN_HOST_SUFFIXES):
        raise ValueError(f"'{host}' is an internal name and cannot be an endpoint")
    return host


def covered_by(host: str, allowlist: list[str]) -> bool:
    """Whether an egress allowlist actually permits this host.

    A leading `*.` matches sub-domains only — `*.example.com` covers
    `api.example.com` but not the apex, so widening to a parent domain has to be
    written out rather than acquired by accident.
    """
    for entry in allowlist:
        if entry == host:
            return True
        if entry.startswith("*.") and host.endswith(entry[1:]) and host != entry[2:]:
            return True
    return False
