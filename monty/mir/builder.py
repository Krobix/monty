import ast
from contextlib import contextmanager
from dataclasses import field, dataclass
from typing import Dict, Any, Iterator

from monty import typechecker
from monty.language import Item
from monty.typechecker import TypeId, TypeInfo, Primitive
from monty.utils import swapattr

from . import Ebb, SSAValue, FluidBlock

__all__ = ("ModuleBuilder", "MirBuilder")

CompilationUnit = "monty.driver.CompilationUnit"


@dataclass
class MirBuilder(ast.NodeVisitor):
    """An `ast.NodeVisitor` that can lower a Python `ast.FunctionDef` into MIR."""

    unit: CompilationUnit
    item: Item

    _ebb: FluidBlock = field(default_factory=FluidBlock)
    _ast_node_to_ssa: Dict[ast.AST, SSAValue] = field(default_factory=dict)

    @staticmethod
    def compile_item(item: Item, *, unit: CompilationUnit) -> Ebb:
        """Walk and lower a function item and ast into an Ebb."""
        if item.function is None:
            raise TypeError(f"language item was not a function! {item=!r}")

        assert item.function is not None
        assert isinstance(item.node, ast.FunctionDef)

        function = item.function

        callable_t = unit.tcx[function.type_id]

        assert isinstance(callable_t, typechecker.Callable)

        self = MirBuilder(unit, item)

        self._ebb.parameters += [callable_t.parameters]
        self._ebb.returns = callable_t.output

        with self._ebb.with_block():
            self.visit(function.node)

        return self._ebb.finalize()

    # Helpers

    @contextmanager
    def _visiting_names(self):
        def visit_name(self, name):
            assert isinstance(name.ctx, ast.Load)
            self._ast_node_to_ssa[name] = value = self._ebb.use(name.id)
            self._ebb.ssa_value_types[value] = self.unit.reveal_type(name, self.item.scope)

        with swapattr(self, "_visit_name", None, visit_name):
            yield

    # Visitors

    def visit_AnnAssign(self, assign):
        value_node = assign.value
        value_ty = self.unit.reveal_type(value_node, self.item.scope)

        self.generic_visit(assign)

        assign_value = self._ast_node_to_ssa[value_node]
        target_id = assign.target.id

        # variable_id = self.unit.get_variable_id(target_id, self.item)

        self._ebb.assign(ident=target_id, value=assign_value, ty=value_ty)

    def visit_Pass(self, _):
        self._ebb.nop()

    def visit_Return(self, ret):
        ret_value_node = ret.value

        with self._visiting_names():
            self.generic_visit(ret)

        ret_value = self._ast_node_to_ssa[ret_value_node]
        self._ebb.return_(value=ret_value)

    def visit_Name(self, name):
        # Hack to selectively encode ast.Name nodes.
        if callable(fn := getattr(self, "_visit_name", None)):
            assert callable(fn)
            fn(self, name)  # pylint: disable=not-callable

    def visit_Constant(self, const):
        assert const.kind is None, f"Unhandled case! {ast.dump(const)=!r}"
        assert type(const.value) in (
            int,
            bool,
        ), f"Only able to handle integer and string constants {ast.dump(const)=!r}"

        ty = type(value := const.value)

        if ty is str:
            _ = self.unit.intern_string(value)
            fn = self._ebb.str_const
            value_ty = self.unit.tcx.get_id_or_insert(Primitive.String)

        elif ty is bool:
            fn = self._ebb.bool_const
            value_ty = self.unit.tcx.get_id_or_insert(Primitive.Bool)

        elif ty is int:
            fn = self._ebb.int_const
            value_ty = self.unit.tcx.get_id_or_insert(Primitive.I64)

        else:
            assert False, f"Unknown fn handler for {ty=!r}!"

        self._ast_node_to_ssa[const] = ssa = fn(value)
        self._ebb.ssa_value_types[ssa] = value_ty

    def visit_BinOp(self, binop):
        with self._visiting_names():
            self.generic_visit(binop)

        lhs = self._ast_node_to_ssa[binop.left]
        rhs = self._ast_node_to_ssa[binop.right]

        ty = self.unit.reveal_type(binop, self.item.scope)

        assert (
            self.unit.type_ctx[ty] == Primitive.I64
        ), f"{self.unit.type_ctx.reconstruct(ty)!r}"

        kind = self.unit.type_ctx[ty]
        op = type(binop.op)

        if kind in (Primitive.I64, Primitive.I32, Primitive.Integer):
            fn = {ast.Add: self._ebb.int_add, ast.Sub: self._ebb.int_sub,}[op]

            value = fn(lhs, rhs)
            self._ebb.ssa_value_types[value] = self.unit.tcx.get_id_or_insert(Primitive.I64)
        else:
            raise Exception(f"Attempted BinOp on unknown kinds {ast.dump(binop)}")

        self._ast_node_to_ssa[binop] = value

    def visit_Call(self, call):
        # TODO: Name mangling...
        # name = self.resolve_name_to_mangled_form(name, ...)

        func = self.unit.resolve_into_function(call, self.item.scope)

        if func not in self._ebb.refs:
            func = self._ebb.reference(func)

        # TODO: Argument passing...

        result = self._ebb.call(func)

        self._ast_node_to_ssa[call] = result

    def visit_Compare(self, compare):
        left = compare.left

        with self._visiting_names():
            self.visit(left)

        result_ty = self.unit.reveal_type(left, self.item.scope)
        result = self._ast_node_to_ssa[left]

        i64 = self.unit.tcx.get_id_or_insert(Primitive.I64)

        for op, rvalue, in zip(compare.ops, compare.comparators):
            rvalue_type = self.unit.reveal_type(rvalue, self.item.scope)

            self.visit(rvalue)

            rvalue_ssa = self._ast_node_to_ssa[rvalue]
            rvalue_type = self.unit.tcx[rvalue_type]

            if rvalue_type == Primitive.Bool:
                rvalue_ssa = self._ebb.bint(i64, rvalue_ssa)
                rvalue_type = Primitive.I64

            if rvalue_type in (Primitive.I64, Primitive.I32, Primitive.Integer):
                ops = {ast.Eq: "eq", ast.NotEq: "neq", ast.Gt: "gt"}

                if (op := type(op)) not in ops:
                    raise Exception(f"Unknown op {ast.dump(compare)=!r}")
                else:
                    result = self._ebb.icmp(ops[op], result, rvalue_ssa)

                result = self._ebb.bint(i64, result)
                result_ty = i64
            else:
                raise Exception(f"Unkown rvalue type on comparator {rvalue_type=!r}")

        result_ty = self.unit.tcx[result_ty]

        if result_ty != Primitive.Bool and result in (
            Primitive.I64,
            Primitive.I32,
            Primitive.Integer,
        ):
            result = self._ebb.bool_const(result, is_ssa_value=True)

        self._ast_node_to_ssa[compare] = result

    def visit_If(self, if_):
        with self._visiting_names():
            self.visit(if_.test)

        expr_value = self._ast_node_to_ssa[if_.test]
    
        if self._ebb.ssa_value_types[expr_value] != (i64 := self.unit.tcx.get_id_or_insert(Primitive.I64)):
            expr_value = self._ebb.cast_bool_to_int(i64, expr_value)

        with self._ebb.with_block() as ident:
            for node in if_.body:
                self.visit(node)
            head = ident

        for node in if_.orelse:
            with self._ebb.with_block() as ident:
                self.visit(node)
                tail = ident

        one = self._ebb.int_const(1)
        self._ebb.branch_icmp("eq", expr_value, one, head)
        self._ebb.jump(tail)


@dataclass
class ModuleBuilder:
    """Used to construct a module."""

    unit: CompilationUnit
    root_item: Item
    output: Any = field(default=None)

    def walk_function_items(self) -> Iterator[Item]:
        seen = set()

        for sub in self.root_item.scope.items:
            if sub.function is not None and sub.function.node not in seen:
                yield sub
                seen.add(sub.function.node)

    def lower_into_mir(self) -> Dict[str, Ebb]:
        return {
            item.function.name: MirBuilder.compile_item(unit=self.unit, item=item)
            for item in self.walk_function_items()
        }
