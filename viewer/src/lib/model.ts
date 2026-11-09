// An indexed, read-only view of the graph: lookups, labels and the walks the viewer needs.

import type { Attrs, EdgeKind, GraphData, GraphEdge, GraphNode, NodeKind } from "./types";

export const VIEW_KINDS: readonly NodeKind[] = [
    "source",
    "seed",
    "snapshot",
    "table",
    "model",
    "task",
    "exposure",
];

export const KIND_NAMES: Record<string, string> = {
    source: "Source",
    seed: "Seed",
    snapshot: "Snapshot",
    table: "Table",
    model: "Model",
    task: "Task",
    exposure: "Exposure",
    column: "Column",
    test: "Test",
    dag: "DAG",
    term: "Business term",
};

export interface LayoutEdge {
    source: string;
    target: string;
    kind: "feeds" | "runs";
}

export interface LineageItem {
    id: string;
    depth: number;
    transform?: string;
    verified?: string;
}

function push<K, V>(map: Map<K, V[]>, key: K, value: V): void {
    const list = map.get(key);
    if (list) list.push(value);
    else map.set(key, [value]);
}

export function attr<T = unknown>(item: { attrs?: Attrs } | undefined, key: string): T | undefined {
    return item?.attrs ? (item.attrs[key] as T | undefined) : undefined;
}

export function byName(a: GraphNode, b: GraphNode): number {
    return (a.name || "").localeCompare(b.name || "") || a.id.localeCompare(b.id);
}

export class GraphModel {
    readonly nodes: GraphNode[];
    readonly edges: GraphEdge[];
    readonly byId = new Map<string, GraphNode>();
    readonly viewNodes: GraphNode[];
    readonly inView: Set<string>;
    readonly layoutEdges: LayoutEdge[] = [];
    readonly upAdj: Map<string, string[]>;
    readonly downAdj: Map<string, string[]>;
    private readonly outE = new Map<string, GraphEdge[]>();
    private readonly inE = new Map<string, GraphEdge[]>();
    private readonly columnCache = new Map<string, GraphNode[]>();

    constructor(data: GraphData) {
        this.nodes = data.nodes ?? [];
        this.edges = data.edges ?? [];
        for (const n of this.nodes) this.byId.set(n.id, n);
        for (const e of this.edges) {
            push(this.outE, e.source, e);
            push(this.inE, e.target, e);
        }
        this.viewNodes = this.nodes.filter((n) => VIEW_KINDS.includes(n.kind));
        this.inView = new Set(this.viewNodes.map((n) => n.id));
        // Relation-level edges: feeds between relations (a column stands in for its relation) and runs.
        const seen = new Set<string>();
        for (const e of this.edges) {
            if (e.kind !== "feeds" && e.kind !== "runs") continue;
            const s =
                e.kind === "feeds" ? this.viewOf(e.source) : this.inView.has(e.source) ? e.source : null;
            const t =
                e.kind === "feeds" ? this.viewOf(e.target) : this.inView.has(e.target) ? e.target : null;
            if (!s || !t || s === t) continue;
            const key = `${e.kind}\u0000${s}\u0000${t}`;
            if (seen.has(key)) continue;
            seen.add(key);
            this.layoutEdges.push({ source: s, target: t, kind: e.kind });
        }
        this.upAdj = new Map(this.viewNodes.map((n) => [n.id, [] as string[]]));
        this.downAdj = new Map(this.viewNodes.map((n) => [n.id, [] as string[]]));
        for (const e of this.layoutEdges) {
            this.downAdj.get(e.source)?.push(e.target);
            this.upAdj.get(e.target)?.push(e.source);
        }
    }

    node(id: string): GraphNode | undefined {
        return this.byId.get(id);
    }

    outs(id: string, kind: EdgeKind): GraphEdge[] {
        return (this.outE.get(id) ?? []).filter((e) => e.kind === kind);
    }

    ins(id: string, kind: EdgeKind): GraphEdge[] {
        return (this.inE.get(id) ?? []).filter((e) => e.kind === kind);
    }

    columnsOf(id: string): GraphNode[] {
        let found = this.columnCache.get(id);
        if (!found) {
            found = this.outs(id, "contains")
                .map((e) => this.byId.get(e.target))
                .filter((n): n is GraphNode => n !== undefined && n.kind === "column");
            this.columnCache.set(id, found);
        }
        return found;
    }

    /** The relation a node is drawn as: itself, or a column's relation. */
    viewOf(id: string): string | null {
        if (this.inView.has(id)) return id;
        const n = this.byId.get(id);
        return n && n.kind === "column" && n.parent && this.inView.has(n.parent) ? n.parent : null;
    }

    label(id: string): string {
        const n = this.byId.get(id);
        if (!n) return id;
        if (n.kind === "column" && n.parent) return `${this.label(n.parent)}.${n.name}`;
        const sourceName = attr<string>(n, "source_name");
        if (n.kind === "source" && sourceName) return `${sourceName}.${n.name}`;
        const dag = attr<string>(n, "dag");
        if (n.kind === "task" && dag) return `${dag}.${n.name}`;
        return n.name;
    }

    boundTo(termId: string): string[] {
        return this.outs(termId, "defines")
            .map((e) => e.target)
            .filter((id) => this.byId.has(id));
    }

    termsOf(id: string): GraphNode[] {
        return this.ins(id, "defines")
            .map((e) => this.byId.get(e.source))
            .filter((n): n is GraphNode => n !== undefined);
    }

    walk(starts: string[], adjacency: Map<string, string[]>): Set<string> {
        const seen = new Set(starts);
        const queue = starts.slice();
        for (let i = 0; i < queue.length; i++) {
            for (const other of adjacency.get(queue[i]!) ?? []) {
                if (!seen.has(other)) {
                    seen.add(other);
                    queue.push(other);
                }
            }
        }
        return seen;
    }

    /** Upstream follows derived_from out-edges; downstream follows them in reverse. */
    columnLineage(start: string, upstream: boolean): LineageItem[] {
        const depth = new Map<string, number>([[start, 0]]);
        const queue = [start];
        const found: LineageItem[] = [];
        for (let i = 0; i < queue.length; i++) {
            const id = queue[i]!;
            const next = upstream ? this.outs(id, "derived_from") : this.ins(id, "derived_from");
            for (const e of next) {
                const other = upstream ? e.target : e.source;
                if (depth.has(other)) continue;
                const d = (depth.get(id) ?? 0) + 1;
                depth.set(other, d);
                queue.push(other);
                found.push({
                    id: other,
                    depth: d,
                    transform: attr<string>(e, "transform"),
                    verified: attr<string>(e, "verified"),
                });
            }
        }
        return found;
    }
}

export function display(value: unknown): string {
    if (value === null || value === undefined) return "";
    if (Array.isArray(value)) return value.map(display).join(", ");
    return typeof value === "object" ? JSON.stringify(value) : String(value);
}
