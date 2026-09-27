from typing import Tuple, Optional
from pydantic import BaseModel, Field, model_validator


class StandardWorkUnit(BaseModel):
    """
        Represents a standard works unit - standardized information about activity
        received after mapping project activity name on the guidebook of activities.
    """
    code: str = Field(..., description="Identifier of the standardized activity in the guidebook")
    name: str = Field(..., description="Name of the standardized activity in the guidebook")
    measurement: str = Field(..., description="Measurement unit of the standardized activity in the guidebook")
    category: str = Field(..., description="Hierarchical category name of the standardized activity"
                                           "in the guidebook")


class InputWorkMappingUnit(BaseModel):
    """
    Input structure describing a project work item to be mapped onto the guidebook.
    """
    work_name: str = Field(..., description="Work name in the project")
    work_measurement: str = Field(..., description="Measurement unit of the work in the project")

    bwd_name: Optional[str] = Field(
        None,
        description="Lowest hierarchy level in the project: working documentation brand (BWD)",
    )
    ose_name: Optional[str] = Field(
        None,
        description="Middle hierarchy level in the project: summary estimate object (OSE)",
    )
    occ_name: Optional[str] = Field(
        None,
        description="Top hierarchy level in the project: capital construction object (OCC)",
    )


class OutputWorkMappingUnit(InputWorkMappingUnit):
    """
    Output structure for a mapped project work item. Extends the input with mapping results.

    Notes
    -----
    - top_k_units and top_k_distances are tuples to emphasize immutability of the response payload.
    - Length of top_k_units must match length of top_k_distances.
    """
    top_k_units: Tuple[StandardWorkUnit, ...] = Field(
        ...,
        description="Top-k mapping results, each represented as a StandardWorkUnit",
    )
    top_k_distances: Tuple[float, ...] = Field(
        ...,
        description="Distances to each of the top-k units (same order as top_k_units)",
    )

    @model_validator(mode="after")
    def _validate_top_k_lengths(self) -> "OutputWorkMappingUnit":
        if len(self.top_k_units) != len(self.top_k_distances):
            raise ValueError(
                "top_k_units and top_k_distances must have the same length "
                f"(got {len(self.top_k_units)} and {len(self.top_k_distances)})."
            )
        return self


class OutputWorkMappingResult(BaseModel):
    result: list[OutputWorkMappingUnit]


class StandardHierarchyUnit(BaseModel):
    """
    Represents a standardized hierarchy item from guidebook.
    """
    code: str = Field(..., description="String code of the hierarchical item")
    name: str = Field(..., description="Hierarchical name")


class OutputHierarchyMappingUnit(BaseModel):
    """
    Output structure for mapping a project hierarchical work name to standardized hierarchy units.

    Notes
    -----
    - top_k_units and top_k_distances are tuples to emphasize immutability of the response payload.
    - Length of top_k_units must match length of top_k_distances.
    """
    hierarchical_work_name: str = Field(..., description="Hierarchical work name in the project")

    top_k_units: Tuple[StandardHierarchyUnit, ...] = Field(
        ...,
        description="Top-k mapping results, each represented as a StandardHierarchyUnit",
    )
    top_k_distances: Tuple[float, ...] = Field(
        ...,
        description="Distances to each of the top-k units (same order as top_k_units)",
    )

    @model_validator(mode="after")
    def _validate_top_k_lengths(self) -> "OutputHierarchyMappingUnit":
        if len(self.top_k_units) != len(self.top_k_distances):
            raise ValueError(
                "top_k_units and top_k_distances must have the same length "
                f"(got {len(self.top_k_units)} and {len(self.top_k_distances)})."
            )
        return self


class OutputHierarchyMappingResult(BaseModel):
    result: list[OutputHierarchyMappingUnit]
