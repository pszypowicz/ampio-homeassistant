"""Contract tests for the translation files.

``translations/en.json`` mirrors ``strings.json``. A person maintains it by
hand, expanding each ``[%key:...%]`` reference to its English text.
hassfest does not compare the two files. These tests do.

Source contracts also require declarations for literal entity, device,
and exception keys. File comparisons cannot catch a key missing from both
files, which leaves entities or errors without their intended text.
"""

import ast
import json
from pathlib import Path
from typing import Any

from custom_components.ampio.const import PLATFORMS

ROOT = Path(__file__).parent.parent
STRINGS = ROOT / "custom_components/ampio/strings.json"
ENGLISH = ROOT / "custom_components/ampio/translations/en.json"
PLATFORM_DIR = ROOT / "custom_components/ampio"

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


def _requested_translation_keys(source: str) -> dict[str, set[str]]:
    """Collect literal entity and exception keys from declarations and calls.

    Dynamic keys are outside this scan. Exception calls pass both
    ``translation_domain`` and a literal ``translation_key``.
    """
    keys: dict[str, set[str]] = {"entity": set(), "exceptions": set()}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", "")
            )
            if any(kw.arg == "translation_domain" for kw in node.keywords):
                section = "exceptions"
            elif name.endswith("EntityDescription"):
                section = "entity"
            else:
                continue
            for kw in node.keywords:
                if (
                    kw.arg == "translation_key"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    keys[section].add(kw.value.value)
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if not isinstance(node.value.value, str):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    target_name = target.id
                elif isinstance(target, ast.Attribute):
                    target_name = target.attr
                else:
                    continue
                if target_name == "_attr_translation_key":
                    keys["entity"].add(node.value.value)
    return keys


def test_every_entity_translation_key_the_code_requests_is_declared() -> None:
    """A translation key a platform module asks for exists under its domain.

    Static: this parses each platform module's source text and never
    imports a platform module or builds an entity, which is what lets it
    see the entities the default test fixtures never construct, such as
    the administrator-only module entities (the Identify button, the
    module sensors, the buzzer, the panel entities). What it cannot see is
    a key assembled from anything other than a string literal; every
    entity translation key in this integration is a literal today.
    """
    declared: dict[str, set[str]] = {
        domain: set(names)
        for domain, names in json.loads(STRINGS.read_text(encoding="utf-8"))[
            "entity"
        ].items()
    }

    missing: list[str] = []
    for platform in PLATFORMS:
        domain = platform.value
        source = (PLATFORM_DIR / f"{domain}.py").read_text(encoding="utf-8")
        undeclared = sorted(
            _requested_translation_keys(source)["entity"] - declared.get(domain, set())
        )
        missing.extend(f"entity.{domain}.{key}" for key in undeclared)

    assert not missing, (
        f"strings.json declares no entity name for {missing}; add it under "
        f"'entity' so the entity does not fall back to showing its object id"
    )


def test_every_exception_translation_key_the_code_requests_is_declared() -> None:
    """Every literal error key has a declared exception message."""
    declared = set(json.loads(STRINGS.read_text(encoding="utf-8"))["exceptions"])
    missing: set[str] = set()
    for path in sorted(PLATFORM_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        missing.update(_requested_translation_keys(source)["exceptions"] - declared)

    assert not missing, (
        f"strings.json declares no exception message for {sorted(missing)}. "
        "Add each key under 'exceptions' so errors show their translated message."
    )


def _is_device_info_subscript(node: ast.expr) -> bool:
    """Whether a subscript target's own name ends in ``device_info``.

    Narrows the subscript shape below to the variable this integration
    actually uses (``device_info["translation_key"] = ...``), so an
    unrelated mapping that happens to carry a ``"translation_key"`` entry
    elsewhere in the package does not read as a device key.
    """
    if isinstance(node, ast.Name):
        return node.id.endswith("device_info")
    return isinstance(node, ast.Attribute) and node.attr.endswith("device_info")


def _requested_device_translation_keys(source: str) -> set[str]:
    """The literal device translation keys one module's source asks for.

    Matches a ``translation_key`` assigned into a variable named (or
    attributed) ``device_info``, by subscript (``device_info["translation_key"]
    = ...``), and a ``translation_key`` keyword passed to a call named
    ``DeviceInfo`` or ``ChildDeviceInfo``. A key built at run time from
    anything but a string literal is invisible to this scan.
    """
    keys: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if not isinstance(node.value.value, str):
                continue
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "translation_key"
                    and _is_device_info_subscript(target.value)
                ):
                    keys.add(node.value.value)
        elif isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", "")
            )
            if name not in {"DeviceInfo", "ChildDeviceInfo"}:
                continue
            for kw in node.keywords:
                if (
                    kw.arg == "translation_key"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    keys.add(kw.value.value)
    return keys


def test_every_device_translation_key_the_code_requests_is_declared() -> None:
    """A device translation key the code asks for exists under 'device'.

    The entity contract above is scoped to one platform module per domain,
    and a device translation key does not follow that shape, since
    ``entity.py`` sets one for an unnamed object's child device, on no
    platform of its own. This scans every module in the integration for
    the same two literal shapes, so it catches the one place this key is
    set today and any other module that starts setting one.
    """
    declared = set(json.loads(STRINGS.read_text(encoding="utf-8"))["device"])

    missing: list[str] = []
    for path in sorted(PLATFORM_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        undeclared = sorted(_requested_device_translation_keys(source) - declared)
        missing.extend(f"device.{key}" for key in undeclared)

    assert not missing, (
        f"strings.json declares no device name for {missing}; add it under "
        f"'device' so the device does not fall back to showing its raw id"
    )
