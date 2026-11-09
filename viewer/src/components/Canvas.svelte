<script lang="ts">
    import { onMount, untrack } from 'svelte';
    import { ICONS } from '../lib/icons';
    import { bounds, curve, HEAD, PAD, ROW, W } from '../lib/layout';
    import { attr, KIND_NAMES } from '../lib/model';
    import { fitText, FONT_MONO, FONT_NAME, FONT_ROW, textWidth } from '../lib/text';
    import type { Point } from '../lib/types';
    import { ANIMATE_LIMIT, type Viewer } from '../lib/viewer.svelte';

    let { viewer, onswitchtheme }: { viewer: Viewer; onswitchtheme: () => void } = $props();
    // The viewer object is created once and never replaced, so reading it here is intended.
    // svelte-ignore state_referenced_locally
    const model = viewer.model;

    let svg: SVGSVGElement | undefined = $state();
    let canvas: HTMLElement | undefined = $state();
    let minimap: HTMLCanvasElement | undefined = $state();
    let texture: SVGSVGElement | undefined = $state();

    // Positions on screen: the layout's target, eased towards it when it changes.
    let displayed = $state.raw(new Map<string, Point>());
    let frame = 0;
    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;

    $effect(() => {
        const target = viewer.target;
        const from = untrack(() => displayed);
        cancelAnimationFrame(frame);
        if (!from.size || target.size > ANIMATE_LIMIT || reducedMotion) {
            displayed = target;
            return;
        }
        const start = performance.now();
        const step = (now: number): void => {
            const t = Math.min(1, (now - start) / 320);
            const k = 1 - Math.pow(1 - t, 3);
            const next = new Map<string, Point>();
            for (const [id, c] of target) {
                const a = from.get(id) ?? c;
                next.set(id, { x: a.x + (c.x - a.x) * k, y: a.y + (c.y - a.y) * k, h: c.h });
            }
            displayed = next;
            if (t < 1) frame = requestAnimationFrame(step);
        };
        frame = requestAnimationFrame(step);
    });

    function at(id: string): Point {
        return displayed.get(id) ?? viewer.target.get(id) ?? { x: 0, y: 0, h: HEAD };
    }

    // Cards and rows

    const cards = $derived(
        Array.from(viewer.target.keys()).map(id => {
            const n = model.node(id)!;
            const all = model.columnsOf(id);
            const { rows, more } = viewer.rowsOf(id);
            return { id, n, all, rows, more };
        }),
    );

    const rowOffsets = $derived.by(() => {
        const index = new Map<string, Map<string, number>>();
        for (const card of cards) {
            index.set(card.id, new Map(card.rows.map((c, i) => [c.id, HEAD + PAD + i * ROW + ROW / 2])));
        }
        return index;
    });

    function typeLabel(dataType: string | undefined): string {
        return dataType ? fitText(dataType.toLowerCase(), FONT_MONO, 92) : '';
    }

    // Edges

    interface DrawnEdge {
        key: string;
        kind: 'feeds' | 'runs' | 'column' | 'reference';
        from: string;
        to: string;
        fromCol: string | null;
        toCol: string | null;
        title?: string;
    }

    const edges = $derived.by(() => {
        const drawn: DrawnEdge[] = [];
        const shown = viewer.target;
        for (const e of model.layoutEdges) {
            if (shown.has(e.source) && shown.has(e.target)) {
                drawn.push({
                    key: `${e.kind}|${e.source}|${e.target}`,
                    kind: e.kind,
                    from: e.source,
                    to: e.target,
                    fromCol: null,
                    toCol: null,
                });
            }
        }
        for (const [id, rows] of rowOffsets) {
            for (const colId of rows.keys()) {
                for (const e of model.outs(colId, 'derived_from')) {
                    const up = model.viewOf(e.target);
                    if (!up || !rowOffsets.get(up)?.has(e.target)) continue;
                    const transform = attr<string>(e, 'transform') ?? 'derived';
                    const verified = attr<string>(e, 'verified');
                    drawn.push({
                        key: `column|${e.target}|${colId}`,
                        kind: 'column',
                        from: up,
                        to: id,
                        fromCol: e.target,
                        toCol: colId,
                        title: `${model.label(e.target)} → ${model.label(colId)} (${transform}${verified ? `, ${verified}` : ''})`,
                    });
                }
            }
        }
        for (const [colId, relation] of viewer.focus.references) {
            const from = model.viewOf(colId);
            if (!from || !rowOffsets.has(from) || !rowOffsets.has(relation) || from === relation) continue;
            drawn.push({
                key: `reference|${colId}|${relation}`,
                kind: 'reference',
                from,
                to: relation,
                fromCol: colId,
                toCol: null,
                title: `${model.label(relation)} joins, filters or groups on ${model.label(colId)}`,
            });
        }
        return drawn;
    });

    function anchor(id: string, col: string | null, side: 'out' | 'in'): { x: number; y: number } {
        const c = at(id);
        const offset = col ? rowOffsets.get(id)?.get(col) : undefined;
        return { x: side === 'out' ? c.x + W : c.x, y: c.y + (offset ?? HEAD / 2) };
    }

    // What is lit

    const matched = $derived(viewer.query.trim() ? viewer.results.relations : null);
    const lit = $derived(viewer.focus.relations ?? matched);

    function edgeState(e: DrawnEdge): { on: boolean; dim: boolean } {
        const focus = viewer.focus;
        let on: boolean;
        if (e.kind === 'column')
            on = !!focus.columns && focus.columns.has(e.fromCol!) && focus.columns.has(e.toCol!);
        else if (e.kind === 'reference') on = true;
        else
            on =
                !!focus.relations &&
                !focus.columns &&
                focus.relations.has(e.from) &&
                focus.relations.has(e.to);
        const keep =
            e.kind !== 'column' && !focus.relations && !!matched && matched.has(e.from) && matched.has(e.to);
        return { on: !!lit && on, dim: !!lit && !on && !keep };
    }

    // Pan and zoom

    let view = $state({ x: 0, y: 0, k: 1 });
    const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v));

    function rect(): DOMRect {
        return svg?.getBoundingClientRect() ?? new DOMRect(0, 0, 1200, 800);
    }

    function zoomAt(px: number, py: number, factor: number): void {
        const k = clamp(view.k * factor, 0.04, 3);
        view = { x: px - ((px - view.x) * k) / view.k, y: py - ((py - view.y) * k) / view.k, k };
    }

    function fitBounds(subset: Map<string, Point>, maxZoom: number): void {
        const b = bounds(subset);
        if (!b) return;
        const r = rect();
        const padX = 60;
        const padTop = 90;
        const padBottom = 60;
        const k = clamp(
            Math.min((r.width - 2 * padX) / b.w, (r.height - padTop - padBottom) / b.h),
            0.04,
            maxZoom,
        );
        view = {
            k,
            x: (r.width - b.w * k) / 2 - b.x * k,
            y: padTop + (r.height - padTop - padBottom - b.h * k) / 2 - b.y * k,
        };
    }

    function centreOn(id: string): void {
        const c = viewer.target.get(id);
        if (!c) return;
        const r = rect();
        const k = view.k < 0.6 ? 0.8 : view.k;
        view = {
            k,
            x: r.width / 2 - (c.x + W / 2) * k,
            y: r.height / 2 - (c.y + Math.min(c.h, 300) / 2) * k,
        };
    }

    $effect(() => {
        const request = viewer.view;
        if (!request) return;
        requestAnimationFrame(() =>
            untrack(() => {
                const target = viewer.target;
                const lit = viewer.focus.relations;
                if (request.kind === 'focus' && lit) {
                    const subset = new Map<string, Point>();
                    for (const id of lit) {
                        const c = target.get(id);
                        if (c) subset.set(id, c);
                    }
                    fitBounds(subset, 1.1);
                } else if (request.kind === 'centre' && request.id) centreOn(request.id);
                else fitBounds(target, 1.25);
            }),
        );
    });

    let drag: { x: number; y: number; vx: number; vy: number; moved: boolean } | null = null;
    let dragging = $state(false);
    let suppressClick = false;

    function onpointerdown(event: PointerEvent): void {
        if (event.button !== 0) return;
        suppressClick = false;
        drag = { x: event.clientX, y: event.clientY, vx: view.x, vy: view.y, moved: false };
    }

    function onpointermove(event: PointerEvent): void {
        if (!drag) return;
        const dx = event.clientX - drag.x;
        const dy = event.clientY - drag.y;
        if (!drag.moved && Math.abs(dx) + Math.abs(dy) > 4) {
            drag.moved = true;
            svg?.setPointerCapture(event.pointerId);
            dragging = true;
        }
        if (drag.moved) view = { ...view, x: drag.vx + dx, y: drag.vy + dy };
    }

    function endDrag(): void {
        if (drag?.moved) suppressClick = true;
        drag = null;
        dragging = false;
    }

    function onwheel(event: WheelEvent): void {
        event.preventDefault();
        const r = rect();
        // Pinch (reported with ctrlKey) and mouse wheels zoom; two-finger trackpad scrolling pans.
        const pinch = event.ctrlKey || event.metaKey;
        const wheel =
            event.deltaMode !== 0 ||
            (event.deltaX === 0 && Number.isInteger(event.deltaY) && Math.abs(event.deltaY) >= 50);
        if (pinch || wheel) {
            const scale = event.deltaMode === 1 ? 0.05 : event.deltaMode === 2 ? 1 : pinch ? 0.01 : 0.0018;
            zoomAt(event.clientX - r.left, event.clientY - r.top, Math.exp(-event.deltaY * scale));
        } else view = { ...view, x: view.x - event.deltaX, y: view.y - event.deltaY };
    }

    function onclick(event: MouseEvent): void {
        if (suppressClick) {
            suppressClick = false;
            return;
        }
        const target = event.target as Element;
        const more = target.closest('[data-more]');
        if (more) {
            viewer.expanded.add(more.getAttribute('data-more')!);
            return;
        }
        const toggle = target.closest('[data-toggle]');
        if (toggle) {
            viewer.toggleColumns(toggle.getAttribute('data-toggle')!);
            return;
        }
        const row = target.closest('[data-col]');
        if (row) {
            const col = row.getAttribute('data-col')!;
            const parent = model.node(col)?.parent;
            if (parent) viewer.select(parent, col === viewer.column ? null : col);
            return;
        }
        const card = target.closest('.node');
        if (card) viewer.select(card.getAttribute('data-id')!);
        else viewer.clear();
    }

    function ondblclick(event: MouseEvent): void {
        const target = event.target as Element;
        const card = target.closest('.node');
        if (card && !target.closest('[data-col]')) viewer.toggleColumns(card.getAttribute('data-id')!);
    }

    function zoomButton(factor: number): void {
        const r = rect();
        zoomAt(r.width / 2, r.height / 2, factor);
    }

    // Minimap

    let miniTransform: { k: number; ox: number; oy: number } | null = null;
    let miniHidden = $state(false);

    $effect(() => {
        const positions = displayed;
        const current = view;
        const lit = viewer.focus.relations;
        void viewer.theme;
        if (!minimap) return;
        const context = minimap.getContext('2d');
        if (!context) return;
        const styles = getComputedStyle(document.documentElement);
        const b = bounds(positions);
        context.clearRect(0, 0, minimap.width, minimap.height);
        miniHidden = !b || positions.size < 6;
        if (!b) return;
        const pad = 16;
        const k = Math.min((minimap.width - 2 * pad) / b.w, (minimap.height - 2 * pad) / b.h);
        const ox = pad + (minimap.width - 2 * pad - b.w * k) / 2 - b.x * k;
        const oy = pad + (minimap.height - 2 * pad - b.h * k) / 2 - b.y * k;
        miniTransform = { k, ox, oy };
        const muted = styles.getPropertyValue('--edge').trim();
        const accent = styles.getPropertyValue('--accent').trim();
        for (const [id, c] of positions) {
            context.fillStyle = lit?.has(id) ? accent : muted;
            context.fillRect(ox + c.x * k, oy + c.y * k, Math.max(2, W * k), Math.max(2, c.h * k));
        }
        const r = rect();
        context.strokeStyle = accent;
        context.lineWidth = 2;
        context.strokeRect(
            ox + (-current.x / current.k) * k,
            oy + (-current.y / current.k) * k,
            (r.width / current.k) * k,
            (r.height / current.k) * k,
        );
    });

    let miniDrag = false;
    function miniPan(event: PointerEvent): void {
        if (!miniTransform || !minimap) return;
        const box = minimap.getBoundingClientRect();
        const mx = ((event.clientX - box.left) / box.width) * minimap.width;
        const my = ((event.clientY - box.top) / box.height) * minimap.height;
        const gx = (mx - miniTransform.ox) / miniTransform.k;
        const gy = (my - miniTransform.oy) / miniTransform.k;
        const r = rect();
        view = { ...view, x: r.width / 2 - gx * view.k, y: r.height / 2 - gy * view.k };
    }

    // Wave lines (DESIGN.md section 8): fine vertical lines, calmer towards the left.

    let wave = $state<{ d: string; opacity: string }[]>([]);
    let waveBox = $state('0 0 1200 800');

    function drawTexture(): void {
        const box = texture?.getBoundingClientRect();
        const width = box?.width || 1200;
        const height = box?.height || 800;
        waveBox = `0 0 ${width} ${height}`;
        let seed = 1729;
        const rand = (): number => (seed = (seed * 16807) % 2147483647) / 2147483647;
        const lines = 22;
        const paths: { d: string; opacity: string }[] = [];
        for (let i = 0; i < lines; i++) {
            const base = width * (0.7 + (0.32 * i) / lines);
            const amp = 18 + rand() * 40;
            const freq = 0.0025 + rand() * 0.004;
            const phase = rand() * Math.PI * 2;
            let d = '';
            for (let y = 0; y <= height; y += 16) {
                const x =
                    base +
                    Math.sin(y * freq + phase) * amp +
                    Math.sin(y * freq * 2.7 + phase * 1.3) * amp * 0.35;
                d += `${y ? ' L' : 'M'}${x.toFixed(1)},${y}`;
            }
            paths.push({ d, opacity: (0.35 + (0.65 * i) / lines).toFixed(2) });
        }
        wave = paths;
    }

    onMount(() => {
        drawTexture();
        const observer = new ResizeObserver(() => drawTexture());
        if (canvas) observer.observe(canvas);
        return () => observer.disconnect();
    });

    const empty = $derived(
        viewer.target.size
            ? ''
            : model.viewNodes.length
              ? 'Nothing to show with these filters.'
              : 'No tables or models in this graph.',
    );
