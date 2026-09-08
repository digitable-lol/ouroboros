"""Types for the part of the flang runtime the bridge speaks to.

The runtime beside this stub is printed by the compiler and carries no
annotations, so the type checker reads this file instead. It is deliberately
partial: it declares the names ``ouroboros/brain/__init__.py`` uses and nothing
else, so reaching for an undeclared one is an error the checker names rather
than a silently untyped call. ``tests/test_brain.py`` holds every name here
against the printed runtime, so a compiler upgrade that renames one of them
turns the tests red instead of the types quietly lying.
"""

from typing import Any

TAG_NOTHING: int
TAG_NUMBER: int
TAG_FLAG: int
TAG_STRING: int
TAG_LIST: int
TAG_RECORD: int
TAG_VARIANT: int

class FlangError(Exception):
    code: str
    message: str

class Value:
    tag: int
    data: Any
    name: str
    end: int | None

class Ctx:
    depth: int
    steps: int
    max_steps: int
    max_depth: int
    index_base: int

def new_ctx() -> Ctx: ...
def nothing() -> Value: ...
def number(value: float) -> Value: ...
def flag(value: bool) -> Value: ...
def text(value: str) -> Value: ...
def list_of(items: list[Value]) -> Value: ...
def record(fields: dict[str, Value]) -> Value: ...
def variant(name: str, fields: dict[str, Value]) -> Value: ...
def list_items(value: Value) -> list[Value]: ...
def list_length(value: Value) -> int: ...
def variant_is(value: Value, name: str) -> bool: ...
def describe(value: Value) -> str: ...
def type_name(value: Value) -> str: ...
def equal(left: Value, right: Value) -> bool: ...
