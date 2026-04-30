# Documentation build notes & assumptions

## What's in this folder

```
docs-site/
├── docs/                    # Markdown source — Docusaurus & MkDocs both read this.
│   ├── intro.md
│   ├── installation.md
│   ├── quickstart.md
│   ├── concepts.md
│   ├── usage/
│   │   ├── basic.md
│   │   ├── advanced.md
│   │   ├── configuration.md
│   │   └── customization.md
│   ├── scenarios/
│   │   ├── index.md
│   │   ├── simple-suite.md
│   │   ├── large-suites.md
│   │   ├── edge-cases.md
│   │   ├── ci-cd.md
│   │   ├── openai-adapter.md
│   │   ├── performance.md
│   │   ├── debugging.md
│   │   └── failure-handling.md
│   ├── api/
│   │   ├── python.md
│   │   ├── cli.md
│   │   ├── graders.md
│   │   └── adapters.md
│   ├── architecture.md
│   ├── best-practices.md
│   ├── troubleshooting.md
│   ├── faq.md
│   ├── contributing.md
│   └── roadmap.md
├── mkdocs.yml               # MkDocs Material site config (+ nav).
├── docusaurus.config.js     # Docusaurus site config.
├── sidebars.js              # Docusaurus sidebar definition.
└── NOTES.md                 # This file.
```

## How to build

### MkDocs Material

```bash
pip install mkdocs-material mkdocs-material-extensions
cd docs-site
mkdocs serve     # http://127.0.0.1:8000/
mkdocs build     # static site in site/
```

### Docusaurus

The Markdown files are Docusaurus-compatible (front-matter + standard
Markdown + Mermaid). To bootstrap a Docusaurus site around them:

```bash
npx create-docusaurus@latest agentprdiff-docs classic
cd agentprdiff-docs
# replace the generated docs/ folder with this docs-site/docs/
# replace docusaurus.config.js with docs-site/docusaurus.config.js
# replace sidebars.js with docs-site/sidebars.js
npm install @docusaurus/theme-mermaid prism-react-renderer
npm run start
```

You'll need to keep an `src/css/custom.css` file (Docusaurus default
generates one) and an `img/` folder with `logo.svg` and `favicon.ico`.

## Key assumptions made while writing

1. **Project name interpretation.** The repo is `agentprdiff`, the
   tagline is "Snapshot tests that catch behavioral regressions when
   models, prompts, or vendors change." Despite the prompt's mention of
   "PR diff analysis," the actual library is *not* a tool for parsing
   GitHub PR diffs. I documented the project as it is — a snapshot-test
   harness for LLM agents — and rewrote the user prompt's "Simple PR
   diff analysis / Large PR handling / Multi-file changes" scenarios
   into the analogous concepts that *do* apply (one-suite case, large
   multi-file suites, multi-suite tests). Where the prompt's request
   (e.g. CI/CD integration, edge cases, performance, debugging,
   failure handling) maps cleanly onto the actual product, I covered it
   directly.
2. **Version 0.2.3.** Pulled from `pyproject.toml` and
   `src/agentprdiff/__init__.py`.
3. **Python 3.10+** is the minimum supported version (per
   `requires-python`).
4. **Bundled price table is current as of 2026-04** per the comment in
   `src/agentprdiff/adapters/pricing.py`.
5. **Async Anthropic and LangChain adapters are *roadmap* items**, per
   the README's "Status" section. Documented as such.
6. **The `examples/quickstart/` directory is the canonical 'Scenario 1'**
   end-to-end example. Code in the doc was lifted from
   `examples/quickstart/agent.py` and `suite.py` for fidelity.
7. **CLI reference covers all six subcommands** — `init`, `record`,
   `check`, `review`, `scaffold`, `diff` — by reading `src/agentprdiff/cli.py`
   directly. Default values, exit codes, and side effects are taken from
   the source rather than the README.
8. **The semantic-judge fallback rules** match the implementation in
   `src/agentprdiff/graders/semantic.py` (`_default_judge` and
   `describe_default_judge`). The "yellow banner" detail in the
   reporters chapter matches `_maybe_print_judge_banner` in
   `src/agentprdiff/reporters.py`.
9. **The architecture diagrams** are Mermaid because both MkDocs Material
   (via `pymdownx.superfences`) and Docusaurus (via `@docusaurus/theme-mermaid`)
   render them natively. No external image files are required.
10. **External links** point at the public GitHub repo
    (`vnageshwaran-de/agentprdiff`). PyPI links point at the published
    `agentprdiff` package.

## What's *not* documented (deliberately)

- Internal `_underscored` helpers in `src/agentprdiff/`. Documented only
  the public, importable API.
- The `examples/regression-tour/` directory. It exists in the repo but is
  redundant with the simpler `examples/quickstart/` for documentation
  purposes. Adopters can find it themselves.
- `docs/launch-post.md`, `docs/ai-driven-adoption.md`, `docs/adapters-vercel.md`
  in the repo. These are project-development artifacts (a launch
  announcement, an AI-driven-adoption playbook, and a forward-looking
  Vercel adapter design doc). They live in the repo for the maintainer
  but aren't end-user docs.
- `AGENTS.md` is referenced from the README but I haven't seen its
  contents in the working tree. The MkDocs/Docusaurus site links to it
  on GitHub directly when relevant.

## Suggestions for follow-up work

- Add `docs-site/static/img/` with a logo + favicon for Docusaurus.
- Add a `versioning` strategy (mike for MkDocs, Docusaurus's built-in
  versioning) once 0.3 ships.
- Add a `getting-started/install-on-a-monorepo.md` once that pattern
  has a couple of real adopters.
- Consider an Algolia DocSearch integration once the site is published.
