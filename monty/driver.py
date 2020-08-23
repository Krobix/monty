import ast
import builtins
from dataclasses import dataclass, field
from typing import Union, TextIO, List, Dict, Optional, Tuple
from io import IOBase

import monty
from monty.language import Scope, Function, Item
from monty.errors import CompilationException
from monty.diagnostic import Diagnostic, Error
from monty.mir import Ebb, ModuleBuilder
from monty.typechecker import InferenceEngine, Primitive, TypeId, constraints

SourceInput = Union[TextIO, str]


@dataclass
class CompilationUnit:
    type_ctx: InferenceEngine = field(default_factory=InferenceEngine)

    modules: Dict[str, ModuleBuilder] = field(default_factory=dict)

    _functions: Dict[str, Function] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        type_id = self.type_ctx.insert(Primitive.Unknown)
        assert type_id == 0, f"Failed to slot Primitive.Unknown at type_id 0!"

    @property
    def tcx(self) -> InferenceEngine:
        return self.type_ctx

    def get_primitives(self) -> Dict[str, TypeId]:
        i64 = self.type_ctx.get_id_or_insert(Primitive.I64)

        return {
            "int": i64,
            "i64": i64,
            "i32": self.type_ctx.get_id_or_insert(Primitive.I32),
            "none": self.type_ctx.get_id_or_insert(Primitive.Nothing),
            "bool": self.type_ctx.get_id_or_insert(Primitive.Bool),
        }

    def reveal_type(self, node: ast.AST, ribs) -> Optional[TypeId]:
        """Attempt to reveal the [product] type of a AST node."""

        # print("@", ast.dump(node), ribs)

        if isinstance(node, ast.BinOp):
            op = node.op

            if isinstance(op, ast.Add):
                op = constraints.Operation.Add
            elif isinstance(op, ast.Sub):
                op = constraints.Operation.Sub
            else:
                assert False

            lhs = self.reveal_type(node.left, ribs)
            rhs = self.reveal_type(node.right, ribs)

            lty = self.type_ctx[lhs]
            rty = self.type_ctx[rhs]

            if lty == Primitive.I64 and rty == Primitive.I64:
                return self.type_ctx.get_id_or_insert(Primitive.I64)
            else:
                raise RuntimeError(f"{lty}, {rty}")

        elif isinstance(node, ast.Compare):
            return self.type_ctx.get_id_or_insert(Primitive.Bool)
            # left = self.reveal_type(node.left)
            # left_ty = self.type_ctx[left]

            # for op, thing, in zip(node.ops, node.comparators):
            #     return self.type_ctx.get_id_or_insert(left_ty)
            # else:
            #     raise RuntimeError(f"{lty}, {rty}")

        elif isinstance(node, ast.Constant):
            return self.resolve_annotation(Scope(node), node)

        elif isinstance(node, ast.Name):
            assert isinstance(node.ctx, ast.Load), f"{ast.dump(node)}"
            target = node.id

            for stack in ribs[::-1]:
                if target in stack:
                    return self.type_ctx.get_id_or_insert(stack[target])

        raise RuntimeError(f"We don't know jack... {ast.dump(node)}")

    def resolve_annotation(
        self,
        scope: Scope,
        ann_node: Union[ast.Str, ast.Subscript, ast.Name, ast.Attribute],
    ) -> TypeId:
        if isinstance(ann_node, ast.Str):
            tree = ast.parse(ann_node, mode="eval")
            assert isinstance(
                tree, (ast.Subscript, ast.Name, ast.Attribute, ast.Constant)
            ), ast.dump(tree)
        else:
            tree = ann_node

        def check_parent_scope(parent_scope: Scope) -> Optional[TypeId]:
            return None

        def check_builtins() -> Optional[TypeId]:

            builtin_map = {
                int: Primitive.I64,
                float: Primitive.Number,
                bool: Primitive.Bool,
                type(None): Primitive.None_,
            }

            if isinstance(tree, ast.Constant):
                value = tree.value
                assert value is None or isinstance(value, (str, int))

                kind = builtin_map.get(type(value), Primitive.Unknown)

                return self.type_ctx.get_id_or_insert(kind)

            elif isinstance(tree, ast.Name) and (builtin := getattr(builtins, tree.id)):
                assert isinstance(tree.ctx, ast.Load)

                if (ty := builtin_map.get(builtin, None)) is None:
                    raise Exception("Unsupported builtin type!")

                return self.type_ctx.get_id_or_insert(ty)

            else:
                return None

        return (
            check_parent_scope(scope.parent)
            or check_builtins()
            or self.type_ctx.get_id_or_insert(Primitive.Unknown)
        )

    def get_function(self, name: str) -> Optional[Function]:
        module, name, *_ = name.split(".", maxsplit=1)

        for item in self.modules[module].walk_function_items():
            if (func := item.function).name == name:
                return func
        else:
            return None


def compile_source(
    source_input: SourceInput, *, module_name: str = "__main__"
) -> CompilationUnit:
    if isinstance(source_input, IOBase):
        root = ast.parse(source_input.read())

    elif isinstance(source_input, str):
        root = ast.parse(source_input)

    else:
        raise TypeError(
            f"Expected `source_input` to be a string or file-like object, instead got {type(source_input)!r}"
        )

    assert isinstance(root, ast.Module), f"Can only process modules!"

    root_item = Item(ty=Primitive.Module, node=root)

    if issues := root_item.validate():
        raise CompilationException(issues)

    unit = CompilationUnit()
    unit.get_primitives()
    unit.modules[module_name] = ModuleBuilder(unit, root_item)

    if issues := monty.typechecker.typecheck(item=root_item, unit=unit):
        raise CompilationException(issues)

    # TODO: Lowering AST/Items (root_items) into HIR/Items (lowered_root)
    # lowered_root = lower_into_hir(root_items)

    for builder in unit.modules.values():
        builder.output = builder.lower_into_mir()

    # print(unit.modules["__main__"].output["main"])

    return unit
