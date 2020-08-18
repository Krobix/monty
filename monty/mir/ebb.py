from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import IntEnum, auto
from typing import Tuple, Optional, List, Dict, Set

from ..language import Function
from ..typechecker import TypeId


SSAValue = int
BlockId = int


class InstrOp(IntEnum):
    IntConst = auto()
    StrConst = auto()
    Assign = auto()
    Return = auto()
    NoOp = auto()
    IAdd = auto()
    UseVar = auto()
    Jump = auto()
    IntCmp = auto()
    BInt = auto()
    BoolConst = auto()
    BranchIntCmp = auto()

    def __str__(self) -> str:
        return self.name.lower()  # pylint: disable=no-member


@dataclass
class BlockInstr:
    op: InstrOp
    args: List[SSAValue]
    ret: Optional[SSAValue] = None


@dataclass
class BasicBlock:
    instructions: List[BlockInstr] = field(default_factory=list)
    parameters: List[Tuple[SSAValue, TypeId]] = field(default_factory=list)


@dataclass
class Ebb:
    """A collection of basic blocks used to form a routine."""
    parameters: List[TypeId] = field(default_factory=list)
    returns: List[TypeId] = field(default_factory=list)
    variables: Dict[str, TypeId] = field(default_factory=dict)

    blocks: Dict[BlockId, BasicBlock] = field(default_factory=dict)
    namespace: Set[str] = field(default_factory=set)

    last_ssa: SSAValue = field(init=False, default=0)

    _cursor: Optional[BlockId] = field(init=False, default=None)
    _cursor_pinned: bool = field(init=False, default=False)

    def _next_ssa_value(self) -> SSAValue:
        self.last_ssa += 1
        return self.last_ssa

    @property
    def cursor(self):
        return self.blocks[self._cursor]

    def sorted_blocks(self) -> List[Tuple[BlockId, BasicBlock]]:
        return sorted(self.blocks.items())

    def using_clean_block(self) -> BlockId:
        if not self._cursor_pinned and self._cursor is None or self.cursor.instructions:
            ident, _, = self.create_block()
            if self._cursor is not None:
                self.jump_to_block(ident)
            self.switch_to_block(ident)
        else:
            ident = self._cursor

        return ident

    @contextmanager
    def pin_head(self):
        self._cursor_pinned = True
        yield
        self._cursor_pinned = False

    def switch_to_block(self, ident: BlockId):
        if self._cursor_pinned:
            raise Exception("cursor is pinned!")

        self._cursor = ident

    def jump_to_block(self, ident: BlockId):
        self.cursor.instructions.append(BlockInstr(op=InstrOp.Jump, args=[ident], ret=None))

    def create_block(self) -> Tuple[BlockId, BasicBlock]:
        ident = (self.blocks and (max(self.blocks.keys()) + 1)) or 0
        block = BasicBlock()

        self.blocks[ident] = block

        return (ident, block)

    @contextmanager
    def with_block(self):
        ident, block, = self.create_block()
        current = self._cursor
        self.switch_to_block(ident)
        yield ident
        self.switch_to_block(current)

    def br_icmp(self, op: str, lhs: SSAValue, rhs: SSAValue, target: BlockId):
        self.cursor.instructions.append(
            BlockInstr(op=InstrOp.BranchIntCmp, args=[op, lhs, rhs, target])
        )

    def bint(self, ty: TypeId, value: SSAValue) -> SSAValue:
        slot = self._next_ssa_value()
        self.cursor.instructions.append(
            BlockInstr(op=InstrOp.BInt, args=[ty, value], ret=slot)
        )
        return slot

    def assign(self, name: str, value: SSAValue, ty: TypeId):
        self.namespace.add(name)
        self.variables[name] = ty
        self.cursor.instructions.append(
            BlockInstr(op=InstrOp.Assign, args=[value], ret=name)
        )

    def icmp(self, op: str, lhs: SSAValue, rhs: SSAValue) -> SSAValue:
        slot = self._next_ssa_value()
        self.cursor.instructions.append(
            BlockInstr(op=InstrOp.IntCmp, args=[op, lhs, rhs], ret=slot)
        )
        return slot

    def use_var(self, name: str) -> SSAValue:
        slot = self._next_ssa_value()
        self.cursor.instructions.append(
            BlockInstr(op=InstrOp.UseVar, args=[name], ret=slot)
        )
        return slot

    def str_const(self, ref: int) -> SSAValue:
        slot = self._next_ssa_value()
        self.cursor.instructions.append(
            BlockInstr(op=InstrOp.StrConst, args=[ref], ret=slot)
        )
        return slot

    def bool_const(self, value: bool, is_ssa_value: bool = False) -> SSAValue:
        slot = self._next_ssa_value()
        self.cursor.instructions.append(
            BlockInstr(op=InstrOp.BoolConst, args=[is_ssa_value, value], ret=slot)
        )
        return slot

    def int_const(self, value: int, bits: int = 64, signed: bool = True) -> SSAValue:
        slot = self._next_ssa_value()
        self.cursor.instructions.append(
            BlockInstr(op=InstrOp.IntConst, args=[value, bits, signed], ret=slot)
        )
        return slot

    def return_(self, value: int):
        self.cursor.instructions.append(BlockInstr(
            op=InstrOp.Return, args=[value], ret=None
        ))

    def iadd(self, lhs: int, rhs: int) -> SSAValue:
        slot = self._next_ssa_value()
        self.cursor.instructions.append(
            BlockInstr(op=InstrOp.IAdd, args=[lhs, rhs], ret=slot)
        )
        return slot

    def nop(self):
        self.cursor.instructions.append(BlockInstr(
            op=InstrOp.NoOp,
            args=[],
            ret=None
        ))
