"""Shared API schema base: JSON is camelCase on the wire, snake_case in Python."""

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


def _has_nul(value: Any) -> bool:
    """U+0000 anywhere in a decoded JSON value (strings, keys, lists, objects); iterative, so depth is no risk."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if "\x00" in item:
                return True
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list | tuple):
            stack.extend(item)
    return False


class RequestModel(ApiModel):
    # Unknown fields (including any client-supplied userId) are rejected, not ignored.
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")
    # Fields whose own validator removes control characters (support requests): exempt from the NUL rule.
    nul_cleaned_fields: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def _refuse_nul(cls, data: Any) -> Any:
        """PostgreSQL text cannot hold U+0000: a request carrying it is a 422, never a database error (500)."""
        cleaned = set(cls.nul_cleaned_fields) | {to_camel(name) for name in cls.nul_cleaned_fields}
        items = data.items() if isinstance(data, dict) else [(None, data)]
        if any(key not in cleaned and (_has_nul(key) or _has_nul(value)) for key, value in items):
            raise ValueError("text must not contain U+0000")
        return data


def check_set_reset(changes: BaseModel, reset: list[str]) -> None:
    """The rules every versioned edit shares (docs/api-contract.md, "Versioned edits"): `null`
    is not a value (reset the field instead), and each field has at most one meaning."""
    explicit = changes.model_fields_set
    if any(getattr(changes, name) is None for name in explicit):
        raise ValueError("null is not a value in `set`; list the field in `reset` instead")
    fields = type(changes).model_fields
    if {fields[name].alias or name for name in explicit} & set(reset):
        raise ValueError("a field cannot be both set and reset")
    if len(set(reset)) != len(reset):
        raise ValueError("a field is listed twice in `reset`")
