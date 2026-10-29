# Code knowledge graph (Graphify)

[Graphify](https://github.com/Graphify-Labs/graphify) (Apache-2.0) turns this repository's code into a graph of modules,
functions, classes and how they call and import each other. Use it to find your way around and to see the blast
radius of a change before you plan it. It is a developer tool: it never runs in production, and the graph never
leaves your machine.

## Commands

From the repository root (needs [uv](https://docs.astral.sh/uv/)):

```sh
bun run graph                  # build or refresh graphify-out/ (AST only: no LLM, no network once cached)
bun run graph -- --rebuild     # start over (the recovery when graphify-out/ is damaged)
bun run test:graph             # the integration tests on a throwaway repository (scripts/tests/graph.test.mjs)
```

Ask the graph with the same pinned version and the same safe environment:

```sh
G="uv tool run --from graphifyy==0.9.97 graphify"
export GRAPHIFY_NO_AUTO_REFRESH=1 PYTHONHASHSEED=0 PYTHONSAFEPATH=1
$G affected "backend/app/chat/service.py"     # what depends on it (callers, importers): the blast radius
$G path "ChatService" "KnowledgeStore"        # the shortest chain between two parts
$G explain "resolve_assistant_config"         # one node: where it is, what it connects to
$G query "how does a chat turn reach the model?" --budget 1500
```

`graphify-out/GRAPH_REPORT.md` summarises the graph: the most connected nodes ("god nodes"), communities and
surprising links. `graphify-out/graph.html` is an interactive view. When you open it in a browser it loads
vis-network from unpkg (pinned, with SRI).

## When to use it

- **Before planning a change:** run `bun run graph`, then `affected` on every file or function you will
  change. The plan lists what the result shows, and every caller outside the change is either updated or argued
  safe.
- **Security work:** `path` from a router to the database or to a model call shows the route of untrusted input.
  The audit (docs/security) uses it to choose what to read.
- **Reviews:** `affected` on the changed files of a diff shows what to re-test.

## What it does not see

It reads the syntax of Python and TypeScript files (tree-sitter). It does not see:
- FastAPI dependency injection (`Depends(...)`);
- calls made through variables, dynamic imports by string or reflection;
- SQL or anything at run time.

An edge it shows is real. A missing edge proves nothing, so confirm important conclusions in the code or with a
test. Docs, Markdown and images are not in the code graph: `update` reads code only. The `/graphify` skill's
semantic mode, which reads documents through an LLM, is not part of this workflow.

## Boundaries (security review, 2026-10-29)

The pinned version was reviewed before first use: its network, subprocess and file-write paths, and the diff from
0.9.67. The conditions of that review are built into `bun run graph` (`scripts/run.mjs`):

- **Pinned:** `graphifyy==0.9.97` through `uv tool run`, which uses uv's own cache and changes nothing installed.
  Upgrading means a new review of the diff, then a change to the pin.
- **Safe environment:**
  - `GRAPHIFY_NO_AUTO_REFRESH=1`: otherwise graphify rewrites skill files under `~/.claude` on each version
    change.
  - `PYTHONHASHSEED=0` and `PYTHONSAFEPATH=1`: no `python -m` re-launch that would import modules from the
    working directory.
  - An explicit scan root.
- **Never reads secrets:** graphify applies `.gitignore` (the root key files, `.env*`) and the root
  `.graphifyignore`, which lists them again as a second guard and also skips generated files and data.
  `bun run test:graph` checks that an ignored secret never reaches any output.
- **Local, git-ignored data:** `graphify-out/` holds docstrings, some comments and config keys. It is
  git-ignored. Never commit it, never ingest it into the RAG knowledge base or user memory, and never deploy it.
  It is separate from every production data store.
- **Not used:** graphify's git hooks, its Claude Code hooks (`graphify claude install` edits `CLAUDE.md` and adds
  hooks on every tool call), `--watch`, `add <url>`, MCP serving, database or Cargo introspection, and LLM
  extraction.
