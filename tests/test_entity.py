"""Tests for the entity base classes of the Ampio integration."""

from importlib import import_module
import inspect

import pytest

from custom_components.ampio.const import PLATFORMS
from homeassistant.helpers.entity import Entity, _entity_class_requires_readd


def _entity_classes() -> list[type[Entity]]:
    """Every entity class the platform modules define."""
    classes: list[type[Entity]] = []
    for platform in PLATFORMS:
        module = import_module(f"custom_components.ampio.{platform}")
        classes.extend(
            cls
            for _, cls in inspect.getmembers(module, inspect.isclass)
            if issubclass(cls, Entity) and cls.__module__ == module.__name__
        )
    return classes


@pytest.mark.parametrize(
    "entity_class", _entity_classes(), ids=lambda cls: cls.__name__
)
def test_entity_id_change_keeps_the_entity(entity_class: type[Entity]) -> None:
    """Home Assistant moves every Ampio entity to a new entity id in place.

    Core removes and adds again an entity of a custom integration whose
    class defines an add or remove hook without ``async_entity_id_changed``
    on that class or a subclass of it. This calls core's own rule, so a
    class that gains a hook without the opt-in fails here.
    """
    assert not _entity_class_requires_readd(entity_class)
