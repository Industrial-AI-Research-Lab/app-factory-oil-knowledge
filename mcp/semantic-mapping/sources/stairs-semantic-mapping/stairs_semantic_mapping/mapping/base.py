from abc import ABC, abstractmethod
from typing import Sequence, Mapping, Any


class BaseSemanticMapper(ABC):
    """
        Abstract interface for all semantic mappers.

        A semantic mapper takes a sequence of textual inputs and returns the
        best-matching canonical entities (e.g., categories, task names, or any
        domain-specific items) using a model-driven semantic search strategy.

        Implementations may use different embedding models, vector stores,
        filtering logic, or multi-stage pipelines, but must expose a unified
        interface via `semantic_search()`.

        Methods
        -------
        semantic_search(inputs, top_k=1, with_distances=False)
            Perform semantic nearest-neighbor search and return structured
            top-k results for each input text. Concrete strategies (flat,
            hierarchical, etc.) must implement this method.
        """
    @abstractmethod
    def semantic_search(
        self,
        inputs: Sequence[str],
        top_k: int = 1,
        with_distances: bool = False,
    ) -> Mapping[str, Any]:
        """
            Perform semantic nearest-neighbor search over the given input texts.

            Parameters
            ----------
            inputs : Sequence[str]
                Raw textual inputs to be mapped or matched.
            top_k : int, optional
                Number of nearest candidates to return for each input.
            with_distances : bool, optional
                If True, include distance scores in the output.

            Returns
            -------
            Mapping[str, Any]
                A structured dictionary containing top-k search results for each input.
                Exact structure is defined by concrete mapper implementations.
            """
        ...