</script>

<main id="canvas" aria-label="Graph" bind:this={canvas}>
    <svg id="texture" aria-hidden="true" viewBox={waveBox} bind:this={texture}>
        {#each wave as line, i (i)}<path d={line.d} opacity={line.opacity} />{/each}
    </svg>
    <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
    <svg
        id="graph"
        role="img"
        aria-label="Lineage graph"
        class:dragging
        bind:this={svg}
        {onpointerdown}
        {onpointermove}
        onpointerup={endDrag}
        onpointercancel={endDrag}
        {onwheel}
        {onclick}
        {ondblclick}
    >
        <defs>
            <marker
                id="arrow"
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="6"
                markerHeight="6"
                orient="auto"><path d="M0,1 L9,5 L0,9 z" /></marker
            >
            <marker
                id="arrow-hl"
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="6"
                markerHeight="6"
                orient="auto"><path d="M0,1 L9,5 L0,9 z" /></marker
            >
        </defs>
        <g id="viewport" transform="translate({view.x},{view.y}) scale({view.k})">
            <g id="edges">
                {#each edges as e (e.key)}
                    {@const state = edgeState(e)}
                    <path
                        class="edge {e.kind}"
                        class:hl={state.on}
                        class:dim={state.dim}
                        d={curve(anchor(e.from, e.fromCol, 'out'), anchor(e.to, e.toCol, 'in'))}
                        marker-end={e.kind === 'feeds' || e.kind === 'runs'
                            ? state.on
                                ? 'url(#arrow-hl)'
                                : 'url(#arrow)'
                            : undefined}
                        >{#if e.title}<title>{e.title}</title>{/if}</path
                    >
                {/each}
            </g>
            <g id="nodes">
                {#each cards as card (card.id)}
                    {@const p = at(card.id)}
                    {@const open = card.rows.length > 0}
                    <g
                        class="node k-{card.n.kind}"
                        class:selected={card.id === viewer.selected && !viewer.column && !viewer.term}
                        class:on-path={!!viewer.focus.relations?.has(card.id)}
                        class:match={!viewer.focus.relations && !!matched?.has(card.id)}
                        class:dim={!!lit && !lit.has(card.id)}
                        data-id={card.id}
                        transform="translate({p.x},{p.y})"
                    >
                        <title
                            >{KIND_NAMES[card.n.kind] ?? card.n.kind}: {card.n.relation ??
                                model.label(card.id)}</title
                        >
                        <rect class="card" width={W} height={p.h} rx="12" />
                        <g class="icon" transform="translate(14,{HEAD / 2 - 10}) scale(0.8333)">
                            <path d={ICONS[card.n.kind] ?? ''} />
                        </g>
                        <text class="name" x="44" y={HEAD / 2}
                            >{fitText(card.n.name, FONT_NAME, W - 46 - (card.all.length ? 66 : 14))}</text
                        >
                        {#if card.all.length}
                            <g class="toggle" data-toggle={card.id}>
                                <rect x={W - 64} y="8" width="56" height={HEAD - 16} rx="10" />
                                <title>{open ? 'Hide columns' : `Show ${card.all.length} columns`}</title>
                                <text class="count" x={W - 32} y={HEAD / 2}>{card.all.length}</text>
                                <path
                                    class="chev"
                                    d={open
                                        ? `M${W - 26},${HEAD / 2 + 3} l5,-5 l5,5`
                                        : `M${W - 26},${HEAD / 2 - 2} l5,5 l5,-5`}
                                />
                            </g>
                        {/if}
                        {#if card.rows.length || card.more}
                            <line class="divider" x1="0" x2={W} y1={HEAD} y2={HEAD} />
                            {#each card.rows as col, i (col.id)}
                                {@const type = typeLabel(col.data_type)}
                                <g
                                    class="row"
                                    class:selected={col.id === viewer.column}
                                    class:on-path={!!viewer.focus.columns?.has(col.id)}
                                    data-col={col.id}
                                    transform="translate(0,{HEAD + PAD + i * ROW})"
                                >
                                    <rect x="4" y="1" width={W - 8} height={ROW - 2} rx="8" />
                                    <circle class="dot" cx="16" cy={ROW / 2} r="3" />
                                    <text class="col" x="28" y={ROW / 2}
                                        >{fitText(
                                            col.name,
                                            FONT_ROW,
                                            W - 44 - (type ? textWidth(type, FONT_MONO) : 0) - 14,
                                        )}</text
                                    >
                                    {#if type}<text class="type" x={W - 14} y={ROW / 2}>{type}</text>{/if}
                                </g>
                            {/each}
                            {#if card.more}
                                <g
                                    class="row more"
                                    data-more={card.id}
                                    transform="translate(0,{HEAD + PAD + card.rows.length * ROW})"
                                >
                                    <rect x="4" y="1" width={W - 8} height={ROW - 2} rx="8" />
                                    <text class="col" x="28" y={ROW / 2}
                                        >Show {card.more} more column{card.more === 1 ? '' : 's'}</text
                                    >
                                </g>
                            {/if}
                        {/if}
                    </g>
                {/each}
            </g>
        </g>
    </svg>
    <div id="empty">{empty}</div>
    <div id="toolbar">
        <div class="group" role="group" aria-label="Direction">
            {#each [['upstream', 'Upstream', 'Show what the selection comes from'], ['both', 'Both', 'Show both directions'], ['downstream', 'Downstream', 'Show what depends on the selection']] as const as [value, text, title] (value)}
                <button
                    type="button"
                    data-direction={value}
                    aria-pressed={viewer.direction === value}
                    {title}
                    onclick={() => (viewer.direction = value)}>{text}</button
                >
            {/each}
        </div>
        <div class="group" role="group" aria-label="View">
            <button
                type="button"
                id="isolate"
                aria-pressed={viewer.isolate}
                title="Show only the lineage of the selection (I)"
                onclick={() => viewer.toggleIsolate()}>Isolate</button
            >
            <button
                type="button"
                id="columns"
                aria-pressed={viewer.allColumns}
                title="Show every column (C)"
                onclick={() => viewer.toggleAllColumns()}>Columns</button
            >
        </div>
        <div class="group" role="group" aria-label="Zoom">
            <button
                type="button"
                class="icon"
                id="zoom-out"
                title="Zoom out (-)"
                aria-label="Zoom out"
                onclick={() => zoomButton(0.8)}
                ><svg
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.8"
                    stroke-linecap="round"><path d="M5 12h14" /></svg
                ></button
            >
            <button
                type="button"
                class="icon"
                id="zoom-in"
                title="Zoom in (+)"
                aria-label="Zoom in"
                onclick={() => zoomButton(1.25)}
                ><svg
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.8"
                    stroke-linecap="round"><path d="M5 12h14M12 5v14" /></svg
                ></button
            >
            <button
                type="button"
                id="fit"
                title="Fit the graph to the window (F)"
                onclick={() => viewer.request('fit')}>Fit</button
            >
            <button
                type="button"
                class="icon"
                id="theme"
                title="Switch between dark and light"
                aria-label="Switch theme"
                onclick={onswitchtheme}
                ><svg
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.8"
                    stroke-linecap="round"
                    stroke-linejoin="round"><path d="M12 3a9 9 0 1 0 9 9 7 7 0 0 1-9-9z" /></svg
                ></button
            >
        </div>
    </div>
    <canvas
        id="minimap"
        width="440"
        height="280"
        aria-label="Overview; click to move there"
        hidden={miniHidden}
        bind:this={minimap}
        onpointerdown={event => {
            miniDrag = true;
            minimap?.setPointerCapture(event.pointerId);
            miniPan(event);
        }}
        onpointermove={event => {
            if (miniDrag) miniPan(event);
        }}
        onpointerup={() => (miniDrag = false)}
    ></canvas>
    <div id="hint">
        <kbd>/</kbd> search &nbsp; <kbd>F</kbd> fit &nbsp; <kbd>I</kbd> isolate &nbsp; <kbd>Esc</kbd> clear
    </div>
</main>
