"""Bounded JSON HTTPS transport; no redirects or automatic retries."""
import json
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError


class NetworkError(Exception):
    def __init__(self, retryable=False):
        self.retryable = retryable
        super().__init__("Remote request failed")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Remote endpoint must be HTTPS without credentials, query or fragment")
    return url.rstrip("/")


def post_json(url, payload, headers, timeout):
    validate_url(url)
    request = Request(url, json.dumps(payload, allow_nan=False).encode(),
                      {"Content-Type": "application/json", **headers}, method="POST")
    try:
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            data = response.read(1_048_577)
            if len(data) > 1_048_576:
                raise NetworkError()
            return json.loads(data)
    except HTTPError as exc:
        raise NetworkError(exc.code in (429, 502, 503, 504)) from None
    except (URLError, OSError, ValueError):
        raise NetworkError() from None
