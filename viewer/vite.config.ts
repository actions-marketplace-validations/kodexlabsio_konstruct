import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vitest/config";
import { viteSingleFile } from "vite-plugin-singlefile";

// One self-contained HTML file: script, styles and fonts inlined, nothing loaded from the network.
// It is written into the Python package, where konstruct fills the data placeholders.
export default defineConfig({
    plugins: [svelte(), viteSingleFile({ removeViteModuleLoader: true })],
    build: {
        outDir: "../konstruct/viewer",
        emptyOutDir: false,
        assetsInlineLimit: Number.MAX_SAFE_INTEGER,
        cssCodeSplit: false,
        target: "es2022",
        reportCompressedSize: false,
    },
    test: {
        include: ["tests/**/*.test.ts"],
        environment: "node",
    },
});
