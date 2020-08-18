from dataclasses import dataclass
from enum import IntEnum, auto

from . import TypeId

__all__ = ("Operation",)


class Operation(IntEnum):
    Add = auto()
    Sub = auto()
