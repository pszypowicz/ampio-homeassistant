"""Contract tests for the English translation.

``translations/en.json`` mirrors ``strings.json``. A person maintains it by
hand, expanding each ``[%key:...%]`` reference to its English text.
hassfest does not compare the two files. These tests do.
"""

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
STRINGS = ROOT / "custom_components/ampio/strings.json"
ENGLISH = ROOT / "custom_components/ampio/translations/en.json"

# A value of this shape points at a Home Assistant common string. The
# hand-maintained file carries the resolved English text, so the two
# legitimately differ there and nowhere else.
REFERENCE_PREFIX = "[%key:"


def _flatten(source: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """The leaf strings of a nested translation file, by dotted path."""
    flat: dict[str, str] = {}
    for key, value in source.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{path}."))
        else:
            assert isinstance(value, str), (
                f"{path} is a {type(value).__name__}, not a string; a "
                f"translation file holds only nested objects and strings"
            )
            flat[path] = value
    return flat


def _load(path: Path) -> dict[str, str]:
    """One translation file, flattened."""
    return _flatten(json.loads(path.read_text(encoding="utf-8")))


def test_the_generated_translation_carries_every_key() -> None:
    """Neither file holds a key the other lacks."""
    strings = _load(STRINGS)
    english = _load(ENGLISH)

    missing = sorted(set(strings) - set(english))
    extra = sorted(set(english) - set(strings))

    assert not missing, (
        f"{ENGLISH} is missing {missing}; edit it in place to add them, "
        f"matching {STRINGS}"
    )
    assert not extra, (
        f"{ENGLISH} carries {extra}, which {STRINGS} does not declare; "
        f"edit it in place to remove them"
    )


def test_the_generated_translation_copies_every_literal() -> None:
    """A value that is not a common-string reference is copied through."""
    strings = _load(STRINGS)
    english = _load(ENGLISH)

    drifted = sorted(
        key
        for key, value in strings.items()
        if not value.startswith(REFERENCE_PREFIX)
        and key in english
        and english[key] != value
    )

    assert not drifted, (
        f"{ENGLISH} disagrees with {STRINGS} on {drifted}; edit {ENGLISH} "
        f"in place to match the {STRINGS} text"
    )


def test_the_generated_translation_resolves_every_reference() -> None:
    """A common-string reference is expanded, never copied through raw."""
    strings = _load(STRINGS)
    english = _load(ENGLISH)

    unexpanded = sorted(
        key
        for key, value in strings.items()
        if value.startswith(REFERENCE_PREFIX)
        and key in english
        and english[key].startswith(REFERENCE_PREFIX)
    )

    assert not unexpanded, (
        f"{ENGLISH} still carries the raw reference for {unexpanded}; edit "
        f"it in place and expand the [%key:...%] reference to its English "
        f"text"
    )
