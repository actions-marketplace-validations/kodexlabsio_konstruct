// What a selection lights up: the relations in its lineage, and for a column or term the columns.

import { attr, type GraphModel } from './model';
import type { Direction } from './types';

export interface Selection {
    selected: string | null;
    column: string | null;
    term: string | null;
    direction: Direction;
}

export interface Focus {
    /** Relation ids in the lineage of the selection. */
    relations: Set<string> | null;
    /** Column ids in the lineage of the selected column, or the columns a term binds. */
    columns: Set<string> | null;
    /** [column id, relation id] for joins and filters on the column path. */
    references: [string, string][];
}

export const NO_FOCUS: Focus = { relations: null, columns: null, references: [] };

export function computeFocus(model: GraphModel, selection: Selection): Focus {
    if (selection.term) {
        const bound = model.boundTo(selection.term);
        return {
            columns: new Set(bound.filter(id => model.node(id)?.kind === 'column')),
            relations: new Set(bound.map(id => model.viewOf(id)).filter((id): id is string => id !== null)),
            references: [],
        };
    }
    if (!selection.selected) return NO_FOCUS;
    const up = selection.direction !== 'downstream';
    const down = selection.direction !== 'upstream';
    if (selection.column) {
        const start = selection.column;
        const columns = new Set([start]);
        if (up) for (const item of model.columnLineage(start, true)) columns.add(item.id);
        if (down) for (const item of model.columnLineage(start, false)) columns.add(item.id);
        const relations = new Set([selection.selected]);
        const references: [string, string][] = [];
        for (const id of columns) {
            const v = model.viewOf(id);
            if (v) relations.add(v);
            for (const e of model.outs(id, 'feeds')) {
                if (attr(e, 'role') !== 'reference') continue;
                const t = model.viewOf(e.target);
                if (t && (down || id !== start)) {
                    references.push([id, t]);
                    relations.add(t);
                }
            }
        }
        return { relations, columns, references };
    }
    const relations = new Set([selection.selected]);
    if (up) for (const id of model.walk([selection.selected], model.upAdj)) relations.add(id);
    if (down) for (const id of model.walk([selection.selected], model.downAdj)) relations.add(id);
    return { relations, columns: null, references: [] };
}
