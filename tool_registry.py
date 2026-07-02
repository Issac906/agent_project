"""Runtime registry for tools exposed by the patent application."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    category: str
    owner: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


_TOOL_REGISTRY: dict[str, ToolSpec] = {}


def register_tool(
    name: str,
    description: str,
    category: str = "Agent workflow",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a function or bound method as an application tool."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        spec = ToolSpec(
            name=name,
            description=description,
            category=category,
            owner=f"{func.__module__}.{func.__qualname__}",
        )
        _TOOL_REGISTRY[name] = spec
        setattr(func, "__tool_spec__", spec)
        return func

    return decorator


def registered_tools() -> list[ToolSpec]:
    return sorted(_TOOL_REGISTRY.values(), key=lambda item: (item.category, item.name))


def discover_bound_tools(instance: object) -> dict[str, tuple[ToolSpec, Callable[[], str]]]:
    tools: dict[str, tuple[ToolSpec, Callable[[], str]]] = {}
    for attribute_name in dir(instance):
        bound = getattr(instance, attribute_name)
        function = getattr(bound, "__func__", bound)
        spec = getattr(function, "__tool_spec__", None)
        if isinstance(spec, ToolSpec):
            tools[spec.name] = (spec, bound)
    return tools
