"""Replace people's names, emails and handles with stable pseudonyms.

Identities are collected from git metadata, package manifests and an
optional roster. Each person maps to `person-N`; known coding agents keep
a readable label (`agent:claude`) because "who committed this" matters
for the AI-collaboration criteria and an agent is not personal data.
The mapping itself is instructor-only and never reaches a model.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .git_reader import GitHistory

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
GITHUB_NOREPLY_RE = re.compile(r"^(?:\d+\+)?([A-Za-z0-9\-]+)@users\.noreply\.github\.com$", re.I)

AGENT_PATTERNS = [
    ("claude", re.compile(r"claude|anthropic", re.I)),
    ("copilot", re.compile(r"copilot", re.I)),
    ("cursor", re.compile(r"\bcursor", re.I)),
    ("codex", re.compile(r"codex|openai", re.I)),
    ("devin", re.compile(r"devin", re.I)),
    ("aider", re.compile(r"aider", re.I)),
    ("gemini", re.compile(r"gemini|jules", re.I)),
    ("bot", re.compile(r"\[bot\]|github-actions|dependabot|renovate", re.I)),
]

MIN_TOKEN_LEN = 3  # shorter single tokens ("Li", "Wu") are too likely to be ordinary text
MIN_HANDLE_LEN = 4


GENERIC_LOCALS = {"info", "admin", "contact", "hello", "mail", "noreply", "no-reply", "root", "user", "test", "student"}


def agent_label(name: str, email: str) -> str | None:
    if email.lower() == "noreply@github.com":
        return "platform:github"
    text = f"{name} {email}"
    for label, pat in AGENT_PATTERNS:
        if pat.search(text):
            return f"agent:{label}"
    return None


def _alternation(words: dict[str, str], flags: int) -> re.Pattern:
    if not words:
        return re.compile(r"(?!x)x")
    alts = sorted(words, key=len, reverse=True)  # longest first: full names beat tokens
    return re.compile(r"(?<![\w@])(?:" + "|".join(map(re.escape, alts)) + r")(?!\w)", flags)


@dataclass
class Identity:
    pseudonym: str
    names: set[str] = field(default_factory=set)
    emails: set[str] = field(default_factory=set)
    handles: set[str] = field(default_factory=set)


@dataclass
class Anonymizer:
    people: list[Identity] = field(default_factory=list)
    _by_key: dict[str, Identity] = field(default_factory=dict)
    _pattern: re.Pattern | None = None
    _lookup: dict[str, str] = field(default_factory=dict)
    _tokens: dict[str, str] = field(default_factory=dict)
    _token_pattern: re.Pattern | None = None

    # -- building ---------------------------------------------------------
    def add(self, name: str = "", email: str = "") -> str:
        """Register an identity and return its pseudonym (or agent label)."""
        name, email = name.strip(), email.strip().lower()
        label = agent_label(name, email)
        if label:
            return label
        keys = [k for k in (f"e:{email}" if email else "", f"n:{name.lower()}" if name else "") if k]
        if not keys:
            return "unknown"
        ident = next((self._by_key[k] for k in keys if k in self._by_key), None)
        if ident is None:
            ident = Identity(pseudonym=f"person-{len(self.people) + 1}")
            self.people.append(ident)
        if name:
            ident.names.add(name)
        if email:
            ident.emails.add(email)
            m = GITHUB_NOREPLY_RE.match(email)
            if m:
                ident.handles.add(m.group(1))
            local = email.split("@", 1)[0]
            if len(local) >= MIN_HANDLE_LEN and not local.isdigit() and local not in GENERIC_LOCALS:
                ident.handles.add(local.split("+")[-1])
        for k in keys:
            self._by_key[k] = ident
        self._pattern = None
        return ident.pseudonym

    def pseudonym_for(self, name: str = "", email: str = "") -> str:
        return self.add(name, email)

    # -- applying ---------------------------------------------------------
    def _compile(self) -> None:
        # Full names, emails and handles match case-insensitively. Single name
        # tokens ("Mark", "Rose") match only as written, capitalized or upper
        # case, so ordinary identifiers like pytest.mark or max() survive.
        full: dict[str, str] = {}
        tokens: dict[str, str] = {}
        for ident in self.people:
            for e in ident.emails:
                full[e.lower()] = f"{ident.pseudonym}@redacted.invalid"
            for n in ident.names:
                full[n.lower()] = ident.pseudonym
                for tok in re.split(r"[\s._\-]+", n):
                    if len(tok) >= MIN_TOKEN_LEN:
                        for form in (tok, tok.capitalize(), tok.upper()):
                            tokens.setdefault(form, ident.pseudonym)
            for h in ident.handles:
                full.setdefault(h.lower(), ident.pseudonym)
        self._lookup, self._tokens = full, tokens
        self._pattern = _alternation(full, re.I)
        self._token_pattern = _alternation(tokens, 0)

    def redact(self, text: str) -> tuple[str, int]:
        """Return (redacted text, number of replacements). Line count is preserved."""
        if self._pattern is None:
            self._compile()
        count = 0

        def known(m: re.Match) -> str:
            nonlocal count
            count += 1
            return self._lookup[m.group(0).lower()]

        def token(m: re.Match) -> str:
            nonlocal count
            count += 1
            return self._tokens[m.group(0)]

        text = self._pattern.sub(known, text)
        text = self._token_pattern.sub(token, text)

        def any_email(m: re.Match) -> str:
            nonlocal count
            value = m.group(0)
            if value.endswith("@redacted.invalid") or agent_label("", value):
                return value
            count += 1
            return "[email]@redacted.invalid"

        text = EMAIL_RE.sub(any_email, text)
        return text, count

    def mapping(self) -> dict:
        """Instructor-only view of who is who."""
        return {
            i.pseudonym: {"names": sorted(i.names), "emails": sorted(i.emails), "handles": sorted(i.handles)}
            for i in self.people
        }


def _manifest_authors(root: Path) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(errors="replace"))
            for key in ("authors", "maintainers"):
                for a in data.get("project", {}).get(key, []) or []:
                    if isinstance(a, dict):
                        found.append((a.get("name", ""), a.get("email", "")))
            for a in data.get("tool", {}).get("poetry", {}).get("authors", []) or []:
                m = re.match(r"\s*([^<]*?)\s*(?:<([^>]+)>)?\s*$", str(a))
                if m:
                    found.append((m.group(1) or "", m.group(2) or ""))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError):
            pass
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(errors="replace"))
            people = [data.get("author")] + list(data.get("contributors") or [])
            for a in people:
                if isinstance(a, dict):
                    found.append((a.get("name", ""), a.get("email", "")))
                elif isinstance(a, str):
                    m = re.match(r"\s*([^<(]*?)\s*(?:<([^>]+)>)?", a)
                    if m:
                        found.append((m.group(1) or "", m.group(2) or ""))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    return [(n, e) for n, e in found if n or e]


def build_anonymizer(root: Path, history: GitHistory, roster: list[dict] | None = None) -> Anonymizer:
    anon = Anonymizer()
    for person in roster or []:
        anon.add(person.get("name", ""), person.get("email", ""))
    for c in history.commits:
        anon.add(c.author_name, c.author_email)
        anon.add(c.committer_name, c.committer_email)
        for n, e in c.co_authors:
            anon.add(n, e)
    for n, e in _manifest_authors(root):
        anon.add(n, e)
    return anon
