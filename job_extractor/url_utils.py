from urllib.parse import urlparse, urlunparse


def normalize_url(url: str) -> str:
    """Normalize an HTTP(S) URL without performing network access."""
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must start with http:// or https:// and include a host.")
    hostname = parsed.hostname.lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL contains an invalid port.") from exc
    host_for_netloc = f"[{hostname}]" if ":" in hostname else hostname
    netloc = host_for_netloc if port is None else f"{host_for_netloc}:{port}"
    return urlunparse(
        (parsed.scheme.lower(), netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
    )
