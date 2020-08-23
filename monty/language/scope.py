import ast
from dataclasses import dataclass, field
from enum import IntEnum, auto
from typing import List, Iterator, Union, Optional, Dict

from monty.typechecker import Callable, Primitive, TypeInfo

from .item import Item, ScopeableNodes

__all__ = ("Scope", "ScopeWalker")


@dataclass
class Scope:
    """Represents a semantic scope."""

    node: ast.AST
    items: List["Item"] = field(default_factory=list)
    parent: Optional["Scope"] = None
    ribs: List[Dict[str, TypeInfo]] = field(default_factory=list)
    module: Optional["Item"] = None

    def __hash__(self):
        return hash(self.node)

    @classmethod
    def from_node(cls, node: ScopeableNodes, *, module: Optional["Item"] = None) -> "Scope":
        walker = ScopeWalker(scope=(s := Scope(node, module=module)))
        walker.visit(s.node)
        return walker.scope

    @property
    def scopes(self) -> List["Scope"]:
        inner = []

        for item in self.items:
            if isinstance((func := item.node), ast.FunctionDef):
                inner.append(Scope.from_node(func))

        return inner

    def get_item_by_node(self, node: ast.AST) -> Optional["Item"]:
        for item in self.items:
            if item.node == node:
                return item
        else:
            return None


@dataclass
class ScopeWalker(ast.NodeVisitor):
    scope: Scope

    def add_item(self, item):
        if item.scope is not None:
            item.scope.parent = self.scope
            item.scope.module = self.scope.module

        self.scope.items.append(item)

    # Visitors

    def visit_Module(self, module):
        if self.scope.node is not module:
            self.add_item(Item(ty=Primitive.Module, node=module))

        self.generic_visit(module)

    def visit_FunctionDef(self, func):
        if func is self.scope.node:
            self.generic_visit(func)
        else:
            self.add_item(Item(node=func))

    def visit_ClassDef(self, klass):
        raise NotImplementedError("Classes are not supported!")

    def visit_Assign(self, assign):
        raise NotImplementedError("Regular assignment is not supported!")
        # self.add_item(Item(ty=Primitive.LValue, node=assign))

    def visit_AnnAssign(self, assign):
        self.add_item(Item(ty=Primitive.LValue, node=assign))

    def visit_AugAssign(self, assign):
        raise NotImplementedError("AugAssign is not supported!")

    def visit_Return(self, ret):
        self.add_item(Item(ty=Primitive.Return, node=ret))
