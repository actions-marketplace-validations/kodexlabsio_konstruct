// The viewer's state: what is selected and shown, and everything derived from it.

import { SvelteSet } from 'svelte/reactivity';
import { computeFocus, type Focus } from './focus';
import { computeOrder, HEAD, PAD, place, ROW } from './layout';
import { attr, GraphModel } from './model';
import { buildIndex, search, type SearchResult } from './search';
import type { Direction, Explanations, Formula, GraphData, GraphNode, Point } from './types';

export const ANIMATE_LIMIT = 400;
export const EXPAND_ALL_LIMIT = 400;
export const AUTO_ISOLATE = 120;

export type ViewRequest = { kind: 'fit' | 'focus' | 'centre'; id?: string; n: number };

export interface Rows {
    rows: GraphNode[];
    more: number;
}

export class Viewer {
    readonly model: GraphModel;
    readonly data: GraphData;
    readonly formulas: Record<string, Formula>;
    readonly conditions: Record<string, string[]>;
    private readonly index;

    selected = $state<string | null>(null);
    column = $state<string | null>(null);
    term = $state<string | null>(null);
    direction = $state<Direction>('both');
    isolate = $state(false);
    allColumns = $state(false);
    query = $state('');
    notice = $state('');
    theme = $state<'dark' | 'light'>('dark');
    view = $state<ViewRequest | null>(null);
    readonly expanded = new SvelteSet<string>();
    readonly collapsed = new SvelteSet<string>();
    readonly hidden = new SvelteSet<string>();
    /** Once the user has pressed Isolate, the viewer stops choosing for them. */
    isolateTouched = false;
    private requests = 0;
    private orderKey = '';
    private orderCache: string[][] = [];

    constructor(data: GraphData, explanations: Explanations) {
        this.data = data;
        this.model = new GraphModel(data);
        this.formulas = explanations.columns ?? {};
        this.conditions = explanations.conditions ?? {};
        this.index = buildIndex(this.model);
    }

    readonly focus: Focus = $derived.by(() =>
        computeFocus(this.model, {
            selected: this.selected,
            column: this.column,
            term: this.term,
            direction: this.direction,
        }),
    );

    readonly results: SearchResult = $derived.by(() => search(this.model, this.index, this.query));

    readonly visible: Set<string> = $derived.by(() => {
        const visible = new Set<string>();
        const lit = this.focus.relations;
        for (const n of this.model.viewNodes) {
            if (this.hidden.has(n.kind) && n.id !== this.selected) continue;
            if (this.isolate && lit && !lit.has(n.id)) continue;
            visible.add(n.id);
        }
        return visible;
    });

    readonly order: string[][] = $derived.by(() => {
        const key = Array.from(this.visible).sort().join('\u0001');
        if (key !== this.orderKey) {
            this.orderCache = computeOrder(this.model, this.visible);
            this.orderKey = key;
        }
        return this.orderCache;
    });

    readonly target: Map<string, Point> = $derived.by(() => place(this.order, id => this.heightOf(id)));

    /** Which column rows a card shows: all when expanded, the lineage when a column is selected. */
    rowsOf(id: string): Rows {
        const all = this.model.columnsOf(id);
        if (!all.length || this.collapsed.has(id)) return { rows: [], more: 0 };
        if (this.expanded.has(id) || this.allColumns) return { rows: all, more: 0 };
        const columns = this.focus.columns;
        if (columns && this.focus.relations?.has(id)) {
            const rows = all.filter(c => columns.has(c.id));
            return { rows, more: rows.length ? all.length - rows.length : 0 };
        }
        return { rows: [], more: 0 };
    }

    heightOf(id: string): number {
        const { rows, more } = this.rowsOf(id);
        const count = rows.length + (more ? 1 : 0);
        return count ? HEAD + PAD + count * ROW + PAD : HEAD;
    }

    request(kind: ViewRequest['kind'], id?: string): void {
        this.view = { kind, id, n: ++this.requests };
    }

    private autoIsolate(on: boolean): void {
        if (!this.isolateTouched) this.isolate = on;
    }

    select(id: string, columnId: string | null = null, centre = false): void {
        if (!this.model.inView.has(id)) return;
        this.term = null;
        this.selected = id;
        this.column = columnId;
        this.collapsed.delete(id);
        // A column's path, or any selection in a large graph, is shown on its own unless the user
        // has chosen otherwise with the Isolate button.
        const lit = this.focus.relations;
        this.autoIsolate(!!(lit && lit.size > 1 && (columnId || this.model.viewNodes.length > AUTO_ISOLATE)));
        // A selected column, or a jump from search or a link, brings its whole lineage into view.
        if (columnId || centre) this.request('focus', id);
    }

    selectTerm(id: string): void {
        this.term = id;
        this.column = null;
        this.selected =
            this.model
                .boundTo(id)
                .map(b => this.model.viewOf(b))
                .find((v): v is string => v !== null) ?? null;
        const lit = this.focus.relations;
        this.autoIsolate(!!(lit && lit.size > 0));
        this.request('focus');
    }

    clear(): void {
        this.selected = null;
        this.column = null;
        this.term = null;
        this.autoIsolate(false);
    }

    toggleIsolate(): void {
        this.isolate = !this.isolate;
        this.isolateTouched = true;
        this.request('fit');
    }

    toggleAllColumns(): void {
        if (!this.allColumns && this.visible.size > EXPAND_ALL_LIMIT) {
            this.notice = 'Too many tables to show every column; select one or isolate first.';
            return;
        }
        this.notice = '';
        this.allColumns = !this.allColumns;
        this.collapsed.clear();
        if (!this.allColumns) this.expanded.clear();
    }

    toggleColumns(id: string): void {
        if (this.rowsOf(id).rows.length > 0) {
            this.expanded.delete(id);
            this.collapsed.add(id);
        } else {
            this.collapsed.delete(id);
            this.expanded.add(id);
        }
    }

    toggleKind(kind: string): void {
        if (this.hidden.has(kind)) this.hidden.delete(kind);
        else this.hidden.add(kind);
    }

    /** The node a deep link names: a term, a column (with its relation) or a relation. */
    open(id: string): boolean {
        const n = this.model.node(id);
        if (!n) return false;
        if (n.kind === 'term') this.selectTerm(id);
        else if (n.kind === 'column' && n.parent && this.model.inView.has(n.parent))
            this.select(n.parent, id, true);
        else if (this.model.inView.has(id)) this.select(id, null, true);
        else return false;
        return true;
    }

    get hash(): string {
        const target = this.term ?? this.column ?? this.selected;
        return target ? `#${encodeURIComponent(target)}` : '';
    }

    status(n: GraphNode): string {
        return attr<string>(n, 'status') ?? 'draft';
    }
}
