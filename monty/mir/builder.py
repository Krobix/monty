import ast
from dataclasses import field, dataclass
from typing import Dict, Any, Iterator

from monty.language import Item
from monty.mir import Ebb
from monty.typechecker import TypeId, TypeInfo, Callable


@dataclass
class ModuleBuilder:
    unit: "monty.driver.CompilationUnit"
    root_item: Item
    output: Any = field(default=None)

    def walk_function_items(self) -> Iterator[Item]:
        for sub in self.root_item.scope.items:
            if sub.function is not None:
                yield sub

    def lower_into_mir(self) -> Dict[str, Ebb]:
        return {item.function.name: MirBuilder.compile_function(self.unit, item.function) for item in self.walk_function_items()}


@dataclass
class MirBuilder(ast.NodeVisitor):
    """Takes a regular AST and produces some MIR."""

    unit: "monty.driver.CompilationUnit"
    ebb: Ebb = field(default_factory=Ebb)

    @classmethod
    def compile_function(cls, unit: "monty.driver.CompilationUnit", func: "Function") -> Ebb:
        self = cls(unit)

        assert func.type_id is not None

        callable = unit.type_ctx[func.type_id]

        assert isinstance(callable, Callable)

        self.ebb.parameters += [callable.parameters]
        self.ebb.returns += [callable.output]

        self.visit(func.node)
        return self.ebb

    def visit_Assign(self, assign):
        self.ebb.using_clean_block()

        with self.ebb.pin_head():
            self.generic_visit(assign)

        target = assign.targets[0].id

        self.ebb.assign("rv", self.ebb.last_ssa)

    def visit_Pass(self, _):
        self.ebb.using_clean_block()
        self.ebb.nop()

    def visit_Constant(self, const):
        self.ebb.using_clean_block()

        assert const.kind is None, f"Unhandled case! {ast.dump(const)=!r}"
        assert isinstance(
            const.value, (int, str)
        ), f"Only able to handle integer and string constants"

        if isinstance(value := const.value, str):
            st_ref = self.unit.intern_string(value)
            self.ebb.str_const(st_ref)
        else:
            self.ebb.int_const(value)

    def visit_Return(self, ret):

        self.generic_visit(ret)

        self.ebb.return_(self.ebb.last_ssa)
