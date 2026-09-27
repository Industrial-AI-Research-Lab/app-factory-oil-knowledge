"""Semantic mapping adapter public interface.

Re-exports the MCP-scenario typed API from adjacent adapter modules so
transport code imports a single stable entry point.

Configuration comes from :mod:`.config` and business mapping from
:mod:`.works` and :mod:`.hierarchies`. Importing this package has no side
effects: it never reads the environment, opens files, connects to
databases, imports FastMCP, or touches source internals directly.

Examples:
    Import the public API from the package root::

        from semantic_mapping_adapter import (
            RuntimeSettings,
            load_runtime_settings,
            search_hierarchies,
            search_works,
        )

    Load settings once at the process boundary, then pass explicitly::

        settings = load_runtime_settings()
        result = search_works(works, top_k, settings=settings)

Exports:
    RuntimeSettings: Immutable runtime configuration model.
    load_runtime_settings: Load settings from the environment.
    search_works: Map work units via the source two-stage mapper.
    search_hierarchies: Map hierarchy names via the source mapper.
    InputWorkMappingUnit: Input work item model.
    OutputWorkMappingResult: Typed works mapping result.
    OutputWorkMappingUnit: Single mapped work with distances.
    StandardWorkUnit: Single standard work candidate.
    OutputHierarchyMappingResult: Typed hierarchy mapping result.
    OutputHierarchyMappingUnit: Single mapped hierarchy with distances.
    StandardHierarchyUnit: Single standard hierarchy candidate.
"""

from .config import RuntimeSettings, load_runtime_settings
from .hierarchies import (
    OutputHierarchyMappingResult,
    OutputHierarchyMappingUnit,
    StandardHierarchyUnit,
    search_hierarchies,
)
from .works import (
    InputWorkMappingUnit,
    OutputWorkMappingResult,
    OutputWorkMappingUnit,
    StandardWorkUnit,
    search_works,
)

__all__ = [
    "InputWorkMappingUnit",
    "OutputHierarchyMappingResult",
    "OutputHierarchyMappingUnit",
    "OutputWorkMappingResult",
    "OutputWorkMappingUnit",
    "RuntimeSettings",
    "StandardHierarchyUnit",
    "StandardWorkUnit",
    "load_runtime_settings",
    "search_hierarchies",
    "search_works",
]
