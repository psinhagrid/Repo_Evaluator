"""Secret detection and redaction, applied while the snapshot is written.

Models read the snapshot, so credentials a student committed by mistake must
never reach it: values are replaced in place (line numbers are unchanged) and
the findings are kept for the hygiene section of the report.
"""

from __future__ import annotations

import re

SECRET_PATTERNS = [
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("generic_assignment", re.compile(
        r"""(?i)\b(?:api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*['"]([^'"\s]{12,})['"]""")),
]
SECRET_ALLOW = re.compile(r"(?i)x{6,}|\.\.\.|<[^>]+>|your|example|placeholder|dummy|changeme|os\.environ|getenv|\$\{")
PRIVATE_KEY_END = re.compile(r"-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")
MASK = "[REDACTED-SECRET]"


def _value(m: re.Match) -> str:
    return m.group(1) if m.groups() else m.group(0)


def redact_secrets(path: str, text: str) -> tuple[str, list[dict]]:
    """Return (text with secret values masked, findings with a short masked excerpt)."""
    lines = text.split("\n")
    findings = []
    in_key = False
    for i, line in enumerate(lines):
        if in_key:
            lines[i] = MASK if not PRIVATE_KEY_END.search(line) else line
            in_key = not PRIVATE_KEY_END.search(line)
            continue
        new = line
        hit = None
        for kind, pat in SECRET_PATTERNS:
            for m in pat.finditer(line):
                value = _value(m)
                if SECRET_ALLOW.search(value):
                    continue
                hit = hit or kind
                if kind == "private_key":
                    in_key = True
                else:
                    new = new.replace(value, MASK)
        if hit:
            lines[i] = new
            findings.append({"scan": "secrets", "kind": hit, "file": path, "line": i + 1,
                             "excerpt": new.strip()[:200]})
    return "\n".join(lines), findings
