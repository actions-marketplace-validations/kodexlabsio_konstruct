"use strict";
// kerplunk graph viewer: one self-contained page over graph.json. Relations are cards in layers
// from sources to exposures; expanding a card lists its columns, and column lineage is drawn
// between column rows. Everything is built with DOM APIs and textContent, never innerHTML.
(function () {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const VIEW_KINDS = ["source", "seed", "snapshot", "table", "model", "task", "exposure"];
  const KIND_ORDER = ["source", "seed", "snapshot", "table", "model", "column", "test", "exposure", "dag", "task"];
  const KIND_NAMES = {
    source: "Source", seed: "Seed", snapshot: "Snapshot", table: "Table", model: "Model", task: "Task",
    exposure: "Exposure", column: "Column", test: "Test", dag: "DAG",
  };
  // Line icons on a 24 point grid, 1.8 stroke, round caps (DESIGN.md section 7).
  const ICONS = {
    source: "M4 6c0-1.66 3.58-3 8-3s8 1.34 8 3-3.58 3-8 3-8-1.34-8-3zM4 6v12c0 1.66 3.58 3 8 3s8-1.34 8-3V6M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3",
    seed: "M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6",
    snapshot: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2",
    table: "M4 5h16v14H4zM4 10h16M4 15h16M10 5v14",
    model: "M12 3l8 4.5v9L12 21l-8-4.5v-9zM4 7.5l8 4.5 8-4.5M12 12v9",
    task: "M17 2l3 3-3 3M20 5H9a5 5 0 0 0-5 5M7 22l-3-3 3-3M4 19h11a5 5 0 0 0 5-5",
    exposure: "M4 20V10M10 20V4M16 20v-7M3 20h18",
  };
  const W = 268, HEAD = 46, ROW = 28, PAD = 6, COL_GAP = 104, ROW_GAP = 22, SWEEPS = 8;
  const MAX_RESULTS = 60, ANIMATE_LIMIT = 400, EXPAND_ALL_LIMIT = 400, AUTO_ISOLATE = 120;

  const data = JSON.parse(document.getElementById("kerplunk-data").textContent);
  const explained = JSON.parse(document.getElementById("kerplunk-explain").textContent || "{}");
  const formulas = explained.columns || {};
  const conditionsOf = explained.conditions || {};
  const nodes = data.nodes || [];
  const edges = data.edges || [];
  const meta = data.meta || {};

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
  function attr(item, key) { return item && item.attrs ? item.attrs[key] : undefined; }
  function isReference(e) { return e.kind === "feeds" && attr(e, "role") === "reference"; }
  function byName(a, b) { return (a.name || "").localeCompare(b.name || "") || a.id.localeCompare(b.id); }

  const viewNodes = nodes.filter((n) => VIEW_KINDS.includes(n.kind));
  const inView = new Set(viewNodes.map((n) => n.id));
  const columnCache = new Map();
  function columnsOf(id) {
    let found = columnCache.get(id);
    if (!found) {
      found = outs(id, "contains").map((e) => byId.get(e.target)).filter((n) => n && n.kind === "column");
      columnCache.set(id, found);
    }
    return found;
  }
  function viewOf(id) {
    if (inView.has(id)) return id;
    const n = byId.get(id);
    return n && n.kind === "column" && n.parent && inView.has(n.parent) ? n.parent : null;
  }
  function label(id) {
    const n = byId.get(id);
    if (!n) return id;
    if (n.kind === "column" && n.parent) return label(n.parent) + "." + n.name;
    const a = n.attrs || {};
    if (n.kind === "source" && a.source_name) return a.source_name + "." + n.name;
    if (n.kind === "task" && a.dag) return a.dag + "." + n.name;
    return n.name;
  }

  // Relation-level edges: feeds between relations (columns stand in for their relation) and runs.
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

  // Layout: layers by longest path over feeds (cycles broken by DFS), order by barycentre sweeps.
  function computeLayers(ids, links) {
    const succ = new Map(ids.map((id) => [id, []]));
    const hasInput = new Set();
    for (const e of links) {
      succ.get(e.source).push(e.target);
      hasInput.add(e.target);
    }
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

  function computeOrder(visible) {
    const ids = viewNodes.filter((n) => visible.has(n.id)).sort(byName).map((n) => n.id);
    const links = layoutEdges.filter((e) => visible.has(e.source) && visible.has(e.target));
    const feeds = links.filter((e) => e.kind === "feeds");
    const layer = computeLayers(ids, feeds);
    // A node with nothing upstream sits just before the earliest node it feeds.
    const hasInput = new Set(feeds.map((e) => e.target));
    const feedsTo = new Map();
    for (const e of feeds) if (!hasInput.has(e.source)) push(feedsTo, e.source, layer.get(e.target));
    for (const [id, targets] of feedsTo) layer.set(id, Math.max(0, Math.min(...targets) - 1));
    // A task linked only by runs edges sits just before the earliest node it runs.
    const linked = new Set();
    for (const e of feeds) {
      linked.add(e.source);
      linked.add(e.target);
    }
    const runsFrom = new Map();
    for (const e of links) if (e.kind === "runs" && !linked.has(e.source)) push(runsFrom, e.source, layer.get(e.target));
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
    for (const e of links) {
      const ls = layer.get(e.source), lt = layer.get(e.target);
      if (ls < lt) {
        after.get(e.source).push(e.target);
        before.get(e.target).push(e.source);
      } else if (ls > lt) {
        after.get(e.target).push(e.source);
        before.get(e.source).push(e.target);
      }
    }
    const pos = new Map();
    const place = (row) => row.forEach((id, i) => pos.set(id, i - (row.length - 1) / 2));
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
    return layers.filter((row) => row.length);
  }

  // State

  const state = {
    selected: null,
    column: null,
    direction: "both",
    isolate: false,
    allColumns: false,
    expanded: new Set(),
    collapsed: new Set(),
    hidden: new Set(),
    matches: null,
  };
  let focus = null;       // relation ids in the lineage of the selection
  let pathColumns = null; // column ids in the lineage of the selected column
  let references = [];    // [column id, relation id] for joins and filters on the column path

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
        found.push({ id: other, depth: depth.get(other), transform: attr(e, "transform"), verified: attr(e, "verified") });
      }
    }
    return found;
  }
  function computeFocus() {
    focus = null;
    pathColumns = null;
    references = [];
    if (!state.selected) return;
    const up = state.direction !== "downstream";
    const down = state.direction !== "upstream";
    if (state.column) {
      pathColumns = new Set([state.column]);
      if (up) for (const item of columnLineage(state.column, true)) pathColumns.add(item.id);
      if (down) for (const item of columnLineage(state.column, false)) pathColumns.add(item.id);
      focus = new Set([state.selected]);
      for (const id of pathColumns) {
        const v = viewOf(id);
        if (v) focus.add(v);
        for (const e of outs(id, "feeds")) {
          const t = viewOf(e.target);
          if (t && (down || id !== state.column)) {
            references.push([id, t]);
            focus.add(t);
          }
        }
      }
      return;
    }
    focus = new Set([state.selected]);
    if (up) for (const id of walk([state.selected], upAdj)) focus.add(id);
    if (down) for (const id of walk([state.selected], downAdj)) focus.add(id);
  }

  function visibleSet() {
    const visible = new Set();
    for (const n of viewNodes) {
      if (state.hidden.has(n.kind) && n.id !== state.selected) continue;
      if (state.isolate && focus && !focus.has(n.id)) continue;
      visible.add(n.id);
    }
    return visible;
  }
  // Which column rows a card shows: all of them when expanded, the lineage when a column is selected.
  function rowsOf(id) {
    const all = columnsOf(id);
    if (!all.length || state.collapsed.has(id)) return { rows: [], more: 0 };
    if (state.expanded.has(id) || state.allColumns) return { rows: all, more: 0 };
    if (pathColumns && focus && focus.has(id)) {
      const rows = all.filter((c) => pathColumns.has(c.id));
      return { rows, more: rows.length ? all.length - rows.length : 0 };
    }
    return { rows: [], more: 0 };
  }
  function heightOf(id) {
    const { rows, more } = rowsOf(id);
    const count = rows.length + (more ? 1 : 0);
    return count ? HEAD + PAD + count * ROW + PAD : HEAD;
  }

  // Text measurement, for truncating names to the card.
  const measure = document.createElement("canvas").getContext("2d");
  function fitText(text, font, width) {
    measure.font = font;
    if (measure.measureText(text).width <= width) return text;
    let lo = 0, hi = text.length;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (measure.measureText(text.slice(0, mid) + "…").width <= width) lo = mid;
      else hi = mid - 1;
    }
    return text.slice(0, lo) + "…";
  }
  const FONT_NAME = "700 15px Manrope, system-ui, sans-serif";
  const FONT_ROW = "500 14px Manrope, system-ui, sans-serif";
  const FONT_MONO = "500 14px 'JetBrains Mono', ui-monospace, monospace";

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
  function icon(kind, size) {
    const svg = svgEl("svg", { viewBox: "0 0 24 24", width: size, height: size, "aria-hidden": "true", fill: "none", stroke: "currentColor", "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round" });
    svg.append(svgEl("path", { d: ICONS[kind] || "M5 12h14" }));
    return svg;
  }
  function display(value) {
    if (value === null || value === undefined) return "";
    if (Array.isArray(value)) return value.map(display).join(", ");
    return typeof value === "object" ? JSON.stringify(value) : String(value);
  }

  // Drawing

  const svg = document.getElementById("graph");
  const viewport = document.getElementById("viewport");
  const edgeLayer = document.getElementById("edges");
  const nodeLayer = document.getElementById("nodes");
  let order = [];
  let orderKey = "";
  let coords = new Map();
  let drawn = { nodes: new Map(), edges: [] };
  let animation = 0;

  function place(visible) {
    const key = Array.from(visible).sort().join("\u0001");
    if (key !== orderKey) {
      order = computeOrder(visible);
      orderKey = key;
    }
    const next = new Map();
    order.forEach((row, layer) => {
      const heights = row.map(heightOf);
      const total = heights.reduce((a, b) => a + b, 0) + ROW_GAP * (row.length - 1);
      let y = -total / 2;
      row.forEach((id, i) => {
        next.set(id, { x: layer * (W + COL_GAP), y, h: heights[i] });
        y += heights[i] + ROW_GAP;
      });
    });
    return next;
  }

  function render(options) {
    const visible = visibleSet();
    const target = place(visible);
    buildDom(target);
    const animate = options && options.animate && target.size <= ANIMATE_LIMIT && coords.size;
    const from = coords;
    coords = target;
    cancelAnimationFrame(animation);
    document.getElementById("empty").textContent = target.size ? "" : viewNodes.length ? "Nothing to show with these filters." : "No tables or models in this graph.";
    if (!animate) {
      position(target);
    } else {
      const start = performance.now();
      const step = (now) => {
        const t = Math.min(1, (now - start) / 320);
        const k = 1 - Math.pow(1 - t, 3);
        const frame = new Map();
        for (const [id, c] of target) {
          const a = from.get(id) || c;
          frame.set(id, { x: a.x + (c.x - a.x) * k, y: a.y + (c.y - a.y) * k, h: c.h });
        }
        position(frame);
        if (t < 1) animation = requestAnimationFrame(step);
      };
      animation = requestAnimationFrame(step);
    }
    paint();
    drawMinimap();
    const lineage = focus;
    if (options && options.focus && lineage) requestAnimationFrame(() => fitTo(lineage));
    else if (options && options.centre) requestAnimationFrame(() => centreOn(options.centre));
    else if (options && options.fit) requestAnimationFrame(fit);
  }

  function buildDom(target) {
    edgeLayer.replaceChildren();
    nodeLayer.replaceChildren();
    drawn = { nodes: new Map(), edges: [] };
    // Relation edges.
    for (const e of layoutEdges) {
      if (!target.has(e.source) || !target.has(e.target)) continue;
      const path = svgEl("path", { class: "edge " + e.kind, "marker-end": "url(#arrow)" });
      edgeLayer.append(path);
      drawn.edges.push({ el: path, kind: e.kind, from: e.source, to: e.target, fromCol: null, toCol: null });
    }
    // Cards and their rows.
    const rowIndex = new Map();
    for (const id of target.keys()) {
      const n = byId.get(id);
      const c = target.get(id);
      const g = svgEl("g", { class: "node k-" + n.kind, "data-id": id });
      const title = svgEl("title", {});
      title.textContent = (KIND_NAMES[n.kind] || n.kind) + ": " + (n.relation || label(id));
      g.append(title, svgEl("rect", { class: "card", width: W, height: c.h, rx: 12 }));
      const ic = svgEl("g", { class: "icon", transform: "translate(14," + (HEAD / 2 - 10) + ") scale(0.8333)" });
      ic.append(svgEl("path", { d: ICONS[n.kind] || "" }));
      g.append(ic);
      const all = columnsOf(id);
      const nameWidth = W - 46 - (all.length ? 66 : 14);
      const name = svgEl("text", { class: "name", x: 44, y: HEAD / 2 });
      name.textContent = fitText(n.name, FONT_NAME, nameWidth);
      g.append(name);
      if (all.length) {
        const toggle = svgEl("g", { class: "toggle", "data-toggle": id });
        const open = rowsOf(id).rows.length > 0;
        toggle.append(svgEl("rect", { x: W - 64, y: 8, width: 56, height: HEAD - 16, rx: 10 }));
        const count = svgEl("text", { class: "count", x: W - 32, y: HEAD / 2 });
        count.textContent = String(all.length);
        const chev = svgEl("path", { class: "chev", d: open ? "M" + (W - 26) + "," + (HEAD / 2 + 3) + " l5,-5 l5,5" : "M" + (W - 26) + "," + (HEAD / 2 - 2) + " l5,5 l5,-5" });
        const tip = svgEl("title", {});
        tip.textContent = open ? "Hide columns" : "Show " + all.length + " columns";
        toggle.append(tip, count, chev);
        g.append(toggle);
      }
      const { rows, more } = rowsOf(id);
      const rowsAt = new Map();
      if (rows.length || more) {
        g.append(svgEl("line", { class: "divider", x1: 0, x2: W, y1: HEAD, y2: HEAD }));
        rows.forEach((col, i) => {
          const y = HEAD + PAD + i * ROW;
          rowsAt.set(col.id, y + ROW / 2);
          const row = svgEl("g", { class: "row", "data-col": col.id, transform: "translate(0," + y + ")" });
          row.append(svgEl("rect", { x: 4, y: 1, width: W - 8, height: ROW - 2, rx: 8 }));
          row.append(svgEl("circle", { class: "dot", cx: 16, cy: ROW / 2, r: 3 }));
          const type = col.data_type ? fitText(col.data_type.toLowerCase(), FONT_MONO, 92) : "";
          const typeWidth = type ? (measure.font = FONT_MONO, measure.measureText(type).width) : 0;
          const text = svgEl("text", { class: "col", x: 28, y: ROW / 2 });
          text.textContent = fitText(col.name, FONT_ROW, W - 44 - typeWidth - 14);
          row.append(text);
          if (type) {
            const t = svgEl("text", { class: "type", x: W - 14, y: ROW / 2 });
            t.textContent = type;
            row.append(t);
          }
          g.append(row);
        });
        if (more) {
          const y = HEAD + PAD + rows.length * ROW;
          const row = svgEl("g", { class: "row more", "data-more": id, transform: "translate(0," + y + ")" });
          row.append(svgEl("rect", { x: 4, y: 1, width: W - 8, height: ROW - 2, rx: 8 }));
          const text = svgEl("text", { class: "col", x: 28, y: ROW / 2 });
          text.textContent = "Show " + more + " more column" + (more === 1 ? "" : "s");
          row.append(text);
          g.append(row);
        }
      }
      rowIndex.set(id, rowsAt);
      nodeLayer.append(g);
      drawn.nodes.set(id, { g, rowsAt });
    }
    // Column edges between rows that are both on show.
    for (const [id, rowsAt] of rowIndex) {
      for (const colId of rowsAt.keys()) {
        for (const e of outs(colId, "derived_from")) {
          const upNode = viewOf(e.target);
          if (!upNode || !rowIndex.has(upNode) || !rowIndex.get(upNode).has(e.target)) continue;
          const path = svgEl("path", { class: "edge column" });
          const tip = svgEl("title", {});
          tip.textContent = label(e.target) + " → " + label(colId) + " (" + (attr(e, "transform") || "derived") + (attr(e, "verified") ? ", " + attr(e, "verified") : "") + ")";
          path.append(tip);
          edgeLayer.append(path);
          drawn.edges.push({ el: path, kind: "column", from: upNode, to: id, fromCol: e.target, toCol: colId });
        }
      }
    }
    for (const [colId, relation] of references) {
      const from = viewOf(colId);
      if (!from || !rowIndex.has(from) || !rowIndex.has(relation) || from === relation) continue;
      const path = svgEl("path", { class: "edge reference" });
      const tip = svgEl("title", {});
      tip.textContent = label(relation) + " joins, filters or groups on " + label(colId);
      path.append(tip);
      edgeLayer.append(path);
      drawn.edges.push({ el: path, kind: "reference", from, to: relation, fromCol: colId, toCol: null });
    }
  }

  function anchor(at, id, col, side) {
    const c = at.get(id);
    const rows = drawn.nodes.get(id).rowsAt;
    const y = col && rows.has(col) ? c.y + rows.get(col) : c.y + HEAD / 2;
    return { x: side === "out" ? c.x + W : c.x, y };
  }
  function curve(a, b) {
    if (b.x <= a.x) {
      // Backwards (a cycle or a task placed after what it feeds): loop around beneath.
      const drop = 40;
      return "M" + a.x + "," + a.y + " C" + (a.x + 60) + "," + (a.y + drop) + " " + (b.x - 60) + "," + (b.y + drop) + " " + b.x + "," + b.y;
    }
    const dx = Math.max(40, (b.x - a.x) / 2);
    return "M" + a.x + "," + a.y + " C" + (a.x + dx) + "," + a.y + " " + (b.x - dx) + "," + b.y + " " + b.x + "," + b.y;
  }
  function position(at) {
    for (const [id, item] of drawn.nodes) {
      const c = at.get(id);
      item.g.setAttribute("transform", "translate(" + c.x + "," + c.y + ")");
    }
    for (const e of drawn.edges) {
      const a = anchor(at, e.from, e.fromCol, "out");
      const b = anchor(at, e.to, e.toCol, "in");
      e.el.setAttribute("d", curve(a, b));
    }
  }

  function paint() {
    const matched = state.matches;
    const lit = focus || matched;
    for (const [id, item] of drawn.nodes) {
      const g = item.g;
      g.classList.toggle("selected", id === state.selected && !state.column);
      g.classList.toggle("on-path", !!(focus && focus.has(id)));
      g.classList.toggle("match", !focus && !!matched && matched.has(id));
      g.classList.toggle("dim", !!lit && !lit.has(id));
      for (const row of g.querySelectorAll(".row[data-col]")) {
        const col = row.getAttribute("data-col");
        row.classList.toggle("selected", col === state.column);
        row.classList.toggle("on-path", !!pathColumns && pathColumns.has(col));
      }
    }
    for (const e of drawn.edges) {
      let on;
      if (e.kind === "column") on = !!pathColumns && pathColumns.has(e.fromCol) && pathColumns.has(e.toCol);
      else if (e.kind === "reference") on = true;
      else on = !!focus && !pathColumns && focus.has(e.from) && focus.has(e.to);
      const dimmed = !!lit && !on && !(e.kind !== "column" && !focus && matched && matched.has(e.from) && matched.has(e.to));
      e.el.classList.toggle("hl", !!lit && on);
      e.el.classList.toggle("dim", dimmed);
      if (e.el.hasAttribute("marker-end")) e.el.setAttribute("marker-end", on && lit ? "url(#arrow-hl)" : "url(#arrow)");
    }
  }

  // Pan and zoom

  const view = { x: 0, y: 0, k: 1 };
  function applyView() {
    viewport.setAttribute("transform", "translate(" + view.x + "," + view.y + ") scale(" + view.k + ")");
    drawMinimap();
  }
  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }
  function zoomAt(px, py, factor) {
    const k = clamp(view.k * factor, 0.04, 3);
    view.x = px - ((px - view.x) * k) / view.k;
    view.y = py - ((py - view.y) * k) / view.k;
    view.k = k;
    applyView();
  }
  function bounds(at) {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const c of at.values()) {
      minX = Math.min(minX, c.x); minY = Math.min(minY, c.y);
      maxX = Math.max(maxX, c.x + W); maxY = Math.max(maxY, c.y + c.h);
    }
    return at.size ? { x: minX, y: minY, w: maxX - minX, h: maxY - minY } : null;
  }
  function fitTo(ids) {
    const subset = new Map();
    for (const id of ids) if (coords.has(id)) subset.set(id, coords.get(id));
    if (!subset.size) return;
    const b = bounds(subset);
    const r = svg.getBoundingClientRect();
    const padX = 70, padTop = 96, padBottom = 70;
    const k = clamp(Math.min((r.width - 2 * padX) / b.w, (r.height - padTop - padBottom) / b.h), 0.04, 1.1);
    view.k = k;
    view.x = (r.width - b.w * k) / 2 - b.x * k;
    view.y = padTop + (r.height - padTop - padBottom - b.h * k) / 2 - b.y * k;
    applyView();
  }
  function fit() {
    const b = bounds(coords);
    if (!b) return;
    const r = svg.getBoundingClientRect();
    const padX = 60, padTop = 90, padBottom = 60;
    view.k = clamp(Math.min((r.width - 2 * padX) / b.w, (r.height - padTop - padBottom) / b.h), 0.04, 1.25);
    view.x = (r.width - b.w * view.k) / 2 - b.x * view.k;
    view.y = padTop + (r.height - padTop - padBottom - b.h * view.k) / 2 - b.y * view.k;
    applyView();
  }
  function centreOn(id) {
    const c = coords.get(id);
    if (!c) return;
    const r = svg.getBoundingClientRect();
    if (view.k < 0.6) view.k = 0.8;
    view.x = r.width / 2 - (c.x + W / 2) * view.k;
    view.y = r.height / 2 - (c.y + Math.min(c.h, 300) / 2) * view.k;
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
    // Pinch (reported with ctrlKey) and mouse wheels zoom; two-finger trackpad scrolling pans.
    const pinch = ev.ctrlKey || ev.metaKey;
    const wheel = ev.deltaMode !== 0 || (ev.deltaX === 0 && Number.isInteger(ev.deltaY) && Math.abs(ev.deltaY) >= 50);
    if (pinch || wheel) {
      const scale = ev.deltaMode === 1 ? 0.05 : ev.deltaMode === 2 ? 1 : pinch ? 0.01 : 0.0018;
      zoomAt(ev.clientX - r.left, ev.clientY - r.top, Math.exp(-ev.deltaY * scale));
    } else {
      view.x -= ev.deltaX;
      view.y -= ev.deltaY;
      applyView();
    }
  }, { passive: false });
  svg.addEventListener("click", (ev) => {
    if (suppressClick) {
      suppressClick = false;
      return;
    }
    const more = ev.target.closest("[data-more]");
    if (more) {
      state.expanded.add(more.getAttribute("data-more"));
      render({ animate: true });
      return;
    }
    const toggle = ev.target.closest("[data-toggle]");
    if (toggle) {
      toggleColumns(toggle.getAttribute("data-toggle"));
      return;
    }
    const row = ev.target.closest("[data-col]");
    if (row) {
      const col = row.getAttribute("data-col");
      select(byId.get(col).parent, col === state.column ? null : col, false);
      return;
    }
    const g = ev.target.closest(".node");
    if (g) select(g.getAttribute("data-id"), null, false);
    else clearSelection();
  });
  svg.addEventListener("dblclick", (ev) => {
    const g = ev.target.closest(".node");
    if (g && !ev.target.closest("[data-col]")) toggleColumns(g.getAttribute("data-id"));
  });

  function toggleColumns(id) {
    const open = rowsOf(id).rows.length > 0;
    if (open) {
      state.expanded.delete(id);
      state.collapsed.add(id);
    } else {
      state.collapsed.delete(id);
      state.expanded.add(id);
    }
    render({ animate: true });
  }

  // Selection

  function select(id, columnId, centre) {
    if (!inView.has(id)) return;
    state.selected = id;
    state.column = columnId;
    state.collapsed.delete(id);
    computeFocus();
    // A column's path, or any selection in a large graph, is shown on its own unless the user
    // has chosen otherwise with the Isolate button.
    if (!isolateTouched) setIsolate(!!(focus && focus.size > 1 && (columnId || viewNodes.length > AUTO_ISOLATE)), false);
    // A selected column, or a jump from search or a link, brings its whole lineage into view.
    render({ animate: true, focus: !!columnId || centre, centre: centre ? id : null });
    renderPanel();
    writeHash();
  }
  function clearSelection() {
    state.selected = null;
    state.column = null;
    if (!isolateTouched) setIsolate(false, false);
    computeFocus();
    document.getElementById("panel").hidden = true;
    render({ animate: true });
    writeHash();
  }
  let isolateTouched = false;
  const isolateButton = document.getElementById("isolate");
  function setIsolate(on, touched) {
    state.isolate = on;
    if (touched) isolateTouched = true;
    isolateButton.setAttribute("aria-pressed", String(on));
  }
  isolateButton.addEventListener("click", () => {
    setIsolate(!state.isolate, true);
    render({ animate: true, fit: true });
  });
  const columnsButton = document.getElementById("columns");
  columnsButton.addEventListener("click", () => {
    if (!state.allColumns && visibleSet().size > EXPAND_ALL_LIMIT) {
      document.getElementById("match-count").textContent = "Too many tables to show every column; select one or isolate first.";
      return;
    }
    state.allColumns = !state.allColumns;
    state.collapsed.clear();
    if (!state.allColumns) state.expanded.clear();
    columnsButton.setAttribute("aria-pressed", String(state.allColumns));
    render({ animate: true });
  });
  for (const button of document.querySelectorAll("[data-direction]")) {
    button.addEventListener("click", () => {
      state.direction = button.getAttribute("data-direction");
      for (const other of document.querySelectorAll("[data-direction]")) other.setAttribute("aria-pressed", String(other === button));
      computeFocus();
      render({ animate: true });
      if (state.selected) renderPanel();
    });
  }
  document.getElementById("fit").addEventListener("click", fit);
  document.getElementById("zoom-in").addEventListener("click", () => {
    const r = svg.getBoundingClientRect();
    zoomAt(r.width / 2, r.height / 2, 1.25);
  });
  document.getElementById("zoom-out").addEventListener("click", () => {
    const r = svg.getBoundingClientRect();
    zoomAt(r.width / 2, r.height / 2, 0.8);
  });
  document.getElementById("close").addEventListener("click", clearSelection);

  // Theme

  function storage(key, value) {
    try {
      if (value === undefined) return window.localStorage.getItem(key);
      window.localStorage.setItem(key, value);
    } catch (error) {
      return null;
    }
    return null;
  }
  const prefersLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
  document.documentElement.setAttribute("data-theme", storage("kerplunk-theme") || (prefersLight ? "light" : "dark"));
  document.getElementById("theme").addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    storage("kerplunk-theme", next);
    drawTexture();
    drawMinimap();
  });

  // Sidebar

  function renderSidebar() {
    const root = display(meta.root);
    const name = root ? root.replace(/[\\/]+$/, "").split(/[\\/]/).pop() : "Graph";
    document.getElementById("project").textContent = name;
    document.title = name + " · kerplunk";
    const counts = new Map();
    for (const n of nodes) counts.set(n.kind, (counts.get(n.kind) || 0) + 1);
    const relations = viewNodes.filter((n) => n.kind !== "task").length;
    const columnEdges = edges.filter((e) => e.kind === "derived_from");
    const verified = columnEdges.filter((e) => attr(e, "verified") === "confirmed").length;
    const stats = [
      [relations, relations === 1 ? "table or model" : "tables and models"],
      [counts.get("column") || 0, "columns"],
      [columnEdges.length, "column edges"],
      verified ? [Math.round((100 * verified) / columnEdges.length) + "%", "verified on data"] : [counts.get("task") || 0, "tasks"],
    ];
    const box = document.getElementById("stats");
    for (const [value, text] of stats) {
      const stat = el("div", "stat");
      stat.append(el("b", null, typeof value === "number" ? value.toLocaleString("en-GB") : value), el("span", null, text));
      box.append(stat);
    }
    const parts = [];
    if (meta.generated_at) parts.push("Built " + display(meta.generated_at).slice(0, 16).replace("T", " "));
    if (meta.kerplunk_version) parts.push("kerplunk " + display(meta.kerplunk_version));
    if (meta.dialect) parts.push(display(meta.dialect));
    document.getElementById("built").textContent = parts.join(" · ");

    const filters = document.getElementById("filters");
    const present = new Map();
    for (const n of viewNodes) present.set(n.kind, (present.get(n.kind) || 0) + 1);
    for (const kind of VIEW_KINDS) {
      if (!present.has(kind)) continue;
      const chip = el("button", "chip");
      chip.type = "button";
      chip.setAttribute("aria-pressed", "true");
      chip.setAttribute("data-kind", kind);
      chip.append(icon(kind, 18), (KIND_NAMES[kind] || kind) + "s", el("b", null, present.get(kind)));
      chip.addEventListener("click", () => {
        if (state.hidden.has(kind)) state.hidden.delete(kind);
        else state.hidden.add(kind);
        chip.setAttribute("aria-pressed", String(!state.hidden.has(kind)));
        render({ animate: true });
      });
      filters.append(chip);
    }

    const warnings = data.warnings || [];
    const details = document.getElementById("warnings");
    if (warnings.length) {
      details.hidden = false;
      details.querySelector("summary").textContent = warnings.length + (warnings.length === 1 ? " warning" : " warnings") + " from the build";
      const list = details.querySelector("ul");
      for (const w of warnings) list.append(el("li", null, w));
    }

    const legend = document.getElementById("legend");
    for (const [text, cls] of [["Data flows into", "edge"], ["Runs", "edge runs"], ["Column lineage", "edge hl"], ["Joins or filters on", "edge reference"]]) {
      const row = el("div");
      const line = svgEl("svg", { class: "line", viewBox: "0 0 28 10", "aria-hidden": "true" });
      line.append(svgEl("path", { class: cls, d: "M1,5 L27,5" }));
      row.append(line, text);
      legend.append(row);
    }
  }

  // Search

  const search = document.getElementById("search");
  const results = document.getElementById("results");
  const haystack = [];
  for (const n of nodes) {
    if (inView.has(n.id) || (n.kind === "column" && viewOf(n.id))) {
      haystack.push({ id: n.id, text: label(n.id).toLowerCase(), extra: ((n.relation || "") + " " + (n.description || "")).toLowerCase() });
    }
  }
  let active = 0;
  let found = [];
  function runSearch() {
    const q = search.value.trim().toLowerCase();
    const counter = document.getElementById("match-count");
    results.replaceChildren();
    active = 0;
    if (!q) {
      state.matches = null;
      found = [];
      counter.textContent = "";
      paint();
      return;
    }
    const words = q.split(/\s+/);
    const scored = [];
    for (const item of haystack) {
      let score = 0;
      for (const w of words) {
        const at = item.text.indexOf(w);
        if (at >= 0) score += at === 0 || item.text[at - 1] === "." || item.text[at - 1] === "_" ? 3 : 2;
        else if (item.extra.includes(w)) score += 1;
        else {
          score = 0;
          break;
        }
      }
      if (!score) continue;
      if (item.text === q || item.text.endsWith("." + q)) score += 5;
      if (inView.has(item.id)) score += 0.5;
      scored.push([score, item]);
    }
    scored.sort((a, b) => b[0] - a[0] || a[1].text.length - b[1].text.length || a[1].text.localeCompare(b[1].text));
    found = scored.slice(0, MAX_RESULTS).map((s) => s[1].id);
    state.matches = new Set(scored.map((s) => viewOf(s[1].id)).filter(Boolean));
    counter.textContent = scored.length ? scored.length + (scored.length === 1 ? " match" : " matches") : "No matches";
    found.forEach((id, i) => {
      const n = byId.get(id);
      const li = el("li");
      li.setAttribute("role", "option");
      if (i === 0) li.classList.add("active");
      const button = el("button");
      button.type = "button";
      button.append(icon(n.kind === "column" ? byId.get(n.parent).kind : n.kind, 18), el("span", "label", label(id)), el("span", "kind", n.kind === "column" ? "column" : KIND_NAMES[n.kind] || n.kind));
      button.addEventListener("click", () => openResult(id));
      li.append(button);
      results.append(li);
    });
    paint();
  }
  function openResult(id) {
    const n = byId.get(id);
    if (n.kind === "column") select(n.parent, id, true);
    else select(id, null, true);
  }
  function moveActive(delta) {
    const items = results.querySelectorAll("li");
    if (!items.length) return;
    items[active].classList.remove("active");
    active = (active + delta + items.length) % items.length;
    items[active].classList.add("active");
    items[active].scrollIntoView({ block: "nearest" });
  }
  search.addEventListener("input", runSearch);
  search.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown") { ev.preventDefault(); moveActive(1); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); moveActive(-1); }
    else if (ev.key === "Enter" && found.length) openResult(found[active]);
    else if (ev.key === "Escape") { search.value = ""; runSearch(); search.blur(); }
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.target instanceof HTMLInputElement || ev.metaKey || ev.ctrlKey || ev.altKey) return;
    if (ev.key === "/") { ev.preventDefault(); search.focus(); search.select(); }
    else if (ev.key === "Escape") clearSelection();
    else if (ev.key === "f" || ev.key === "F") fit();
    else if (ev.key === "i" || ev.key === "I") isolateButton.click();
    else if (ev.key === "c" || ev.key === "C") columnsButton.click();
    else if (ev.key === "+" || ev.key === "=") document.getElementById("zoom-in").click();
    else if (ev.key === "-") document.getElementById("zoom-out").click();
  });

  // Detail panel

  const panel = document.getElementById("panel");
  const head = document.getElementById("panel-head");
  const body = document.getElementById("panel-body");

  function section(title, count) {
    const h = el("h3", null, title);
    if (count !== undefined) h.append(el("small", null, String(count)));
    return h;
  }
  function relationLink(id) {
    const button = el("button", "link", label(id));
    button.type = "button";
    button.title = id;
    button.addEventListener("click", () => select(id, null, true));
    return button;
  }
  function columnLink(id) {
    const n = byId.get(id);
    const button = el("button", "link", label(id));
    button.type = "button";
    button.title = id;
    button.addEventListener("click", () => {
      if (n && n.parent && inView.has(n.parent)) select(n.parent, id, true);
    });
    return button;
  }
  function list(items, make) {
    const ul = el("ul", "list");
    for (const item of items) {
      const li = el("li");
      make(li, item);
      ul.append(li);
    }
    return ul;
  }
  function command(text) {
    const box = el("div", "command");
    const code = el("code", null, text);
    const copy = el("button", null, "Copy");
    copy.type = "button";
    copy.addEventListener("click", () => {
      const done = () => { copy.textContent = "Copied"; setTimeout(() => (copy.textContent = "Copy"), 1400); };
      if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, () => selectText(code));
      else selectText(code);
    });
    box.append(code, copy);
    return box;
  }
  function selectText(node) {
    const range = document.createRange();
    range.selectNodeContents(node);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }
  function formula(text) {
    const pre = el("pre", "formula");
    pre.append(el("span", "eq", "= "), text);
    return pre;
  }
  function ref(id) {
    const text = label(id);
    return /^[\w.]+$/.test(text) ? text : JSON.stringify(text);
  }

  function renderPanel() {
    const relation = byId.get(state.selected);
    const n = state.column ? byId.get(state.column) : relation;
    panel.hidden = false;
    head.replaceChildren();
    body.replaceChildren();
    const kindName = state.column ? "Column of " + (KIND_NAMES[relation.kind] || relation.kind).toLowerCase() : KIND_NAMES[n.kind] || n.kind;
    head.append(el("p", "eyebrow", kindName));
    head.append(el("h2", null, state.column ? label(n.id) : n.name));
    if (state.column) {
      if (n.data_type) head.append(el("p", "relation", n.data_type.toLowerCase()));
    } else if (n.relation) head.append(el("p", "relation", n.relation));
    if (n.description) head.append(el("p", "description", n.description));
    if (state.column) renderColumn(n);
    else renderRelation(n);
    panel.scrollTop = 0;
  }

  function renderRelation(n) {
    const tags = el("div", "tags");
    for (const key of ["materialized", "table_type", "schedule", "operator", "orchestrator"]) {
      const v = attr(n, key);
      if (v) tags.append(el("span", "tag", display(v)));
    }
    for (const origin of n.origins || []) tags.append(el("span", "tag", origin));
    if (tags.childNodes.length) head.append(tags);

    const columns = columnsOf(n.id);
    const ups = (upAdj.get(n.id) || []).slice().sort();
    const downs = (downAdj.get(n.id) || []).slice().sort();
    const tests = [];
    for (const target of [n.id].concat(columns.map((c) => c.id))) for (const e of ins(target, "tests")) tests.push([e.source, target]);
    const facts = el("div", "facts");
    for (const [value, text] of [[columns.length, "columns"], [ups.length, "upstream"], [downs.length, "downstream"], [tests.length, "tests"]]) {
      const f = el("div");
      f.append(el("b", null, value), el("span", null, text));
      facts.append(f);
    }
    body.append(facts);

    if (columns.length) {
      body.append(section("Columns", columns.length));
      const table = el("table", "columns");
      for (const c of columns) {
        const row = el("tr");
        const name = el("td");
        const button = el("button", "link", c.name);
        button.type = "button";
        button.title = "Show the lineage of " + c.name;
        button.addEventListener("click", () => select(n.id, c.id, false));
        name.append(button);
        if (c.description) name.append(el("div", "note", c.description));
        row.append(name, el("td", "type", c.data_type ? c.data_type.toLowerCase() : ""));
        table.append(row);
      }
      body.append(table);
    }
    const conditions = conditionsOf[n.id] || [];
    if (conditions.length) {
      body.append(section("Rows shaped by", conditions.length));
      const box = el("div", "conditions");
      for (const c of conditions) box.append(el("code", null, c));
      body.append(box);
    }
    if (ups.length) {
      body.append(section("Upstream", ups.length));
      body.append(list(ups, (li, id) => li.append(relationLink(id), el("span", "meta", byId.get(id).kind))));
    }
    if (downs.length) {
      body.append(section("Downstream", downs.length));
      body.append(list(downs, (li, id) => li.append(relationLink(id), el("span", "meta", byId.get(id).kind))));
    }
    if (tests.length) {
      body.append(section("Tests", tests.length));
      body.append(list(tests, (li, [test, target]) => {
        const t = byId.get(test);
        li.append(el("span", "grow", t ? t.name : test));
        if (target !== n.id) li.append(el("span", "meta", byId.get(target).name));
      }));
    }
    const runs = outs(n.id, "runs").map((e) => e.target).filter((id) => byId.has(id));
    if (runs.length) {
      body.append(section("Runs", runs.length));
      body.append(list(runs, (li, id) => li.append(inView.has(id) ? relationLink(id) : el("span", "grow", label(id)), el("span", "meta", byId.get(id).kind))));
    }
    const runBy = ins(n.id, "runs").map((e) => e.source).filter((id) => byId.has(id) && byId.get(id).kind === "task");
    if (runBy.length) {
      body.append(section("Run by", runBy.length));
      body.append(list(runBy, (li, id) => li.append(relationLink(id))));
    }
    const facts2 = [["File", n.path], ["Line", attr(n, "line")], ["SQL", attr(n, "sql_path")], ["Sensors", attr(n, "sensors")]].filter(([, v]) => v);
    if (facts2.length) {
      body.append(section("Where"));
      body.append(list(facts2, (li, [k, v]) => li.append(el("span", "meta", k), el("span", "grow mono", display(v)))));
    }
    body.append(section("Ask kerplunk"));
    const commands = el("div", "commands");
    commands.append(command("kerplunk impact " + ref(n.id)), command("kerplunk describe " + ref(n.id)));
    body.append(commands);
  }

  function renderColumn(n) {
    const info = formulas[n.id];
    if (info && info.e) {
      body.append(section("Formula"));
      body.append(formula(info.e));
      if (info.c && info.c !== info.e) {
        body.append(section("In terms of sources"));
        body.append(formula(info.c));
        if (info.s && info.s.length) body.append(el("p", "note", "From " + info.s.join(", ") + (info.p ? ". Partial: some steps are not inlined, because they have no recorded expression or the formula would grow too long; kerplunk explain shows each step." : "")));
      }
    } else {
      body.append(section("Formula"));
      body.append(el("p", "note", ins(n.id, "derived_from").length ? "Read directly from the warehouse or a seed." : outs(n.id, "derived_from").length ? "The SQL that builds this column could not be reduced to a formula." : "Read from a source; nothing upstream in the graph."));
    }
    const up = state.direction !== "downstream" ? columnLineage(n.id, true) : [];
    const down = state.direction !== "upstream" ? columnLineage(n.id, false) : [];
    const lineage = (items) => list(items, (li, item) => {
      li.style.paddingLeft = (item.depth - 1) * 14 + "px";
      li.append(columnLink(item.id));
      const grow = el("span", "grow");
      li.append(grow);
      if (item.transform) li.append(el("span", "meta", item.transform));
      if (item.verified === "confirmed") li.append(el("span", "ok", "verified"));
    });
    if (state.direction !== "downstream") {
      body.append(section("Comes from", up.length));
      body.append(up.length ? lineage(up) : el("p", "note", "Nothing upstream."));
    }
    if (state.direction !== "upstream") {
      body.append(section("Feeds", down.length));
      body.append(down.length ? lineage(down) : el("p", "note", "Nothing downstream reads this column."));
    }
    const refs = outs(n.id, "feeds").filter(isReference).map((e) => viewOf(e.target)).filter(Boolean);
    if (refs.length) {
      body.append(section("Joined, filtered or grouped on by", refs.length));
      body.append(list(Array.from(new Set(refs)), (li, id) => li.append(relationLink(id))));
    }
    const conditions = conditionsOf[n.parent] || [];
    if (conditions.length) {
      body.append(section("Rows shaped by", conditions.length));
      const box = el("div", "conditions");
      for (const c of conditions) box.append(el("code", null, c));
      body.append(box);
    }
    const tests = ins(n.id, "tests").map((e) => byId.get(e.source)).filter(Boolean);
    if (tests.length) {
      body.append(section("Tests", tests.length));
      body.append(list(tests, (li, t) => li.append(el("span", "grow", t.name))));
    }
    body.append(section("Ask kerplunk"));
    const commands = el("div", "commands");
    commands.append(command("kerplunk explain " + ref(n.id)), command("kerplunk impact " + ref(n.id)));
    body.append(commands);
  }

  // Deep links: #<node id> or #<relation id>|<column id>.

  let writing = false;
  function writeHash() {
    const next = state.selected ? "#" + encodeURIComponent(state.column || state.selected) : "";
    if (location.hash === next) return;
    writing = true;
    history.replaceState(null, "", next || location.pathname + location.search);
    writing = false;
  }
  function readHash() {
    if (writing) return false;
    const id = decodeURIComponent(location.hash.slice(1));
    const n = byId.get(id);
    if (!n) return false;
    if (n.kind === "column" && n.parent && inView.has(n.parent)) select(n.parent, id, true);
    else if (inView.has(id)) select(id, null, true);
    else return false;
    return true;
  }
  window.addEventListener("hashchange", readHash);

  // Minimap

  const minimap = document.getElementById("minimap");
  const mini = minimap.getContext("2d");
  let miniFrame = 0;
  let miniTransform = null;
  function drawMinimap() {
    cancelAnimationFrame(miniFrame);
    miniFrame = requestAnimationFrame(() => {
      const styles = getComputedStyle(document.documentElement);
      const b = bounds(coords);
      mini.clearRect(0, 0, minimap.width, minimap.height);
      minimap.hidden = !b || coords.size < 6;
      if (!b) return;
      const pad = 16;
      const k = Math.min((minimap.width - 2 * pad) / b.w, (minimap.height - 2 * pad) / b.h);
      const ox = pad + (minimap.width - 2 * pad - b.w * k) / 2 - b.x * k;
      const oy = pad + (minimap.height - 2 * pad - b.h * k) / 2 - b.y * k;
      miniTransform = { k, ox, oy };
      const muted = styles.getPropertyValue("--edge").trim();
      const accent = styles.getPropertyValue("--accent").trim();
      for (const [id, c] of coords) {
        mini.fillStyle = focus && focus.has(id) ? accent : muted;
        mini.fillRect(ox + c.x * k, oy + c.y * k, Math.max(2, W * k), Math.max(2, c.h * k));
      }
      const r = svg.getBoundingClientRect();
      mini.strokeStyle = accent;
      mini.lineWidth = 2;
      mini.strokeRect(ox + (-view.x / view.k) * k, oy + (-view.y / view.k) * k, (r.width / view.k) * k, (r.height / view.k) * k);
    });
  }
  function miniPan(ev) {
    if (!miniTransform) return;
    const rect = minimap.getBoundingClientRect();
    const mx = ((ev.clientX - rect.left) / rect.width) * minimap.width;
    const my = ((ev.clientY - rect.top) / rect.height) * minimap.height;
    const gx = (mx - miniTransform.ox) / miniTransform.k;
    const gy = (my - miniTransform.oy) / miniTransform.k;
    const r = svg.getBoundingClientRect();
    view.x = r.width / 2 - gx * view.k;
    view.y = r.height / 2 - gy * view.k;
    applyView();
  }
  let miniDrag = false;
  minimap.addEventListener("pointerdown", (ev) => { miniDrag = true; minimap.setPointerCapture(ev.pointerId); miniPan(ev); });
  minimap.addEventListener("pointermove", (ev) => { if (miniDrag) miniPan(ev); });
  minimap.addEventListener("pointerup", () => { miniDrag = false; });

  // Wave lines (DESIGN.md section 8): fine vertical lines, calmer towards the left.

  function drawTexture() {
    const texture = document.getElementById("texture");
    texture.replaceChildren();
    const r = texture.getBoundingClientRect();
    const width = r.width || 1200, height = r.height || 800;
    texture.setAttribute("viewBox", "0 0 " + width + " " + height);
    let seed = 1729;
    const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    const lines = 22;
    for (let i = 0; i < lines; i++) {
      const base = width * (0.7 + (0.32 * i) / lines);
      const amp = 18 + rand() * 40;
      const freq = 0.0025 + rand() * 0.004;
      const phase = rand() * Math.PI * 2;
      let d = "";
      for (let y = 0; y <= height; y += 16) {
        const x = base + Math.sin(y * freq + phase) * amp + Math.sin(y * freq * 2.7 + phase * 1.3) * amp * 0.35;
        d += (y ? " L" : "M") + x.toFixed(1) + "," + y;
      }
      const path = svgEl("path", { d, opacity: (0.35 + (0.65 * i) / lines).toFixed(2) });
      texture.append(path);
    }
  }

  // Start

  renderSidebar();
  drawTexture();
  const start = () => {
    computeFocus();
    render({});
    if (!readHash()) requestAnimationFrame(fit);
  };
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(start, start);
  else start();
  let resizeFrame = 0;
  window.addEventListener("resize", () => {
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => {
      drawTexture();
      drawMinimap();
    });
  });
})();
