from __future__ import annotations

from inspect import isclass
from types import UnionType
from typing import TYPE_CHECKING, Any, TypeGuard, Union

from msgspec import NODEFAULT, Struct, convert, to_builtins
from msgspec.structs import fields
from pydantic import BaseModel, create_model
from pydantic.fields import FieldInfo as PFieldInfo
from starlite.plugins import PluginProtocol

if TYPE_CHECKING:
    from msgspec.structs import FieldInfo as MFieldInfo

TYPE_CONSTRUCTOR_MAP: dict[type, type] = {dict: dict, list: list, set: set, UnionType: Union}


class MsgspecPlugin(PluginProtocol):
    """Used to convert between Msgspec and Pydantic models."""

    def __init__(self) -> None:
        self._struct_namespace_map: dict[str, type[Struct]] = {}

    @staticmethod
    def is_plugin_supported_type(value: Any) -> TypeGuard[Struct]:
        try:
            return issubclass(value, Struct)
        except TypeError:
            return False

    def handle_nested_stuct(
        self,
        struct: type[Struct],
        parent_name: str,
        parent_nested_structs: list[type[Struct]],
    ) -> None:
        """Check if a given struct has been handled."""
        nested_struct_name = struct.__name__
        if nested_struct_name != parent_name and nested_struct_name not in self._struct_namespace_map:
            parent_nested_structs.append(struct)

    def is_complex_type(self, type_: Any) -> bool:
        """Check a type is an annoted complex type, i.e. `Union[str, int, None]`."""

        return hasattr(type_, "__origin__") and type_.__origin__ in (list, set) or type_.__class__ is UnionType

    def get_complex_type(
        self,
        type_: type[list] | type[set] | UnionType,
        parent_name: str,
        parent_nested_structs: list[type[Struct]],
    ) -> list[Any]:
        type_root = type_.__origin__ if hasattr(type_, "__origin__") else type_.__class__

        type_args = []
        for t in type_.__args__:
            if isclass(t) and issubclass(t, Struct):
                self.handle_nested_stuct(struct=t, parent_name=parent_name, parent_nested_structs=parent_nested_structs)
                type_args.append(t.__name__)
            elif t is None.__class__:
                type_args.append(None)
            elif self.is_complex_type(t):
                type_args.append(
                    self.get_complex_type(type_=t, parent_name=parent_name, parent_nested_structs=parent_nested_structs)
                )
            else:
                type_args.append(t)
        return TYPE_CONSTRUCTOR_MAP[type_root][*type_args]

    def generate_field_info(self, field: MFieldInfo) -> PFieldInfo:
        """Convert `msgspec.structs.FieldInfo` to `pydantic.FieldInfo`."""

        field_info_kwargs: dict[str, Any] = {}
        if hasattr(field, "encode_name"):
            field_info_kwargs["alias"] = field.encode_name
        if field.default is not NODEFAULT:
            field_info_kwargs["default"] = field.default
        if field.default_factory is not NODEFAULT:
            field_info_kwargs["default_factory"] = field.default_factory

        return PFieldInfo(**field_info_kwargs)

    def to_pydantic_model_class(self, model_class: type[Struct], **kwargs: Any) -> type[BaseModel]:
        struct_name = model_class.__name__
        if struct_name not in self._struct_namespace_map:
            field_definitions: dict[str, Any] = {}
            nested_structs: list[type[Struct]] = []
            for field in fields(model_class):
                if isclass(field.type) and issubclass(field.type, Struct):
                    self.handle_nested_stuct(
                        struct=field.type, parent_name=struct_name, parent_nested_structs=nested_structs
                    )
                    field_definitions[field.name] = (field.type.__name__, self.generate_field_info(field))

                elif self.is_complex_type(field.type):
                    pydantic_type = self.get_complex_type(
                        type_=field.type, parent_name=struct_name, parent_nested_structs=nested_structs
                    )
                    field_definitions[field.name] = (pydantic_type, self.generate_field_info(field))

                else:
                    field_definitions[field.name] = (field.type, self.generate_field_info(field))
            self._struct_namespace_map[struct_name] = create_model(
                struct_name,
                __config__=type(
                    "Config",
                    (),
                    {
                        "orm_mode": True,
                        "arbitrary_types_allowed": True,
                        "use_enum_values": True,
                        "allow_population_by_field_name": True,
                    },
                ),
                **field_definitions,
            )
            for nested_struct in nested_structs:
                self.to_pydantic_model_class(model_class=nested_struct)
        model = self._struct_namespace_map[struct_name]
        model.update_forward_refs(**self._struct_namespace_map)
        return model

    def from_pydantic_model_instance(self, model_class: type[Struct], pydantic_model_instance: BaseModel) -> Struct:
        return convert(pydantic_model_instance, model_class, from_attributes=True)

    def to_dict(self, model_instance: Struct) -> dict[str, Any]:
        return to_builtins(model_instance)

    def from_dict(self, model_class: type[Struct], **kwargs: Any) -> Any:
        return model_class(**kwargs)
