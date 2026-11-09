import { describe, expect, it } from "vitest";
import { computeFocus } from "../src/lib/focus";
import { bounds, computeLayers, computeOrder, curve, place, W } from "../src/lib/layout";
import { GraphModel } from "../src/lib/model";
import { buildIndex, search } from "../src/lib/search";
import { graph } from "./fixture";

const model = new GraphModel(graph);

describe("the graph model", () => {
    it("draws relations and tasks, not columns or terms", () => {
        expect(model.viewNodes.map((n) => n.id)).toEqual([
            "source.raw.orders",
            "model.stg_orders",
            "model.orders",
            "task.nightly.run",
        ]);
    });

    it("labels sources, tasks and columns the way the CLI does", () => {
        expect(model.label("source.raw.orders")).toBe("raw.orders");
        expect(model.label("task.nightly.run")).toBe("nightly.run");
        expect(model.label("model.orders#revenue")).toBe("orders.revenue");
    });

    it("folds a filter column into its relation for relation edges", () => {
        expect(model.layoutEdges).toContainEqual({
            source: "source.raw.orders",
            target: "model.stg_orders",
            kind: "feeds",
        });
        expect(model.layoutEdges.filter((e) => e.source === "source.raw.orders")).toHaveLength(1);
    });

    it("walks column lineage both ways with depth, transform and verification", () => {
        expect(model.columnLineage("model.orders#revenue", true)).toEqual([
            { id: "model.stg_orders#amount", depth: 1, transform: "transformed", verified: "confirmed" },
            { id: "source.raw.orders#amount", depth: 2, transform: "direct", verified: undefined },
        ]);
        expect(model.columnLineage("source.raw.orders#amount", false).map((i) => i.id)).toEqual([
            "model.stg_orders#amount",
            "model.orders#revenue",
        ]);
    });

    it("knows which terms define a column", () => {
        expect(model.boundTo("term.revenue")).toEqual(["model.orders#revenue"]);
        expect(model.termsOf("model.orders#revenue").map((t) => t.name)).toEqual(["Revenue"]);
    });
});

describe("layout", () => {
    it("layers by longest path and breaks cycles", () => {
        const layers = computeLayers(
            ["a", "b", "c"],
            [
                { source: "a", target: "b", kind: "feeds" },
                { source: "b", target: "c", kind: "feeds" },
                { source: "c", target: "a", kind: "feeds" },
            ],
        );
        expect([layers.get("a"), layers.get("b"), layers.get("c")]).toEqual([0, 1, 2]);
    });

    it("puts a task just before what it runs", () => {
        const order = computeOrder(model, new Set(model.viewNodes.map((n) => n.id)));
        const layerOf = (id: string): number => order.findIndex((row) => row.includes(id));
        expect(layerOf("source.raw.orders")).toBeLessThan(layerOf("model.stg_orders"));
        expect(layerOf("model.stg_orders")).toBeLessThan(layerOf("model.orders"));
        expect(layerOf("task.nightly.run")).toBe(layerOf("model.orders") - 1);
    });

    it("stacks cards by height and measures bounds", () => {
        const coords = place([["a", "b"]], (id) => (id === "a" ? 46 : 100));
        expect(coords.get("a")).toEqual({ x: 0, y: -84, h: 46 });
        expect(coords.get("b")).toEqual({ x: 0, y: -16, h: 100 });
        expect(bounds(coords)).toEqual({ x: 0, y: -84, w: W, h: 168 });
        expect(bounds(new Map())).toBeNull();
    });

    it("draws backward edges beneath", () => {
        expect(curve({ x: 0, y: 0 }, { x: 200, y: 0 })).toBe("M0,0 C100,0 100,0 200,0");
        expect(curve({ x: 200, y: 0 }, { x: 0, y: 0 })).toContain("C260,40");
    });
});

describe("focus", () => {
    it("lights a relation's lineage in the chosen direction", () => {
        const both = computeFocus(model, {
            selected: "model.stg_orders",
            column: null,
            term: null,
            direction: "both",
        });
        // The task runs orders, which is downstream of stg_orders, so the task is not in its lineage.
        expect([...(both.relations ?? [])].sort()).toEqual([
            "model.orders",
            "model.stg_orders",
            "source.raw.orders",
        ]);
        const ofOrders = computeFocus(model, {
            selected: "model.orders",
            column: null,
            term: null,
            direction: "upstream",
        });
        expect(ofOrders.relations?.has("task.nightly.run")).toBe(true);
        const up = computeFocus(model, {
            selected: "model.stg_orders",
            column: null,
            term: null,
            direction: "upstream",
        });
        expect([...(up.relations ?? [])].sort()).toEqual(["model.stg_orders", "source.raw.orders"]);
    });

    it("lights a column's path and the filters on it", () => {
        const focus = computeFocus(model, {
            selected: "model.orders",
            column: "model.orders#revenue",
            term: null,
            direction: "both",
        });
        expect([...(focus.columns ?? [])].sort()).toEqual([
            "model.orders#revenue",
            "model.stg_orders#amount",
            "source.raw.orders#amount",
        ]);
        expect(focus.references).toEqual([]);
        const fromSource = computeFocus(model, {
            selected: "source.raw.orders",
            column: "source.raw.orders#status",
            term: null,
            direction: "both",
        });
        expect(fromSource.references).toEqual([["source.raw.orders#status", "model.stg_orders"]]);
    });

    it("lights a term's columns", () => {
        const focus = computeFocus(model, {
            selected: null,
            column: null,
            term: "term.revenue",
            direction: "both",
        });
        expect([...(focus.columns ?? [])]).toEqual(["model.orders#revenue"]);
        expect([...(focus.relations ?? [])]).toEqual(["model.orders"]);
    });
});

describe("search", () => {
    const index = buildIndex(model);

    it("ranks an exact synonym first and highlights the term's relation", () => {
        const result = search(model, index, "sales");
        expect(result.ids[0]).toBe("term.revenue");
        expect([...result.relations]).toEqual(["model.orders"]);
    });

    it("matches word starts in labels and needs every word", () => {
        // Equal scores and lengths fall back to alphabetical order.
        expect(search(model, index, "orders amount").ids).toEqual([
            "source.raw.orders#amount",
            "model.stg_orders#amount",
        ]);
        expect(search(model, index, "orders zzz").total).toBe(0);
        expect(search(model, index, "  ").ids).toEqual([]);
    });
});
