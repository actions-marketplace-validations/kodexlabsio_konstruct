"""Write graph.html: one self-contained, offline, interactive viewer of the graph.

The page embeds ``Graph.to_dict()`` as JSON and draws the relation-level graph (sources, tables,
models, seeds, snapshots, exposures and Airflow tasks) as an SVG, with search, a details panel and
column lineage. It loads nothing from the network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kerplunk.graph import Graph

_DATA_PLACEHOLDER = "__KERPLUNK_DATA__"

# Inside <script type="application/json"> the HTML parser only looks for "</script"; escaping
# "<", ">" and "&" rules that out entirely, and U+2028/U+2029 are escaped for older JS parsers.
_JSON_ESCAPES = str.maketrans(
    {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026", "\u2028": "\\u2028", "\u2029": "\\u2029"}
)

_STYLE = r"""
:root {
  color-scheme: light;
  --surface: #fcfcfb; --panel: #f4f3f0; --node-bg: #ffffff; --text: #0b0b0b; --muted: #52514e;
  --border: #d9d8d3; --edge: #a9a8a2; --edge-hl: #3a3a38; --accent: #2a78d6;
  --k-model: #2a78d6; --k-table: #eb6834; --k-source: #1baf7a; --k-seed: #eda100;
  --k-snapshot: #e87ba4; --k-task: #008300; --k-exposure: #6250d6;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --surface: #1a1a19; --panel: #232322; --node-bg: #2a2a28; --text: #f0efec; --muted: #c3c2b7;
    --border: #3d3d3a; --edge: #6b6a65; --edge-hl: #e0dfda; --accent: #3987e5;
    --k-model: #3987e5; --k-table: #d95926; --k-source: #199e70; --k-seed: #c98500;
    --k-snapshot: #d55181; --k-task: #008300; --k-exposure: #9085e9;
  }
}
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body {
  display: flex; flex-direction: column; background: var(--surface); color: var(--text);
  font: 13px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
header { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 16px; padding: 8px 12px; border-bottom: 1px solid var(--border); }
header h1 { font-size: 15px; margin: 0; }
#counts { display: flex; flex-wrap: wrap; gap: 4px 10px; color: var(--muted); }
#counts b { color: var(--text); font-weight: 600; }
#meta { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
#search-box { margin-left: auto; display: flex; align-items: center; gap: 8px; }
#search { width: 260px; padding: 4px 8px; font: inherit; color: inherit; background: var(--node-bg); border: 1px solid var(--border); border-radius: 4px; }
#match-count { color: var(--muted); font-size: 12px; min-width: 70px; }
#warnings { padding: 4px 12px; border-bottom: 1px solid var(--border); font-size: 12px; }
#warnings summary { cursor: pointer; color: var(--muted); }
#warnings ul { margin: 4px 0; padding-left: 20px; max-height: 180px; overflow: auto; }
main { flex: 1; display: flex; min-height: 0; }
#canvas { position: relative; flex: 1; min-width: 0; overflow: hidden; }
#graph { width: 100%; height: 100%; display: block; cursor: grab; touch-action: none; user-select: none; -webkit-user-select: none; }
#graph.dragging { cursor: grabbing; }
#empty { position: absolute; top: 40%; width: 100%; text-align: center; color: var(--muted); }
#controls { position: absolute; top: 10px; right: 10px; display: flex; gap: 4px; }
button { font: inherit; color: inherit; background: var(--node-bg); border: 1px solid var(--border); border-radius: 4px; padding: 2px 8px; cursor: pointer; }
button:hover { border-color: var(--muted); }
#legend { position: absolute; left: 10px; bottom: 10px; display: flex; flex-wrap: wrap; gap: 4px 12px; max-width: 70%; padding: 6px 8px; font-size: 12px; background: var(--panel); border: 1px solid var(--border); border-radius: 4px; }
#legend span { display: inline-flex; align-items: center; gap: 5px; }
.swatch { display: inline-block; width: 10px; height: 10px; border-radius: 2px; background: var(--kc); }
.line { display: inline-block; width: 18px; border-top: 2px solid var(--edge); }
.line.dashed { border-top-style: dashed; }
.node { cursor: pointer; }
.node .box { fill: var(--node-bg); stroke: var(--kc); stroke-width: 1.5; }
.node .bar { fill: var(--kc); }
.node text { fill: var(--text); font-size: 12px; dominant-baseline: central; pointer-events: none; }
.node.match .box { stroke: var(--text); stroke-width: 2.5; }
.node.selected .box { stroke: var(--accent); stroke-width: 3.5; }
.edge { fill: none; stroke: var(--edge); stroke-width: 1.2; }
.edge.runs { stroke-dasharray: 5 4; }
.edge.hl { stroke: var(--edge-hl); stroke-width: 2; }
#arrow path { fill: var(--edge); }
.dim { opacity: 0.15; }
.k-model { --kc: var(--k-model); } .k-table { --kc: var(--k-table); } .k-source { --kc: var(--k-source); }
.k-seed { --kc: var(--k-seed); } .k-snapshot { --kc: var(--k-snapshot); } .k-task { --kc: var(--k-task); }
.k-exposure { --kc: var(--k-exposure); }
#panel { width: 420px; max-width: 45%; overflow: auto; padding: 10px 14px 20px; background: var(--panel); border-left: 1px solid var(--border); }
#panel[hidden] { display: none; }
#panel h2 { font-size: 16px; margin: 4px 0; overflow-wrap: anywhere; }
#panel h3 { font-size: 13px; margin: 16px 0 6px; }
#panel h4 { font-size: 12px; margin: 10px 0 4px; color: var(--muted); }
#close { float: right; }
.kind { display: inline-flex; align-items: center; gap: 6px; color: var(--muted); }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 3px 10px; margin: 8px 0; }
dt { color: var(--muted); }
dd { margin: 0; overflow-wrap: anywhere; white-space: pre-wrap; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th, td { text-align: left; vertical-align: top; padding: 3px 4px; border-bottom: 1px solid var(--border); overflow-wrap: anywhere; }
th { color: var(--muted); font-weight: 600; }
#panel td:nth-child(1), #panel td:nth-child(2) { white-space: nowrap; overflow-wrap: normal; }
tr.current td { background: var(--node-bg); }
ul.plain { list-style: none; margin: 0; padding: 0; }
ul.plain li { padding: 2px 0; overflow-wrap: anywhere; }
.link { border: none; background: none; padding: 0; color: var(--accent); text-align: left; overflow-wrap: anywhere; }
.link:hover { text-decoration: underline; }
.note { color: var(--muted); font-size: 12px; }
@media (max-width: 800px) {
  #panel { position: absolute; right: 0; top: 0; bottom: 0; max-width: 90%; z-index: 2; }
  main { position: relative; }
  #search { width: 160px; }
}
"""

_SCRIPT = r"""
"use strict";
(function () {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const VIEW_KINDS = ["source", "seed", "snapshot", "table", "model", "task", "exposure"];
  const KIND_ORDER = ["source", "seed", "snapshot", "table", "model", "column", "test", "exposure", "dag", "task"];
  const SHOWN_ATTRS = ["materialized", "table_type", "schedule", "operator", "dag", "sql_path", "asset"];
  const NODE_W = 190, NODE_H = 30, COL_GAP = 100, ROW_GAP = 14, MAX_LABEL = 24, SWEEPS = 6;

  const data = JSON.parse(document.getElementById("kerplunk-data").textContent);
  const nodes = data.nodes || [];
  const edges = data.edges || [];
  const byId = new Map();
  const outE = new Map();
  const inE = new Map();
  for (const n of nodes) byId.set(n.id, n);
  for (const e of edges) {
    push(outE, e.source, e);
    push(inE, e.target, e);
  }

  function push(map, key, value) {
    const list = map.get(key);
    if (list) list.push(value);
    else map.set(key, [value]);
  }
  function outs(id, kind) { return (outE.get(id) || []).filter((e) => e.kind === kind); }
  function ins(id, kind) { return (inE.get(id) || []).filter((e) => e.kind === kind); }
  function attr(item, key) { return item.attrs ? item.attrs[key] : undefined; }
  function isReference(e) { return e.kind === "feeds" && attr(e, "role") === "reference"; }
  function byName(a, b) { return (a.name || "").localeCompare(b.name || "") || a.id.localeCompare(b.id); }

  const viewNodes = nodes.filter((n) => VIEW_KINDS.includes(n.kind));
  const inView = new Set(viewNodes.map((n) => n.id));

  // A column stands in for its relation, so reference edges from columns join the relation graph.
  function viewOf(id) {
    if (inView.has(id)) return id;
    const n = byId.get(id);
    return n && n.kind === "column" && n.parent && inView.has(n.parent) ? n.parent : null;
  }

  function columnsOf(id) {
    return outs(id, "contains").map((e) => byId.get(e.target)).filter(Boolean);
  }

  function label(id) {
    const n = byId.get(id);
    if (!n) return id;
    if (n.kind === "column" && n.parent) return label(n.parent) + "." + n.name;
    const attrs = n.attrs || {};
    if (n.kind === "source" && attrs.source_name) return attrs.source_name + "." + n.name;
    if (n.kind === "task" && attrs.dag) return attrs.dag + "." + n.name;
    return n.name;
  }

  // Layout edges: feeds between relations (deduplicated per pair) and task -> node runs edges.
  const layoutEdges = [];
  const seenPairs = new Set();
  for (const e of edges) {
    if (e.kind !== "feeds" && e.kind !== "runs") continue;
    const s = e.kind === "feeds" ? viewOf(e.source) : inView.has(e.source) ? e.source : null;
    const t = e.kind === "feeds" ? viewOf(e.target) : inView.has(e.target) ? e.target : null;
    if (!s || !t || s === t) continue;
    const key = e.kind + "\u0000" + s + "\u0000" + t;
    if (seenPairs.has(key)) continue;
    seenPairs.add(key);
    layoutEdges.push({ source: s, target: t, kind: e.kind });
  }
  const upAdj = new Map(viewNodes.map((n) => [n.id, []]));
  const downAdj = new Map(viewNodes.map((n) => [n.id, []]));
  for (const e of layoutEdges) {
    downAdj.get(e.source).push(e.target);
    upAdj.get(e.target).push(e.source);
  }

  function computeLayers(ids, links) {
    const succ = new Map(ids.map((id) => [id, []]));
    const hasInput = new Set();
    for (const e of links) {
      succ.get(e.source).push(e.target);
      hasInput.add(e.target);
    }
    // Depth-first search finds the edges that close cycles; layering ignores them. Starting from
    // nodes without inputs makes the edge that returns to the start of a cycle the one dropped.
    const state = new Map();
    const back = new Set();
    for (const root of ids.filter((id) => !hasInput.has(id)).concat(ids)) {
      if (state.has(root)) continue;
      state.set(root, 1);
      const stack = [[root, 0]];
      while (stack.length) {
        const top = stack[stack.length - 1];
        const next = succ.get(top[0]);
        if (top[1] < next.length) {
          const t = next[top[1]++];
          const s = state.get(t);
          if (s === 1) back.add(top[0] + "\u0000" + t);
          else if (!s) {
            state.set(t, 1);
            stack.push([t, 0]);
          }
        } else {
          state.set(top[0], 2);
          stack.pop();
        }
      }
    }
    const indegree = new Map(ids.map((id) => [id, 0]));
    const forward = new Map(ids.map((id) => [id, []]));
    for (const e of links) {
      if (back.has(e.source + "\u0000" + e.target)) continue;
      forward.get(e.source).push(e.target);
      indegree.set(e.target, indegree.get(e.target) + 1);
    }
    const layer = new Map(ids.map((id) => [id, 0]));
    const queue = ids.filter((id) => indegree.get(id) === 0);
    for (let i = 0; i < queue.length; i++) {
      const id = queue[i];
      for (const t of forward.get(id)) {
        layer.set(t, Math.max(layer.get(t), layer.get(id) + 1));
        indegree.set(t, indegree.get(t) - 1);
        if (indegree.get(t) === 0) queue.push(t);
      }
    }
    return layer;
  }

  function computeLayout() {
    const ids = viewNodes.slice().sort(byName).map((n) => n.id);
    const feeds = layoutEdges.filter((e) => e.kind === "feeds");
    const layer = computeLayers(ids, feeds);
    // A node with nothing upstream sits just before the earliest node it feeds, rather than in
    // the first column, so an orchestration task that only writes a late table stays beside it.
    const hasInput = new Set(feeds.map((e) => e.target));
    const feedsTo = new Map();
    for (const e of feeds) if (!hasInput.has(e.source)) push(feedsTo, e.source, layer.get(e.target));
    for (const [id, targets] of feedsTo) layer.set(id, Math.max(0, Math.min(...targets) - 1));
    // A task linked only by runs edges sits just before the earliest node it runs; if that node is
    // in the first column, everything moves one column right to make room.
    const linked = new Set();
    for (const e of feeds) {
      linked.add(e.source);
      linked.add(e.target);
    }
    const runsFrom = new Map();
    for (const e of layoutEdges) if (e.kind === "runs" && !linked.has(e.source)) push(runsFrom, e.source, layer.get(e.target));
    let shift = 0;
    for (const targets of runsFrom.values()) if (Math.min(...targets) === 0) shift = 1;
    if (shift) for (const id of ids) layer.set(id, layer.get(id) + shift);
    for (const [id, targets] of runsFrom) layer.set(id, Math.min(...targets) + shift - 1);
    let depth = 0;
    for (const id of ids) depth = Math.max(depth, layer.get(id));
    const layers = Array.from({ length: depth + 1 }, () => []);
    for (const id of ids) layers[layer.get(id)].push(id);

    const before = new Map(ids.map((id) => [id, []]));
    const after = new Map(ids.map((id) => [id, []]));
    for (const e of layoutEdges) {
      const ls = layer.get(e.source), lt = layer.get(e.target);
      if (ls < lt) {
        after.get(e.source).push(e.target);
        before.get(e.target).push(e.source);
      } else if (ls > lt) {
        after.get(e.target).push(e.source);
        before.get(e.source).push(e.target);
      }
    }
    // Positions are centred on zero so layers of different sizes line up around the middle.
    const pos = new Map();
    function place(row) { row.forEach((id, i) => pos.set(id, i - (row.length - 1) / 2)); }
    layers.forEach(place);
    for (let sweep = 0; sweep < SWEEPS; sweep++) {
      const down = sweep % 2 === 0;
      const order = down ? layers : layers.slice().reverse();
      const neighbours = down ? before : after;
      for (const row of order) {
        const centre = new Map();
        for (const id of row) {
          const list = neighbours.get(id);
          let sum = 0;
          for (const other of list) sum += pos.get(other);
          centre.set(id, list.length ? sum / list.length : pos.get(id));
        }
        row.sort((a, b) => centre.get(a) - centre.get(b));
        place(row);
      }
    }
    const coords = new Map();
    for (const id of ids) {
      coords.set(id, { x: layer.get(id) * (NODE_W + COL_GAP), y: pos.get(id) * (NODE_H + ROW_GAP) - NODE_H / 2 });
    }
    return coords;
  }

  function walk(starts, adjacency) {
    const seen = new Set(starts);
    const queue = starts.slice();
    for (let i = 0; i < queue.length; i++) {
      for (const other of adjacency.get(queue[i]) || []) {
        if (!seen.has(other)) {
          seen.add(other);
          queue.push(other);
        }
      }
    }
    return seen;
  }

  // Upstream columns follow derived_from out-edges; downstream columns follow them in reverse.
  function columnLineage(start, upstream) {
    const depth = new Map([[start, 0]]);
    const queue = [start];
    const found = [];
    for (let i = 0; i < queue.length; i++) {
      const id = queue[i];
      for (const e of upstream ? outs(id, "derived_from") : ins(id, "derived_from")) {
        const other = upstream ? e.target : e.source;
        if (depth.has(other)) continue;
        depth.set(other, depth.get(id) + 1);
        queue.push(other);
        found.push({ id: other, depth: depth.get(other), transform: attr(e, "transform") });
      }
    }
    return found;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function svgEl(tag, attrs) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const key in attrs) node.setAttribute(key, attrs[key]);
    return node;
  }
  function display(value) {
    if (value === null || value === undefined) return "";
    return typeof value === "object" ? JSON.stringify(value) : String(value);
  }
  function shorten(text) { return text.length > MAX_LABEL ? text.slice(0, MAX_LABEL - 1) + "\u2026" : text; }

  function renderHeader() {
    const counts = new Map();
    for (const n of nodes) counts.set(n.kind, (counts.get(n.kind) || 0) + 1);
    const kinds = Array.from(counts.keys()).sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
    const box = document.getElementById("counts");
    for (const kind of kinds) {
      const item = el("span");
      item.append(el("b", null, counts.get(kind)), " " + kind);
      box.append(item);
    }
    const edgeCount = el("span");
    edgeCount.append(el("b", null, edges.length), " edges");
    box.append(edgeCount);
    const meta = data.meta || {};
    const parts = [];
    if (meta.generated_at) parts.push("generated " + display(meta.generated_at));
    if (meta.root) parts.push("root " + display(meta.root));
    document.getElementById("meta").textContent = parts.join("  |  ");
    const warnings = data.warnings || [];
    const details = document.getElementById("warnings");
    if (!warnings.length) {
      details.hidden = true;
      return;
    }
    details.querySelector("summary").textContent = "Warnings (" + warnings.length + ")";
    const list = details.querySelector("ul");
    for (const w of warnings) list.append(el("li", null, w));
  }
  function rank(kind) {
    const i = KIND_ORDER.indexOf(kind);
    return i < 0 ? KIND_ORDER.length : i;
  }

  function renderLegend() {
    const legend = document.getElementById("legend");
    const present = new Set(viewNodes.map((n) => n.kind));
    for (const kind of VIEW_KINDS) {
      if (!present.has(kind)) continue;
      const item = el("span", "k-" + kind);
      item.append(el("i", "swatch"), kind);
      legend.append(item);
    }
    for (const [text, cls] of [["feeds", "line"], ["runs", "line dashed"]]) {
      const item = el("span");
      item.append(el("i", cls), text);
      legend.append(item);
    }
  }

  const svg = document.getElementById("graph");
  const viewport = document.getElementById("viewport");
  const nodeEls = new Map();
  const edgeEls = [];
  const coords = computeLayout();
  let bounds = null;

  function renderGraph() {
    if (!viewNodes.length) {
      document.getElementById("empty").textContent = "No relations in this graph.";
      return;
    }
    const edgeLayer = document.getElementById("edges");
    const nodeLayer = document.getElementById("nodes");
    for (const e of layoutEdges) {
      const a = coords.get(e.source), b = coords.get(e.target);
      const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2, x2 = b.x, y2 = b.y + NODE_H / 2;
      const dx = Math.max(50, Math.abs(x2 - x1) / 2);
      const path = svgEl("path", {
        class: "edge " + e.kind,
        d: "M" + x1 + "," + y1 + " C" + (x1 + dx) + "," + y1 + " " + (x2 - dx) + "," + y2 + " " + x2 + "," + y2,
        "marker-end": "url(#arrow)",
      });
      edgeLayer.append(path);
      edgeEls.push({ el: path, source: e.source, target: e.target });
    }
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of viewNodes) {
      const c = coords.get(n.id);
      minX = Math.min(minX, c.x); minY = Math.min(minY, c.y);
      maxX = Math.max(maxX, c.x + NODE_W); maxY = Math.max(maxY, c.y + NODE_H);
      const g = svgEl("g", { class: "node k-" + n.kind, transform: "translate(" + c.x + "," + c.y + ")", "data-id": n.id });
      const title = svgEl("title", {});
      title.textContent = n.kind + ": " + (n.relation || n.id);
      const text = svgEl("text", { x: 14, y: NODE_H / 2 });
      text.textContent = shorten(n.name);
      g.append(title, svgEl("rect", { class: "box", width: NODE_W, height: NODE_H, rx: 4 }), svgEl("rect", { class: "bar", width: 6, height: NODE_H, rx: 2 }), text);
      nodeLayer.append(g);
      nodeEls.set(n.id, g);
    }
    bounds = { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
  }

  const view = { x: 0, y: 0, k: 1 };
  function applyView() { viewport.setAttribute("transform", "translate(" + view.x + "," + view.y + ") scale(" + view.k + ")"); }
  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }
  function zoomAt(px, py, factor) {
    const k = clamp(view.k * factor, 0.03, 4);
    view.x = px - ((px - view.x) * k) / view.k;
    view.y = py - ((py - view.y) * k) / view.k;
    view.k = k;
    applyView();
  }
  function fit() {
    if (!bounds) return;
    const r = svg.getBoundingClientRect();
    const pad = 30;
    view.k = clamp(Math.min((r.width - 2 * pad) / bounds.w, (r.height - 2 * pad) / bounds.h), 0.03, 1.5);
    view.x = (r.width - bounds.w * view.k) / 2 - bounds.x * view.k;
    view.y = (r.height - bounds.h * view.k) / 2 - bounds.y * view.k;
    applyView();
  }
  // Opening the panel narrows the drawing; bring the selected node back into view if needed.
  function ensureVisible(id) {
    const c = coords.get(id);
    if (!c) return;
    const r = svg.getBoundingClientRect();
    const left = c.x * view.k + view.x;
    const top = c.y * view.k + view.y;
    const right = left + NODE_W * view.k;
    const bottom = top + NODE_H * view.k;
    if (left < 0 || top < 0 || right > r.width || bottom > r.height) centreOn(id);
  }
  function centreOn(id) {
    const c = coords.get(id);
    if (!c) return;
    const r = svg.getBoundingClientRect();
    view.x = r.width / 2 - (c.x + NODE_W / 2) * view.k;
    view.y = r.height / 2 - (c.y + NODE_H / 2) * view.k;
    applyView();
  }

  let drag = null;
  let suppressClick = false;
  svg.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    suppressClick = false;
    drag = { x: ev.clientX, y: ev.clientY, vx: view.x, vy: view.y, moved: false };
  });
  svg.addEventListener("pointermove", (ev) => {
    if (!drag) return;
    const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    if (!drag.moved && Math.abs(dx) + Math.abs(dy) > 4) {
      drag.moved = true;
      svg.setPointerCapture(ev.pointerId);
      svg.classList.add("dragging");
    }
    if (drag.moved) {
      view.x = drag.vx + dx;
      view.y = drag.vy + dy;
      applyView();
    }
  });
  function endDrag() {
    if (drag && drag.moved) suppressClick = true;
    drag = null;
    svg.classList.remove("dragging");
  }
  svg.addEventListener("pointerup", endDrag);
  svg.addEventListener("pointercancel", endDrag);
  svg.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const r = svg.getBoundingClientRect();
    const scale = ev.deltaMode === 1 ? 0.05 : ev.deltaMode === 2 ? 1 : 0.0015;
    zoomAt(ev.clientX - r.left, ev.clientY - r.top, Math.exp(-ev.deltaY * scale));
  }, { passive: false });
  svg.addEventListener("click", (ev) => {
    if (suppressClick) {
      suppressClick = false;
      return;
    }
    const g = ev.target.closest(".node");
    if (g) select(g.getAttribute("data-id"), null, false);
    else clearSelection();
  });

  const state = { selected: null, column: null, matches: null };

  function paint() {
    let focus = null;
    if (state.selected) {
      if (state.column) {
        focus = new Set([state.selected]);
        for (const item of columnLineage(state.column, true).concat(columnLineage(state.column, false))) {
          const v = viewOf(item.id);
          if (v) focus.add(v);
        }
        for (const e of outs(state.column, "feeds")) {
          const v = viewOf(e.target);
          if (v) focus.add(v);
        }
      } else {
        focus = walk([state.selected], upAdj);
        for (const id of walk([state.selected], downAdj)) focus.add(id);
      }
    } else if (state.matches) {
      focus = state.matches;
    }
    for (const [id, g] of nodeEls) {
      g.classList.toggle("dim", focus !== null && !focus.has(id));
      g.classList.toggle("selected", id === state.selected);
      g.classList.toggle("match", state.matches !== null && state.matches.has(id));
    }
    for (const e of edgeEls) {
      const on = focus !== null && focus.has(e.source) && focus.has(e.target);
      e.el.classList.toggle("dim", focus !== null && !on);
      e.el.classList.toggle("hl", on);
    }
  }

  const search = document.getElementById("search");
  const haystack = new Map();
  for (const n of nodes) {
    if (inView.has(n.id)) haystack.set(n.id, [n.name, n.id, n.relation || ""].join("\u0000").toLowerCase());
  }
  function runSearch() {
    const q = search.value.trim().toLowerCase();
    const counter = document.getElementById("match-count");
    if (!q) {
      state.matches = null;
      counter.textContent = "";
      paint();
      return;
    }
    const found = new Set();
    for (const [id, text] of haystack) if (text.includes(q)) found.add(id);
    for (const n of nodes) {
      if (n.kind === "column" && (n.name || "").toLowerCase().includes(q)) {
        const v = viewOf(n.id);
        if (v) found.add(v);
      }
    }
    state.matches = found;
    counter.textContent = found.size + (found.size === 1 ? " match" : " matches");
    paint();
  }
  search.addEventListener("input", runSearch);
  search.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter" || !state.matches || !state.matches.size) return;
    const first = viewNodes.filter((n) => state.matches.has(n.id)).sort(byName)[0];
    select(first.id, null, true);
  });

  const panel = document.getElementById("panel");
  const body = document.getElementById("panel-body");
  document.getElementById("close").addEventListener("click", clearSelection);
  document.getElementById("fit").addEventListener("click", fit);
  document.getElementById("zoom-in").addEventListener("click", () => {
    const r = svg.getBoundingClientRect();
    zoomAt(r.width / 2, r.height / 2, 1.25);
  });
  document.getElementById("zoom-out").addEventListener("click", () => {
    const r = svg.getBoundingClientRect();
    zoomAt(r.width / 2, r.height / 2, 0.8);
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && ev.target !== search) clearSelection();
  });

  function clearSelection() {
    state.selected = null;
    state.column = null;
    panel.hidden = true;
    paint();
  }

  function select(id, columnId, centre) {
    if (!inView.has(id)) return;
    state.selected = id;
    state.column = columnId;
    renderPanel();
    paint();
    if (centre) centreOn(id);
    else ensureVisible(id);
  }

  function relationLink(id) {
    const item = el("span");
    const button = el("button", "link", label(id));
    button.title = id;
    button.addEventListener("click", () => select(id, null, true));
    item.append(button, el("span", "note", "  " + byId.get(id).kind));
    return item;
  }
  function columnLink(id) {
    const n = byId.get(id);
    const button = el("button", "link", label(id));
    button.title = id;
    const target = n && n.parent ? n.parent : id;
    button.addEventListener("click", () => select(target, id, target !== state.selected));
    return button;
  }
  function section(title, count) { return el("h3", null, count === undefined ? title : title + " (" + count + ")"); }
  function linkList(ids, make) {
    if (!ids.length) return el("p", "note", "None");
    const list = el("ul", "plain");
    for (const id of ids) {
      const li = el("li");
      li.append(make(id));
      list.append(li);
    }
    return list;
  }

  function renderPanel() {
    const n = byId.get(state.selected);
    body.replaceChildren();
    panel.hidden = false;
    const kind = el("div", "kind k-" + n.kind);
    kind.append(el("i", "swatch"), n.kind);
    body.append(kind, el("h2", null, n.name));
    const facts = el("dl");
    const rows = [["id", n.id], ["relation", n.relation], ["description", n.description], ["path", n.path]];
    if (n.origins && n.origins.length) rows.push(["origins", n.origins.join(", ")]);
    for (const key of SHOWN_ATTRS) rows.push([key.replace("_", " "), attr(n, key)]);
    for (const [key, value] of rows) {
      const text = display(value);
      if (text) facts.append(el("dt", null, key), el("dd", null, text));
    }
    body.append(facts);

    const ups = upAdj.get(n.id).slice().sort();
    const downs = downAdj.get(n.id).slice().sort();
    body.append(section("Upstream", ups.length), linkList(ups, relationLink));
    body.append(section("Downstream", downs.length), linkList(downs, relationLink));

    const columns = columnsOf(n.id);
    body.append(section("Columns", columns.length));
    if (columns.length) {
      const table = el("table");
      const head = el("tr");
      for (const h of ["name", "type", "description"]) head.append(el("th", null, h));
      table.append(head);
      for (const c of columns) {
        const row = el("tr", c.id === state.column ? "current" : null);
        const name = el("td");
        const button = el("button", "link", c.name);
        button.title = "Show lineage";
        button.addEventListener("click", () => select(n.id, c.id, false));
        name.append(button);
        row.append(name, el("td", null, c.data_type || ""), el("td", null, c.description || ""));
        table.append(row);
      }
      body.append(table);
    } else {
      body.append(el("p", "note", "No columns recorded."));
    }

    const tests = [];
    for (const target of [n.id].concat(columns.map((c) => c.id))) {
      for (const e of ins(target, "tests")) tests.push([e.source, target]);
    }
    body.append(section("Tests", tests.length));
    if (tests.length) {
      const list = el("ul", "plain");
      for (const [test, target] of tests) {
        const t = byId.get(test);
        const li = el("li", null, t ? t.name : test);
        li.title = test;
        if (target !== n.id) li.append(el("span", "note", "  on " + label(target)));
        list.append(li);
      }
      body.append(list);
    }

    if (state.column) renderLineage(state.column);
  }

  function renderLineage(columnId) {
    const box = el("div");
    box.id = "lineage";
    box.append(section("Lineage of " + label(columnId)));
    for (const [title, upstream] of [["Upstream columns", true], ["Downstream columns", false]]) {
      const items = columnLineage(columnId, upstream);
      box.append(el("h4", null, title + " (" + items.length + ")"));
      if (!items.length) {
        box.append(el("p", "note", "None"));
        continue;
      }
      const list = el("ul", "plain");
      for (const item of items) {
        const li = el("li");
        li.style.paddingLeft = (item.depth - 1) * 14 + "px";
        li.title = item.depth === 1 ? "Direct" : item.depth + " steps away";
        li.append(columnLink(item.id));
        if (item.transform) li.append(el("span", "note", "  " + display(item.transform)));
        list.append(li);
      }
      box.append(list);
    }
    const refs = outs(columnId, "feeds").filter(isReference).map((e) => e.target).filter((id) => inView.has(id));
    box.append(el("h4", null, "Used in joins, filters or grouping by (" + refs.length + ")"));
    box.append(linkList(refs, relationLink));
    body.append(box);
    box.scrollIntoView({ block: "start" });
  }

  renderHeader();
  renderLegend();
  renderGraph();
  applyView();
  requestAnimationFrame(fit);
})();
"""

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>kerplunk graph</title>
<style>__STYLE__</style>
</head>
<body>
<header>
  <h1>kerplunk</h1>
  <div id="counts"></div>
  <div id="meta"></div>
  <div id="search-box">
    <input id="search" type="search" placeholder="Search relations and columns" autocomplete="off" spellcheck="false">
    <span id="match-count"></span>
  </div>
</header>
<details id="warnings"><summary>Warnings</summary><ul></ul></details>
<main>
  <div id="canvas">
    <svg id="graph" aria-label="Relation graph">
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="7" markerHeight="7" orient="auto">
          <path d="M0,0 L10,5 L0,10 z"></path>
        </marker>
      </defs>
      <g id="viewport"><g id="edges"></g><g id="nodes"></g></g>
    </svg>
    <div id="empty"></div>
    <div id="controls">
      <button id="zoom-in" type="button" title="Zoom in">+</button>
      <button id="zoom-out" type="button" title="Zoom out">-</button>
      <button id="fit" type="button" title="Fit the graph to the window">Fit</button>
    </div>
    <div id="legend"></div>
  </div>
  <aside id="panel" hidden>
    <button id="close" type="button">Close</button>
    <div id="panel-body"></div>
  </aside>
</main>
<script type="application/json" id="kerplunk-data">__KERPLUNK_DATA__</script>
<script>__SCRIPT__</script>
</body>
</html>
"""

_TEMPLATE = _PAGE.replace("__STYLE__", _STYLE).replace("__SCRIPT__", _SCRIPT)


def _embed_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).translate(_JSON_ESCAPES)


def write_html(graph: Graph, path: Path) -> Path:
    """Write the interactive viewer for ``graph`` to ``path`` and return ``path``."""
    page = _TEMPLATE.replace(_DATA_PLACEHOLDER, _embed_json(graph.to_dict()), 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path
