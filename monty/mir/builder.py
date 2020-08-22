import ast
from dataclasses import field, dataclass
from typing import Dict, Any, Iterator

from monty.language import Item
from monty.mir import Ebb, SSAValue
from monty.typechecker import TypeId, TypeInfo, Callable, Primitive
from monty.utils import swapattr


@dataclass
class ModuleBuilder:
    unit: "monty.driver.CompilationUnit"
    root_item: Item
    output: Any = field(default=None)

    def walk_function_items(self) -> Iterator[Item]:
        seen = set()

        for sub in self.root_item.scope.items:
            if sub.function is not None and sub.function.node not in seen:
                yield sub
                seen.add(sub.function.node)

    def lower_into_mir(self) -> Dict[str, Ebb]:
        return {item.function.name: MirBuilder.compile_function(self.unit, item) for item in self.walk_function_items()}


@dataclass
class MirBuilder(ast.NodeVisitor):
    """Takes a regular AST and produces some MIR."""

    unit: "monty.driver.CompilationUnit"
    item: "mondy.language.Item"
    ebb: Ebb = field(default_factory=Ebb)

    nodes_to_ssa: Dict[ast.AST, SSAValue] = field(default_factory=dict)

    def __getattribute__(self, key):
        if key.startswith("visit_"):
            pass
            # print(f"gettattr(self, {key=!r})")

        return object.__getattribute__(self, key)

    @classmethod
    def compile_function(cls, unit: "monty.driver.CompilationUnit", item: Item) -> Ebb:
        self = cls(unit, item)
        func = item.function

        assert func.type_id is not None

        callable = unit.type_ctx[func.type_id]

        assert isinstance(callable, Callable)

        self.ebb.parameters += [callable.parameters]
        self.ebb.returns += [callable.output]

        self.ebb.using_clean_block()
        self.visit(func.node)

        return self.ebb

    def visit_AnnAssign(self, assign):
        self.ebb.using_some_block()

        self.generic_visit(assign)

        value = self.nodes_to_ssa[assign.value]

        target = assign.target.id

        self.ebb.assign(target, value, self.unit.reveal_type(assign.value, self.item.ribs))

    def visit_Pass(self, _):
        self.ebb.using_clean_block()
        self.ebb.nop()

    def visit_Compare(self, comp):
        left = comp.left

        # print(ast.dump(comp))

        def visit_name(self, name):
            assert isinstance(name.ctx, ast.Load)
            self.nodes_to_ssa[name] = self.ebb.use_var(name.id)

        with swapattr(self, "_visit_name", None, visit_name):
            self.visit(left)

        result_type = self.unit.reveal_type(left, self.item.ribs)
        result = self.nodes_to_ssa[left]

        for op, rvalue, in zip(comp.ops, comp.comparators):
            rvalue_type = self.unit.reveal_type(rvalue, self.item.ribs)

            # print(ast.dump(rvalue))

            self.visit(rvalue)

            rvalue_ssa = self.nodes_to_ssa[rvalue]
            rvalue_type = self.unit.type_ctx[rvalue_type]

            if rvalue_type == Primitive.Bool:
                i64 = self.unit.type_ctx.get_id_or_insert(Primitive.I64)
                rvalue_ssa = self.ebb.bint(i64, rvalue_ssa)
                rvalue_type = Primitive.I64

            if rvalue_type in (Primitive.I64, Primitive.I32, Primitive.Integer):
                ops = {
                    ast.Eq: "eq",
                    ast.NotEq: "neq",
                    ast.Gt: "gt",
                }

                if (op := type(op)) not in ops:
                    raise Exception(f"Unknown op {ast.dump(comp)=!r}")
                else:
                    result = self.ebb.icmp(ops[op], result, rvalue_ssa)

                i64 = self.unit.type_ctx.get_id_or_insert(Primitive.I64)
                result = self.ebb.bint(i64, result)
                result_type = i64
            else:
                raise Exception(f"Unkown rvalue type {rvalue_type=!r}")

        result_type = self.unit.type_ctx[result_type]

        # print(">>", result_type)

        if result_type != Primitive.Bool:
            if result_type in (Primitive.I64, Primitive.I32, Primitive.Integer):
                result = self.ebb.bool_const(result, is_ssa_value=True)

        self.nodes_to_ssa[comp] = result

    def visit_While(self, while_):
        def visit_name(self, name):
            assert isinstance(name.ctx, ast.Load)
            self.nodes_to_ssa[name] = self.ebb.use_var(name.id)

        with self.ebb.with_block() as head:
            with swapattr(self, "_visit_name", None, visit_name):
                self.visit(while_.test)

            value = self.nodes_to_ssa[while_.test]
            i64 = self.unit.type_ctx.get_id_or_insert(Primitive.I64)
            value = self.ebb.bint(i64, value)

            one = self.ebb.int_const(1)

            with self.ebb.with_block() as while_body:
                for node in while_.body:
                    self.visit(node)

                self.ebb.jump_to_block(head)

            self.ebb.br_icmp("eq", value, one, while_body)

        # Inline the while test in the current block then jump into the body

        with swapattr(self, "_visit_name", None, visit_name):
            self.visit(while_.test)

        value = self.nodes_to_ssa[while_.test]
        i64 = self.unit.type_ctx.get_id_or_insert(Primitive.I64)
        value = self.ebb.bint(i64, value)

        one = self.ebb.int_const(1)

        self.ebb.br_icmp("eq", value, one, while_body)
        self.ebb.using_clean_block()

        for block_id, block in self.ebb.blocks.items():
            print(f"\t{block_id=!r} =>")

            for instr in block.instructions:
                print(f"\t\t{instr=!r}")

        # raise Exception(f"{ast.dump(while_)=!r}")

    def visit_If(self, if_):
        # print(ast.dump(if_))

        assert isinstance(if_.test, ast.Name)

        def visit_name(self, name):
            assert isinstance(name.ctx, ast.Load)
            self.nodes_to_ssa[name] = self.ebb.use_var(name.id)

        with swapattr(self, "_visit_name", None, visit_name):
            self.visit(if_.test)

        expr_result = self.nodes_to_ssa[if_.test]
        expr_result = self.ebb.bint(self.unit.type_ctx.get_id_or_insert(Primitive.I64), expr_result)

        with self.ebb.with_block()  as ident:
            self.visit(if_.body[0])
            head = ident

        for node in if_.orelse:
            with self.ebb.with_block()  as ident:
                self.visit(node)
                tail = ident

        one = self.ebb.int_const(1)
        self.ebb.br_icmp("eq", expr_result, one, head)
        self.ebb.jump_to_block(tail)

    def visit_BinOp(self, binop):
        self.ebb.using_some_block()

        print(ast.dump(binop))

        def visit_name(self, name):
            assert isinstance(name.ctx, ast.Load)
            self.nodes_to_ssa[name] = self.ebb.use_var(name.id)

        with swapattr(self, "_visit_name", None, visit_name):
            self.generic_visit(binop)

        lhs = self.nodes_to_ssa[binop.left]
        rhs = self.nodes_to_ssa[binop.right]

        ty = self.unit.reveal_type(binop, self.item.ribs)

        assert self.unit.type_ctx[ty] == Primitive.I64, f"{self.unit.type_ctx.reconstruct(ty)!r}"

        kind = self.unit.type_ctx[ty]

        if kind in (Primitive.I64, Primitive.I32, Primitive.Integer):
            value = {
                ast.Add: self.ebb.iadd,
                ast.Sub: self.ebb.isub,
            }[type(binop.op)](lhs, rhs)
        else:
            raise Exception(f"Attempted BinOp on unknown kinds {ast.dump(binop)}")

        self.nodes_to_ssa[binop] = value

    def visit_Constant(self, const):
        assert const.kind is None, f"Unhandled case! {ast.dump(const)=!r}"
        assert type(const.value) in (int, str, bool), f"Only able to handle integer and string constants"

        ty = type(value := const.value)

        if ty is str:
            st_ref = self.unit.intern_string(value)
            value = self.ebb.str_const(st_ref)

        elif ty is bool:
            value = self.ebb.bool_const(value)

        else:
            assert ty is int
            value = self.ebb.int_const(value)

        self.nodes_to_ssa[const] = value

    def visit_Name(self, name):
        if callable(fn := getattr(self, "_visit_name", None)):
            fn(self, name)  # pylint: disable=not-callable

    def visit_Return(self, ret):
        def visit_name(self, name):
            assert isinstance(name.ctx, ast.Load)
            self.nodes_to_ssa[name] = self.ebb.use_var(name.id)

        with swapattr(self, "_visit_name", None, visit_name):
            self.generic_visit(ret)

        value = self.nodes_to_ssa[ret.value]
        self.ebb.return_(value)
