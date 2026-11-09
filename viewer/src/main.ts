import { mount } from 'svelte';
import './app.css';
import App from './App.svelte';
import type { Explanations, GraphData } from './lib/types';
import { Viewer } from './lib/viewer.svelte';

function embedded<T>(id: string): T | null {
    const text = document.getElementById(id)?.textContent ?? '';
    if (!text.trim() || text.startsWith('__KONSTRUCT_')) return null;
    return JSON.parse(text) as T;
}

/** In `pnpm dev` the placeholders are still in the page: read dev/graph.json instead. */
async function load(): Promise<[GraphData, Explanations]> {
    const graph = embedded<GraphData>('konstruct-data');
    if (graph) return [graph, embedded<Explanations>('konstruct-explain') ?? {}];
    // Only `pnpm dev` reads a file; the production build never makes a request.
    if (import.meta.env.DEV) {
        const response = await fetch('/dev/graph.json');
        return [response.ok ? ((await response.json()) as GraphData) : {}, {}];
    }
    return [{}, {}];
}

async function start(): Promise<void> {
    const [graph, explanations] = await load();
    // Names are fitted to cards with the real fonts, so wait for them.
    await document.fonts?.ready.catch(() => undefined);
    mount(App, { target: document.body, props: { viewer: new Viewer(graph, explanations) } });
}

void start();
