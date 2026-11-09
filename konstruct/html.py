"""Write graph.html: one self-contained, offline, interactive viewer of the graph.

The page is assembled from ``konstruct/viewer`` (``index.html``, ``viewer.css``, ``viewer.js`` and
the Manrope and JetBrains Mono fonts, both under the SIL Open Font License, embedded as data
URLs). It embeds ``Graph.to_dict()`` as JSON, plus each column's formula and each relation's
conditions as computed by :mod:`konstruct.explain`, and loads nothing from the network.
"""

from __future__ import annotations

import base64
import json
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from konstruct.graph import Graph

_DATA_PLACEHOLDER = "__KONSTRUCT_DATA__"
_EXPLAIN_PLACEHOLDER = "__KONSTRUCT_EXPLAIN__"

# Inside <script type="application/json"> the HTML parser only looks for "</script"; escaping
# "<", ">" and "&" rules that out entirely, and U+2028/U+2029 are escaped for older JS parsers.
_JSON_ESCAPES = str.maketrans(
    {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026", "\u2028": "\\u2028", "\u2029": "\\u2029"}
)

VIEWER_FORMULA_LIMIT = 800

_FONTS = (
    ("Manrope", 500, "manrope-latin-500-normal.woff2"),
    ("Manrope", 700, "manrope-latin-700-normal.woff2"),
    ("Manrope", 800, "manrope-latin-800-normal.woff2"),
    ("JetBrains Mono", 500, "jetbrains-mono-latin-500-normal.woff2"),
)


def _asset(name: str) -> resources.abc.Traversable:
    return resources.files("konstruct").joinpath("viewer", name)


def _font_faces() -> str:
    faces = []
    for family, weight, filename in _FONTS:
        encoded = base64.b64encode(_asset(f"fonts/{filename}").read_bytes()).decode("ascii")
        faces.append(
            f'@font-face {{ font-family: "{family}"; font-weight: {weight}; font-style: normal; '
            f'font-display: swap; src: url(data:font/woff2;base64,{encoded}) format("woff2"); }}'
        )
    return "\n".join(faces)


@cache
def template() -> str:
    """The page with styles, fonts and script in place, and the data placeholders still open."""
    style = _asset("viewer.css").read_text(encoding="utf-8").replace("__FONTS__", _font_faces())
    script = _asset("viewer.js").read_text(encoding="utf-8")
    page = _asset("index.html").read_text(encoding="utf-8")
    return page.replace("__STYLE__", style).replace("__SCRIPT__", script)


def _embed_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).translate(_JSON_ESCAPES)


def explanations(graph: Graph, cache: Any = None) -> dict[str, Any]:
    """Column formulas and relation conditions for the viewer, or empty if they cannot be made.
    ``cache`` is the build cache, so an update recomputes only the formulas that changed."""
    from konstruct.explain import Explainer

    try:
        # A formula longer than this is not readable at a glance; the panel points to
        # `konstruct explain` for the full composition.
        return Explainer(graph, max_composed=VIEWER_FORMULA_LIMIT).summaries(cache=cache)
    except RecursionError:
        return {}


def write_html(graph: Graph, path: Path, cache: Any = None) -> Path:
    """Write the interactive viewer for ``graph`` to ``path`` and return ``path``."""
    page = (
        template()
        .replace(_EXPLAIN_PLACEHOLDER, _embed_json(explanations(graph, cache)), 1)
        .replace(_DATA_PLACEHOLDER, _embed_json(graph.to_dict()), 1)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path
