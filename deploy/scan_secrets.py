#!/usr/bin/env python3
"""The pre-commit secret scan (CLAUDE.md "Dev standards": "Pre-commit: ruff,
mypy, secret scan").

ONE DEFINITION OF WHAT A SECRET LOOKS LIKE. The patterns come from
`investment.redact`, the module that keeps credentials out of logs and EventLog
payloads — so a shape added there because it leaked once is also a shape this
refuses to commit, without anyone remembering to update a second list. A
separate regex set here is exactly the drift CLAUDE.md's "WHEN A SECOND ONE
ARRIVES" rule warns about.

`# pragma: allowlist secret` on the line exempts it. Test fixtures are
deliberately shaped like real credentials — `tests/test_redact.py` asserts that
a token-shaped string does NOT survive redaction, which it cannot do without
containing one — and a scanner with no way to say so is a scanner that gets
disabled.

Run over the STAGED files pre-commit hands it, one path per argument.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from investment.redact import SECRET_PATTERNS

ALLOW = "pragma: allowlist secret"

# WHY THE SHAPES ARE SHARED AND THE THRESHOLD IS NOT. Redaction and refusal pay
# opposite prices for a false positive: an over-redacted log line loses a word
# nobody needed, while an over-eager commit gate blocks work and gets disabled
# within a week. `redact`'s patterns are therefore loose on purpose — they match
# `key=` and `token=` with any value — and `_is_opaque` is what this side adds:
# the VALUE must also look like a credential rather than like code.
#
# Measured on this repository the first time it ran: 23 findings, 22 of them
# `sorted(..., key=lambda row: ...)` and `api_key=api_key`. A gate with that
# signal-to-noise is not a gate.
_MIN_VALUE_CHARS = 16
_CODE_MARKERS = (".", "(", ")", "[", ",", "lambda", "self", "settings")


def _is_opaque(matched: str) -> bool:
    """Whether the matched text's VALUE could be a credential rather than an
    expression: long, containing a digit, and free of the punctuation that only
    appears in code."""
    _, _, value = matched.partition("=")
    value = value or matched
    if len(value) < _MIN_VALUE_CHARS or not any(c.isdigit() for c in value):
        return False
    return not any(marker in value for marker in _CODE_MARKERS)


def scan(path: Path) -> list[str]:
    """The offending `file:line: text` for one file, else empty."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, FileNotFoundError):
        return []  # a binary or a deleted file carries no reviewable secret
    findings = []
    for number, line in enumerate(lines, start=1):
        if ALLOW in line:
            continue
        for pattern in SECRET_PATTERNS:
            match = pattern.search(line)
            if match and _is_opaque(match.group(0)):
                findings.append(f"{path}:{number}: looks like a credential — {match.group(0)[:40]}")
                break
    return findings


def main(argv: list[str]) -> int:
    findings = [f for arg in argv for f in scan(Path(arg))]
    for finding in findings:
        print(finding, file=sys.stderr)
    if findings:
        print(
            f"\n{len(findings)} possible secret(s) staged. Move the value to .env, or add "
            f"`# {ALLOW}` to the line if it is a fixture.",
            file=sys.stderr,
        )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
