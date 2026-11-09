// The shapes konstruct embeds in the page: Graph.to_dict() and the explain summaries.

export type NodeKind =
    | "source"
    | "table"
    | "column"
    | "model"
    | "seed"
    | "snapshot"
    | "dag"
    | "task"
    | "test"
    | "exposure"
    | "term";

export type EdgeKind =
    "contains" | "feeds" | "derived_from" | "runs" | "precedes" | "tests" | "defines" | "relates_to";

export type Attrs = Record<string, unknown>;

export interface GraphNode {
    id: string;
    kind: NodeKind;
    name: string;
    parent?: string;
    relation?: string;
    description?: string;
    data_type?: string;
    path?: string;
    origins?: string[];
    attrs?: Attrs;
}

export interface GraphEdge {
    source: string;
    target: string;
    kind: EdgeKind;
    attrs?: Attrs;
}

export interface GraphMeta {
    root?: string;
    generated_at?: string;
    konstruct_version?: string;
    dialect?: string;
    [key: string]: unknown;
}

export interface GraphData {
    format_version?: number;
    meta?: GraphMeta;
    nodes?: GraphNode[];
    edges?: GraphEdge[];
    warnings?: string[];
}

/** One column's formula: e (expression), c (composed), s (sources), p (partial). */
export interface Formula {
    e?: string;
    c?: string;
    s?: string[];
    p?: number;
}

export interface Explanations {
    columns?: Record<string, Formula>;
    conditions?: Record<string, string[]>;
}

export interface Owner {
    name: string;
    role?: string | null;
}

export interface Usage {
    queries: number;
    users: number;
    days: number;
    score?: number;
}

export type Direction = "upstream" | "both" | "downstream";

export interface Point {
    x: number;
    y: number;
    h: number;
}
