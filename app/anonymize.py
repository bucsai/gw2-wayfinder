"""Replaces account, character and guild names with aliases before text reaches the model."""

import re
import string

KEY_PATTERN = re.compile(r"[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{20}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}", re.I)
UUID_PATTERN = re.compile(r"[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}", re.I)


def _letters(n: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    result = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        result = string.ascii_uppercase[r] + result
    return result


def _pattern(words) -> re.Pattern | None:
    words = sorted(words, key=len, reverse=True)
    if not words:
        return None
    return re.compile(r"(?<!\w)(" + "|".join(map(re.escape, words)) + r")(?!\w)", re.I)


class Pseudonymizer:
    def __init__(self):
        self.to_alias: dict[str, str] = {}
        self.to_real: dict[str, str] = {}
        self.counts: dict[str, int] = {}

    def alias(self, real: str, kind: str) -> str:
        """Return the alias for a real name. kind is "account", "character" or "guild"."""
        key = real.lower()
        if key not in self.to_alias:
            n = self.counts.get(kind, 0)
            self.counts[kind] = n + 1
            alias = f"Account_{n + 1}" if kind == "account" else f"{kind.title()}_{_letters(n)}"
            self.to_alias[key] = alias
            self.to_real[alias] = real
            if kind == "account":
                # Also hide the name without the ".1234" suffix.
                bare = real.rsplit(".", 1)[0].lower()
                if len(bare) >= 3:
                    self.to_alias.setdefault(bare, alias)
        return self.to_alias[key]

    def real(self, alias: str) -> str | None:
        return self.to_real.get(alias.strip())

    def scrub(self, text: str) -> str:
        """Replace keys, UUIDs and known real names with aliases."""
        text = KEY_PATTERN.sub("[redacted]", text)
        text = UUID_PATTERN.sub("[id]", text)
        pattern = _pattern(self.to_alias)
        return pattern.sub(lambda m: self.to_alias[m.group(1).lower()], text) if pattern else text

    def restore(self, text: str) -> str:
        """Replace aliases with real names, for text shown to the user."""
        pattern = _pattern(self.to_real)
        if not pattern:
            return text
        return pattern.sub(lambda m: self.to_real.get(m.group(1), m.group(1)), text)
