"""kerplunk: a queryable graph of a data stack, with column-level lineage.

The main entry point is `Project`::

    import kerplunk

    project = kerplunk.Project(".")
    project.build(update=True)
    print(project.impact("orders.amount"))
"""

__version__ = "0.10.2"

from kerplunk.explain import Explanation
from kerplunk.graph import Edge, Graph, Node
from kerplunk.project import Project
from kerplunk.query import (
    Definitions,
    Description,
    EdgeItem,
    Item,
    Listing,
    PathResult,
    QueryError,
    QueryResult,
    SearchResult,
    TermDefinition,
    Walk,
)

__all__ = [
    "Definitions",
    "Description",
    "Edge",
    "EdgeItem",
    "Explanation",
    "Graph",
    "Item",
    "Listing",
    "Node",
    "PathResult",
    "Project",
    "QueryError",
    "QueryResult",
    "SearchResult",
    "TermDefinition",
    "Walk",
    "__version__",
]
