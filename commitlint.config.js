const AI_TOOL =
    /\b(claude|anthropic|copilot|openai|chatgpt|gpt-?[\d.]+o?|codex|gemini|cursor|windsurf|codeium|devin|aider|codewhisperer|tabnine|cline|deepseek|grok|qwen|opencode|amazon q)\b/i;

/** Trailers and footer lines that AI coding agents add to attribute themselves */
function isAiAttribution(line) {
    const trailer = line.match(/^([A-Za-z][\w-]*):\s*(.+)$/);
    if (trailer) {
        const [, key, value] = trailer;
        return (
            /^(ai-)?(assisted|generated)-(by|with)$|^made-with$/i.test(key) ||
            AI_TOOL.test(key) ||
            (/^co-(authored|developed)-by$/i.test(key) && AI_TOOL.test(value))
        );
    }
    return (
        line.includes('🤖') ||
        (/\b(generated|created|written|assisted)\s+(with|by|using)\b/i.test(line) && AI_TOOL.test(line))
    );
}

module.exports = {
    extends: ['@commitlint/config-conventional'],
    plugins: [
        {
            rules: {
                'no-ai-attribution': ({ raw }) => {
                    // `git commit -v` appends the diff below a scissors line
                    const found = (raw ?? '')
                        .split(/^# -+ >8 -+$/m)[0]
                        .split('\n')
                        .map(line => line.trim())
                        .filter(line => line && !line.startsWith('#') && isAiAttribution(line));
                    return [
                        found.length === 0,
                        `remove AI attribution from the commit message: ${found.join(' | ')}`,
                    ];
                },
            },
        },
    ],
    rules: {
        'no-ai-attribution': [2, 'always'],
        'scope-case': [2, 'always', 'lower-case'],
        'subject-case': [2, 'always', ['sentence-case']],
        'scope-enum': [
            2,
            'always',
            [
                // Inputs
                'airflow',
                'dbt',
                'iceberg',
                'sql',
                'warehouse',
                // Graph and outputs
                'build',
                'graph',
                'html',
                'lineage',
                'query',
                'report',
                // Interfaces
                'cli',
                'mcp',
                'skill',
                // Project
                'examples',
                'readme',
                'tests',
                // Tooling
                'ci',
                'config',
                'deps',
                'deps-dev',
                'release',
            ],
        ],
    },
};
