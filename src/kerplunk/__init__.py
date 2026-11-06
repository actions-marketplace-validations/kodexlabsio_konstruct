"""kerplunk: a queryable graph of a data stack, with column-level lineage.

The main entry point is `Project`::

    import kerplunk

    project = kerplunk.Project(".")
    project.build(update=True)
    print(project.impact("orders.amount"))
"""

__version__ = "0.5.0"

from kerplunk.graph import Edge, Graph, Node
from kerplunk.project import Project
from kerplunk.query import (
    Description,
    EdgeItem,
    Item,
    Listing,
    PathResult,
    QueryError,
    QueryResult,
    SearchResult,
    Walk,
)

__all__ = [
    "Description",
    "Edge",
    "EdgeItem",
    "Graph",
    "Item",
    "Listing",
    "Node",
    "PathResult",
    "Project",
    "QueryError",
    "QueryResult",
    "SearchResult",
    "Walk",
    "__version__",
]
