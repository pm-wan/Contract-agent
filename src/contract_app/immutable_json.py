"""Immutable containers and copy helpers for JSON-compatible snapshots."""

from __future__ import annotations

from typing import Any, NoReturn, Self


def _reject_mutation(*args: Any, **kwargs: Any) -> NoReturn:
    raise TypeError("frozen_json_value_is_immutable")


class FrozenJsonObject(dict[str, Any]):
    __setitem__ = _reject_mutation
    __delitem__ = _reject_mutation
    clear = _reject_mutation
    pop = _reject_mutation
    popitem = _reject_mutation
    setdefault = _reject_mutation
    update = _reject_mutation
    __ior__ = _reject_mutation

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> Self:
        memo[id(self)] = self
        return self


class FrozenJsonArray(list[Any]):
    __setitem__ = _reject_mutation
    __delitem__ = _reject_mutation
    __iadd__ = _reject_mutation
    __imul__ = _reject_mutation
    append = _reject_mutation
    clear = _reject_mutation
    extend = _reject_mutation
    insert = _reject_mutation
    pop = _reject_mutation
    remove = _reject_mutation
    reverse = _reject_mutation
    sort = _reject_mutation

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> Self:
        memo[id(self)] = self
        return self


def freeze_json(value: Any) -> Any:
    """Recursively copy JSON objects and arrays into read-only built-in subclasses."""
    if isinstance(value, dict):
        return FrozenJsonObject({key: freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return FrozenJsonArray(freeze_json(item) for item in value)
    return value


def thaw_json(value: Any) -> Any:
    """Return detached, mutable JSON values suitable for persistence or callers."""
    if isinstance(value, dict):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw_json(item) for item in value]
    return value
