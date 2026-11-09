<script lang="ts">
    import { attr, display, KIND_NAMES, VIEW_KINDS } from '../lib/model';
    import type { Viewer } from '../lib/viewer.svelte';
    import Icon from './Icon.svelte';

    let { viewer, search = $bindable() }: { viewer: Viewer; search?: HTMLInputElement } = $props();

    // The viewer object is created once and never replaced, so reading it here is intended.
    // svelte-ignore state_referenced_locally
    const model = viewer.model;
    // svelte-ignore state_referenced_locally
    const meta = viewer.data.meta ?? {};
    // svelte-ignore state_referenced_locally
    const warnings = viewer.data.warnings ?? [];
    const project =
        display(meta.root)
            .replace(/[\\/]+$/, '')
            .split(/[\\/]/)
            .pop() || 'Graph';

    const counts = new Map<string, number>();
    for (const n of model.nodes) counts.set(n.kind, (counts.get(n.kind) ?? 0) + 1);
    const relations = model.viewNodes.filter(n => n.kind !== 'task').length;
    const columnEdges = model.edges.filter(e => e.kind === 'derived_from');
    const verified = columnEdges.filter(e => attr(e, 'verified') === 'confirmed').length;
    const stats: [number | string, string][] = [
        [relations, relations === 1 ? 'table or model' : 'tables and models'],
        [counts.get('column') ?? 0, 'columns'],
        [columnEdges.length, 'column edges'],
        verified
            ? [`${Math.round((100 * verified) / columnEdges.length)}%`, 'verified on data']
            : [counts.get('task') ?? 0, 'tasks'],
    ];
    if (counts.get('term')) {
        const approved = model.nodes.filter(
            n => n.kind === 'term' && attr(n, 'status') === 'approved',
        ).length;
        stats.push([counts.get('term') ?? 0, 'business terms'], [approved, 'approved']);
    }
    const version = meta.konstruct_version;
    const built = [
        meta.generated_at ? `Built ${display(meta.generated_at).slice(0, 16).replace('T', ' ')}` : '',
        version ? `Konstruct ${display(version)}` : '',
        meta.dialect ? display(meta.dialect) : '',
    ]
        .filter(Boolean)
        .join(' · ');

    const present = new Map<string, number>();
    for (const n of model.viewNodes) present.set(n.kind, (present.get(n.kind) ?? 0) + 1);
    const kinds = VIEW_KINDS.filter(k => present.has(k));

    let active = $state(0);
    let list: HTMLUListElement | undefined = $state();
    $effect(() => {
        void viewer.query;
        active = 0;
    });

    const counter = $derived(
        viewer.notice ||
            (viewer.query.trim()
                ? viewer.results.total
                    ? `${viewer.results.total} ${viewer.results.total === 1 ? 'match' : 'matches'}`
                    : 'No matches'
                : ''),
    );

    function open(id: string): void {
        const n = model.node(id);
        if (!n) return;
        if (n.kind === 'term') viewer.selectTerm(id);
        else if (n.kind === 'column' && n.parent) viewer.select(n.parent, id, true);
        else viewer.select(id, null, true);
    }

    function move(delta: number): void {
        const count = viewer.results.ids.length;
        if (!count) return;
        active = (active + delta + count) % count;
        list?.children[active]?.scrollIntoView({ block: 'nearest' });
    }

    function onkeydown(event: KeyboardEvent): void {
        const ids = viewer.results.ids;
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            move(1);
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            move(-1);
        } else if (event.key === 'Enter' && ids.length) open(ids[active]!);
        else if (event.key === 'Escape') {
            viewer.query = '';
            search?.blur();
        }
    }

    function iconFor(id: string): string {
        const n = model.node(id);
        if (!n) return '';
        return n.kind === 'column' ? (model.node(n.parent ?? '')?.kind ?? 'column') : n.kind;
    }

    function kindText(id: string): string {
        const kind = model.node(id)?.kind ?? '';
        return kind === 'column' || kind === 'term' ? kind : (KIND_NAMES[kind] ?? kind);
    }

    const legend: [string, string][] = [
        ['Data flows into', 'edge'],
        ['Runs', 'edge runs'],
        ['Column lineage', 'edge hl'],
        ['Joins or filters on', 'edge reference'],
    ];
</script>

<aside id="sidebar" aria-label="Project">
    <p class="kicker"><b>&#x276F;</b> Konstruct</p>
    <p class="eyebrow">Lineage graph</p>
    <h1 id="project">{project}</h1>
    <div id="stats">
        {#each stats as [value, text] (text)}
            <div class="stat">
                <b>{typeof value === 'number' ? value.toLocaleString('en-GB') : value}</b><span>{text}</span>
            </div>
        {/each}
    </div>
    <p id="built">{built}</p>
    <div class="search">
        <label for="search" class="eyebrow" hidden>Search</label>
        <input
            id="search"
            type="search"
            placeholder="Search tables and columns"
            autocomplete="off"
            spellcheck="false"
            aria-controls="results"
            bind:this={search}
            bind:value={viewer.query}
            {onkeydown}
        />
        <kbd aria-hidden="true">/</kbd>
    </div>
    <div id="match-count" aria-live="polite">{counter}</div>
    <ul id="results" role="listbox" aria-label="Search results" bind:this={list}>
        {#each viewer.results.ids as id, i (id)}
            <li role="option" aria-selected={i === active} class:active={i === active}>
                <button type="button" onclick={() => open(id)}>
                    <Icon kind={iconFor(id)} />
                    <span class="label">{model.label(id)}</span>
                    <span class="kind">{kindText(id)}</span>
                </button>
            </li>
        {/each}
    </ul>
    <div class="section-title"><span>Show</span></div>
    <div id="filters">
        {#each kinds as kind (kind)}
            <button
                type="button"
                class="chip"
                data-kind={kind}
                aria-pressed={!viewer.hidden.has(kind)}
                onclick={() => viewer.toggleKind(kind)}
            >
                <Icon {kind} />{KIND_NAMES[kind]}s<b>{present.get(kind)}</b>
            </button>
        {/each}
    </div>
    {#if warnings.length}
        <details id="warnings">
            <summary
                >{warnings.length} {warnings.length === 1 ? 'warning' : 'warnings'} from the build</summary
            >
            <ul>
                {#each warnings as warning, i (i)}<li>{warning}</li>{/each}
            </ul>
        </details>
    {/if}
    <div id="legend" aria-label="Legend">
        {#each legend as [text, cls] (text)}
            <div>
                <svg class="line" viewBox="0 0 28 10" aria-hidden="true"
                    ><path class={cls} d="M1,5 L27,5" /></svg
                >
                {text}
            </div>
        {/each}
    </div>
</aside>
