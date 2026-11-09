// Search over tables, columns and business terms, ranked by where the words match.

import { attr, type GraphModel } from './model';

export const MAX_RESULTS = 60;

interface Entry {
    id: string;
    text: string;
    extra: string;
    term: boolean;
    names: string[];
}

export interface SearchResult {
    /** The best matches, at most MAX_RESULTS. */
    ids: string[];
    total: number;
    /** Relations to highlight on the canvas. */
    relations: Set<string>;
}

export function buildIndex(model: GraphModel): Entry[] {
    const entries: Entry[] = [];
    for (const n of model.nodes) {
        if (model.inView.has(n.id) || (n.kind === 'column' && model.viewOf(n.id))) {
            entries.push({
                id: n.id,
                text: model.label(n.id).toLowerCase(),
                extra: `${n.relation ?? ''} ${n.description ?? ''}`.toLowerCase(),
                term: false,
                names: [],
            });
        } else if (n.kind === 'term') {
            const synonyms = attr<string[]>(n, 'synonyms') ?? [];
            entries.push({
                id: n.id,
                text: `${n.name} ${synonyms.join(' ')}`.toLowerCase(),
                extra: (n.description ?? '').toLowerCase(),
                term: true,
                names: [n.name, ...synonyms].map(x => x.toLowerCase()),
            });
        }
    }
    return entries;
}

export function search(model: GraphModel, index: Entry[], query: string): SearchResult {
    const q = query.trim().toLowerCase();
    if (!q) return { ids: [], total: 0, relations: new Set() };
    const words = q.split(/\s+/);
    const scored: [number, Entry][] = [];
    for (const entry of index) {
        let score = 0;
        for (const w of words) {
            const at = entry.text.indexOf(w);
            if (at >= 0) score += at === 0 || '. _'.includes(entry.text[at - 1] ?? '') ? 3 : 2;
            else if (entry.extra.includes(w)) score += 1;
            else {
                score = 0;
                break;
            }
        }
        if (!score) continue;
        if (entry.text === q || entry.text.endsWith(`.${q}`) || entry.names.includes(q)) score += 5;
        if (model.inView.has(entry.id)) score += 0.5;
        if (entry.term) score += 1;
        scored.push([score, entry]);
    }
    scored.sort(
        (a, b) => b[0] - a[0] || a[1].text.length - b[1].text.length || a[1].text.localeCompare(b[1].text),
    );
    const relations = new Set<string>();
    for (const [, entry] of scored) {
        const ids = entry.term ? model.boundTo(entry.id) : [entry.id];
        for (const id of ids) {
            const v = model.viewOf(id);
            if (v) relations.add(v);
        }
    }
    return { ids: scored.slice(0, MAX_RESULTS).map(s => s[1].id), total: scored.length, relations };
}
