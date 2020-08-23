from monty.typechecker import Primitive, Ref, List, Callable, TypeInfo, TypeId
from monty.utils import SSAMap
from monty.errors import TypeCheckError

__all__ = ("InferenceEngine",)


class InferenceEngine(SSAMap[TypeInfo]):
    def __repr__(self) -> str:
        return f"<InferenceEngine: {self.mapping=!r}>"

    def unify(self, left: TypeId, right: TypeId):
        left_ty, right_ty, = self[left], self[right]

        if left_ty is Primitive.Unknown:
            self[left] = Ref(right)

        elif right_ty is Primitive.Unknown:
            self[right] = Ref(left)

        elif isinstance(left_ty, Ref):
            self.unify(left, left_ty.target)

        elif isinstance(right_ty, Ref):
            self.unify(right, right_ty.target)

        elif isinstance(left_ty, List) and isinstance(right_ty, List):
            self.unify(left_ty.kind, right_ty.kind)

        elif isinstance(left_ty, Callable) and isinstance(right_ty, Callable):
            self.unify(left_ty.parameters, right_ty.parameters)
            self.unify(left_ty.output, right_ty.output)

        else:
            raise TypeCheckError(f"Failed to unify ({left_ty} U {right_ty})")

    def get_id_or_insert(self, thing: TypeInfo) -> TypeId:
        return self.get_by_value(thing) or self.insert(thing)

    def reconstruct(self, type_id: TypeId) -> str:
        return self[type_id].reconstruct(self)

    def is_callable(self, type_id: TypeId) -> bool:
        type_info = self[type_id]
        return isinstance(type_info, Callable)
