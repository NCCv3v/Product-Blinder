"""Session-only secrets, URL restrictions and export redaction."""

import re
from urllib.parse import urlsplit


class Secrets:
    def __init__(self, target="", judge=""):
        self.target, self.judge = target, judge

    def redact(self, obj):
        if isinstance(obj, str):
            for secret in (self.target, self.judge):
                if secret:
                    obj = obj.replace(secret, "[REDACTED]")
            return re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", obj)
        if isinstance(obj, list):
            return [self.redact(x) for x in obj]
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if any(
                    x in str(k).lower() for x in ("api_key", "password", "authorization", "secret", "token")
                ) and k not in (
                    "tokens",
                    "total_tokens",
                    "prompt_tokens",
                    "completion_tokens",
                    "max_output_tokens",
                ):
                    out[k] = "[REDACTED]"
                else:
                    out[k] = self.redact(v)
            return out
        return obj


def validate_url(url):
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("Use a complete http(s) endpoint URL")
    if u.username or u.password or u.query or u.fragment:
        raise ValueError("No credentials, query strings or fragments in endpoint URLs")
    # Local APIs are intentional. Metadata endpoints are not valid targets.
    if u.hostname in ("169.254.169.254", "metadata.google.internal") or u.hostname.startswith("169.254."):
        raise ValueError("Cloud metadata endpoints are forbidden")
    if u.scheme == "http" and u.hostname not in ("localhost", "127.0.0.1", "::1", "host.docker.internal"):
        raise ValueError("Remote endpoints require HTTPS; HTTP is allowed only for local APIs")
    return url


def check_no_embedded_secrets(obj, secrets):
    """Known credentials must not be frozen into target bodies, suites or metadata."""
    import json

    raw = json.dumps(obj)
    if any(s and s in raw for s in (secrets.target, secrets.judge)):
        raise ValueError("Credentials must only be entered in the separate masked settings")

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if any(p in k.lower() for p in ("api_key", "password", "authorization", "secret")):
                    raise ValueError("Credentials are not allowed in suite/config fields")
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(obj)
