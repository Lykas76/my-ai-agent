"""Best-effort credential-shaped input rejection, not a general DLP system."""
import re

PATTERN = re.compile(
    r"(?i)bearer\s|[0-9a-f]{32}\.[A-Za-z0-9_-]{43}|sk-[A-Za-z0-9_-]{20,}|"
    r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"\b[0-9]{6,12}:[A-Za-z0-9_-]{30,}\b|-----BEGIN .*PRIVATE KEY"
)


def reject_credentials(text):
    if PATTERN.search(text):
        raise ValueError("Credential-shaped text is not accepted")
