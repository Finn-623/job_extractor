from urllib.parse import urlsplit, urlunsplit

def safe_request_url(url: str) -> str:
    """Drop query strings because they can contain signatures or tokens."""
    p=urlsplit(url)
    return urlunsplit((p.scheme,p.netloc,p.path,"",""))

def is_api_response(url: str, fragment: str, method: str, expected_method: str,
                    status: int | None=None, content_type: str="") -> bool:
    return (fragment in urlsplit(url).path and method.upper()==expected_method.upper()
            and (status is None or 200 <= status < 300)
            and (not content_type or "json" in content_type.lower()))
