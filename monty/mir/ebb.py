from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import count
from typing import NamedTuple, Tuple, Dict, final, List, Optional, Any

from ..typechecker import TypeId
from . import VariableId, BlockId, BlockInstr, SSAValue, InstrOp

__all__ = ("Ebb", "BasicBlock", "FluidBlock")

_NULL = object()


@final
class BasicBlock(NamedTuple):
    """A single basic block."""

    body: Tuple[BlockInstr]
    parameters: Dict[SSAValue, TypeId]


@final
class Ebb(NamedTuple):
    """A collection of basic blocks."""

    # Ebb(i64, i64) -> i64
    parameters: Tuple[TypeId]
    return_value: TypeId

    # Ebb.variables.{variable_id} -> i64
    variables: Dict[VariableId, TypeId]

    # Ebb.block_id.{block_id} = {b0: iconst.i64(1), b1: iconst.i64(1), b2: iadd(v1, v2)}
    blocks: Dict[BlockId, BasicBlock]

    def ssa_body_mapping(self) -> Dict[SSAValue, BlockInstr]:
        return {
            instr.ret: instr
            for block in self.blocks.values()
            for instr in block.body
            if isinstance(instr.ret, SSAValue)
        }

    def sorted_blocks(self) -> List[Tuple[BlockId, BasicBlock]]:
        return sorted(self.blocks.items())


@dataclass
class FluidBlock:
    """An ebb that is being formed."""

    parameters: List[TypeId] = field(default_factory=list)
    returns: Optional[TypeId] = None

    variables: Dict[VariableId, TypeId] = field(default_factory=dict)
    blocks: Dict[BlockId, BasicBlock] = field(default_factory=dict)
    ssa_value_types: Dict[SSAValue, TypeId] = field(default_factory=dict)

    __cursor: Optional[BlockId] = field(init=False, default=None)
    __last_ssa_value: int = -1

    @property
    def _cursor(self) -> BasicBlock:
        return self.blocks[self.__cursor]

    def _emit(self, *, op, args, ret: Any = _NULL) -> SSAValue:
        if ret is _NULL:
            self.__last_ssa_value += 1
            slot = self.__last_ssa_value
        else:
            slot = ret

        instr = BlockInstr(op=op, args=args, ret=slot)
        self._cursor.body.append(instr)
        return slot

    def _typecheck(self, value: SSAValue, expected: TypeId):
        actual = self.ssa_value_types[value]
        if actual != expected:
            raise TypeError(f"{value=!r} had type {actual=!r} but {expected=!r}")

    def finalize(self) -> Ebb:
        """Produce a finalized EBB."""
        parameters = self.parameters[:]
        return_value = self.returns
        variables = {**self.variables}
        blocks = {**self.blocks}

        assert isinstance(return_value, int), f"{return_value=!r}"

        return Ebb(
            parameters=parameters,
            return_value=return_value,
            variables=variables,
            blocks=blocks,
        )

    # BasicBlock methods

    def switch_to_block(self, block_id: BlockId):
        """Switch the cursor to a block."""
        self.__cursor = block_id

    def create_block(self, *, parameters: Dict[SSAValue, TypeId] = None) -> BlockId:
        """Create a new block."""
        block_id = max(self.blocks) + 1 if self.blocks else 0
        parameters = parameters or {}

        block = BasicBlock(body=[], parameters=parameters)
        self.blocks[block_id] = block

        return block_id

    @contextmanager
    def with_block(self):
        """Create a new block, switch to it and then switch back."""
        previous_block_id = self.__cursor
        new_block_id = self.create_block()
        self.switch_to_block(new_block_id)
        yield new_block_id
        self.switch_to_block(previous_block_id)

    # Instruction emitting methods

    def int_const(self, value: int, bits: int = 64, signed: bool = True) -> SSAValue:
        """Produce an integer constant."""
        return self._emit(op=InstrOp.IntConst, args=[value, bits, signed])

    def bool_const(self, value: bool) -> SSAValue:
        """Produce a boolean constant."""
        return self._emit(op=InstrOp.BoolConst, args=[value])

    # Data-casting

    def cast_bool_to_int(self, ty: TypeId, value: SSAValue) -> SSAValue:
        """cast a boolean value to an integer one of some type."""
        return self._emit(op=InstrOp.BInt, args=[ty, value])

    # Arithmetic operations

    def int_add(self, left: SSAValue, right: SSAValue) -> SSAValue:
        """Add two integer values."""
        return self._emit(op=InstrOp.IAdd, args=[left, right])

    def int_sub(self, left: SSAValue, right: SSAValue) -> SSAValue:
        """Add two integer values."""
        return self._emit(op=InstrOp.IAdd, args=[left, right])

    def icmp(self, op: str, lhs: SSAValue, rhs: SSAValue) -> SSAValue:
        """Perform an integer-based comparison."""
        return self._emit(op=InstrOp.IntCmp, args=[op, lhs, rhs])

    # Variable operations

    def use(self, ident: "T") -> SSAValue:
        """use a variable as an ssa value."""
        return self._emit(op=InstrOp.UseVar, args=[ident])

    def assign(self, ident: "T", value: SSAValue, ty: TypeId):
        """Assign a value to a variable."""
        self._typecheck(value, expected=ty)
        self.variables[ident] = ty
        return self._emit(op=InstrOp.Assign, args=[value], ret=ident)

    # Control-flow

    def nop(self):
        """Emit a no-op."""
        self._emit(op=InstrOp.NoOp, args=[])

    def jump(self, target: BlockId):
        """Jump to a target block."""
        self._emit(op=InstrOp.Jump, args=[target], ret=None)

    def branch_icmp(self, mode: str, left: SSAValue, right: SSAValue, target: BlockId) -> SSAValue:
        return self._emit(op=InstrOp.BranchIntCmp, args=[mode, left, right, target])

    def return_(self, value: SSAValue):
        """Return from the function with a value."""
        self._typecheck(value, expected=self.returns)
        return self._emit(op=InstrOp.Return, args=[value], ret=None)
