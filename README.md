# agent-toolkit

Modular frameworks for AI agent memory, workflow, and coordination.

An agent's useful work should survive more than a single context window. This
repository collects small, inspectable frameworks for the operational parts of
agentic work: retaining state, managing handoffs, recording corrections, and
keeping concurrent tasks separate.

Each framework has its own human-readable guide, agent-facing setup instructions,
and implementation. Adopt the components you need; the repository is not a new
model, hosted service, or all-in-one agent runtime.

## Frameworks

| Framework | What it does | Start here |
| --- | --- | --- |
| **Diary** | Persistent, task-scoped working memory with recursive child journals, explicit lifecycle transitions, and MCP-backed recovery. | [Read the guide](diary/README.md) · [Set it up](diary/SETUP_PROMPT.md) |

### Diary: continuity you can inspect

Diary records the exact request, an ordered plan, meaningful progress, corrections,
and the current work state outside the model's context window. After a context
reset, the agent can recover the active work without treating a compressed summary
as the complete history.

It is a notebook for an agent's future self: when a long conversation is compressed,
the work state remains available outside the shortened summary. Start with the
[block structure and lifecycle](diary/README.md#six-fields-that-answer-the-next-agents-questions)
to see what gets recorded and how corrections and resumed work stay connected.

![Diary stores independent root, child, and grandchild journals beneath the Diaries directory. Shared MCP code is separate from task records.](diary/assets/diary-tree.svg)

## Repository map

```text
agent-toolkit/
├── README.md
└── diary/
    ├── README.md                Human guide: design, evidence, limitations
    ├── SETUP_PROMPT.md          Agent-facing installation and behavior contract
    ├── assets/                 Architecture diagrams
    ├── benchmarks/             Reproducible experiments and measured raw data
    └── mcps/continuity-journal/
        ├── scripts/            MCP server, routing, persistence, configuration
        ├── skills/             Reusable operating instructions
        ├── references/         Complete continuity protocol
        └── tests/              Isolated regression and real MCP transport tests
```

## Start with an agent

Clone this repository, then give your agent the following request:

```text
Read diary/SETUP_PROMPT.md completely. Use the bundled
diary/mcps/continuity-journal package to set up Diary in this environment.
Preserve any existing records, verify the installation, and report anything
that requires my input. Do not treat reading the prompt as installation.
```

The setup prompt, operating skill, and MCP server have different jobs. The prompt
establishes the installation and durable rules; the skill tells the agent how to
operate the framework; the server performs the scoped reads and atomic writes.

## Public code, private records

This repository distributes implementation, documentation, and synthetic tests.
It does **not** contain live Diaries, user prompts, checkpoints, credentials, or
machine-specific runtime configuration. Keep those outside your checkout.

The MCP server performs no network requests, but its results are returned to the
calling agent. If that agent is cloud-hosted, the returned content enters that
provider's context. Local storage is not a promise of end-to-end offline operation.

## Development

See the [performance experiments](diary/benchmarks/README.md) for measured MCP
bookkeeping costs and the limits of what those measurements establish.

See the [Diary runtime guide](diary/mcps/continuity-journal/README.md) for the tested
environment, installation, and regression suite. Keep behavior changes covered by
isolated tests and keep private runtime data out of commits.

## License

[MIT](LICENSE) · Copyright 2026 v2lixx.
