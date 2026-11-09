<script lang="ts">
    let { text }: { text: string } = $props();
    let code: HTMLElement | undefined = $state();
    let label = $state('Copy');

    function selectText(): void {
        if (!code) return;
        const range = document.createRange();
        range.selectNodeContents(code);
        const selection = window.getSelection();
        selection?.removeAllRanges();
        selection?.addRange(range);
    }

    function copy(): void {
        const done = (): void => {
            label = 'Copied';
            setTimeout(() => (label = 'Copy'), 1400);
        };
        // file:// pages are not a secure context, so the clipboard API may be missing: select instead.
        if (navigator.clipboard && window.isSecureContext)
            navigator.clipboard.writeText(text).then(done, selectText);
        else selectText();
    }
</script>

<div class="command">
    <code bind:this={code}>{text}</code>
    <button type="button" onclick={copy}>{label}</button>
</div>
