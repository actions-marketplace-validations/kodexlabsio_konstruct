<script lang="ts">
    import { onMount } from 'svelte';
    import Canvas from './components/Canvas.svelte';
    import Panel from './components/Panel.svelte';
    import Sidebar from './components/Sidebar.svelte';
    import type { Viewer } from './lib/viewer.svelte';

    let { viewer }: { viewer: Viewer } = $props();

    function stored(key: string, value?: string): string | null {
        try {
            if (value === undefined) return window.localStorage.getItem(key);
            window.localStorage.setItem(key, value);
        } catch {
            return null;
        }
        return null;
    }

    const prefersLight = window.matchMedia?.('(prefers-color-scheme: light)').matches;
    // svelte-ignore state_referenced_locally
    viewer.theme =
        (stored('konstruct-theme') as 'dark' | 'light' | null) ?? (prefersLight ? 'light' : 'dark');

    $effect(() => {
        document.documentElement.setAttribute('data-theme', viewer.theme);
    });

    export function switchTheme(): void {
        viewer.theme = viewer.theme === 'dark' ? 'light' : 'dark';
        stored('konstruct-theme', viewer.theme);
    }

    // Deep links: #<node id>. A link in the address when the page opens is read first, before the
    // effect below keeps the address in step with the selection (and would otherwise clear it).
    // svelte-ignore state_referenced_locally
    const openedFromLink = location.hash ? viewer.open(decodeURIComponent(location.hash.slice(1))) : false;
    let writing = false;
    $effect(() => {
        const next = viewer.hash;
        if (location.hash === next) return;
        writing = true;
        history.replaceState(null, '', next || location.pathname + location.search);
        writing = false;
    });

    function readHash(): boolean {
        if (writing || !location.hash) return false;
        return viewer.open(decodeURIComponent(location.hash.slice(1)));
    }

    let search: HTMLInputElement | undefined = $state();

    onMount(() => {
        const name =
            (viewer.data.meta?.root ?? '')
                .replace(/[\\/]+$/, '')
                .split(/[\\/]/)
                .pop() || 'Graph';
        document.title = `${name} · Konstruct`;
        if (!openedFromLink) viewer.request('fit');
    });

    function onkeydown(event: KeyboardEvent): void {
        if (event.target instanceof HTMLInputElement || event.metaKey || event.ctrlKey || event.altKey)
            return;
        const key = event.key;
        if (key === '/') {
            event.preventDefault();
            search?.focus();
            search?.select();
        } else if (key === 'Escape') viewer.clear();
        else if (key === 'f' || key === 'F') viewer.request('fit');
        else if (key === 'i' || key === 'I') viewer.toggleIsolate();
        else if (key === 'c' || key === 'C') viewer.toggleAllColumns();
        else if (key === '+' || key === '=') document.getElementById('zoom-in')?.click();
        else if (key === '-') document.getElementById('zoom-out')?.click();
    }
</script>

<svelte:window {onkeydown} onhashchange={readHash} />

<a class="skip" href="#search">Skip to search</a>
<Sidebar {viewer} bind:search />
<Canvas {viewer} onswitchtheme={switchTheme} />
<Panel {viewer} />
