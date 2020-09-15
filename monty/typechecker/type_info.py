from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import IntEnum, auto
from typing import Set

import monty
from monty.errors import TypeCheckError

__all__ = ("TypeId", "TypeInfo", "Primitive", "List", "Callable", "Ref")

TypeId = int


class TypeInfo:
    """Base class for all comprehensible types."""

    def reconstruct(self, tcx: "InferenceEngine") -> str:
        """Produce a locally constructed representation of the type."""
        raise NotImplementedError("Failed to implement reconstruct.")

    def size(self) -> int:
        """Get the size of this type in bytes."""
        return 0


class Primitive(TypeInfo, IntEnum):
    """Primitive types that do not compound or have any special semantics (apart from `Unknown`)."""

    Unknown = 0  # NOTICE! Primitive.Unknown is a special cased type always slotted to 0

    Bool = auto()
    Number = auto()
    LValue = auto()
    Module = auto()
    Return = auto()
    Integer = auto()
    Nothing = auto()
    None_ = auto()

    I64 = auto()
    I32 = auto()

    def reconstruct(self, tcx) -> str:
        return self.name

    def size(self) -> int:
        """Get the size of this type in bytes."""
        return {
            self.Bool: 1,
            self.I64: 8,
            self.I32: 4,
            self.None_: 1,
            self.Nothing: 0,
            self.Integer: 4,
            self.Unknown: 0,
        }[self]


@dataclass
class List(TypeInfo):
    """List are monomorphic, but inference allows dynamic creation of sum types for the inner kind."""

    kind: TypeId

    def reconstruct(self, tcx: "InferenceEngine") -> str:
        return f"List[{tcx.reconstruct(self.kind)}]"


@dataclass
class Callable(TypeInfo):
    """Functions, lambda's, classes, etc...Anything implementing `__call__`."""

    parameters: TypeId = field(default=Primitive.Unknown)
    output: TypeId = field(default=Primitive.Unknown)

    def reconstruct(self, tcx: "InferenceEngine") -> str:
        return f"Callable[{tcx.reconstruct(self.parameters)}, {tcx.reconstruct(self.output)}]"


@dataclass
class Ref(TypeInfo):
    """A reference type is used as a form of indirection when unifying types in the inference engine."""

    target: TypeId

    def reconstruct(self, tcx):
        return tcx.reconstruct(self.target)


@dataclass
class TypeVar(TypeInfo):
    """A type variable."""

    constraints: Set["TypeVarConstraint"] = field(default_factory=set)
