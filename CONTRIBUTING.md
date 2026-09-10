# Contributing

## Commit messages

English, [Conventional Commits](https://www.conventionalcommits.org/), one
subject line of the shape `type(scope): subject`.

- imperative mood — `add`, `fix`, `move`, not `added` or `adds`;
- lower case after the colon, no full stop at the end, subject at most 72
  characters;
- a body, wrapped at 80 columns, whenever the change had a reason. Keep the
  numbers and keep the reasoning: a message here records why a decision was
  taken and what it cost, and that is the only place some of it is written down;
- `BREAKING CHANGE:` as a paragraph in the body when behaviour changed on the
  outside — a renamed directory the tool creates, a field that disappeared from
  an answer, a record that now holds something else.

Types:

| type | for |
|---|---|
| `feat` | new behaviour |
| `fix` | a defect fixed |
| `docs` | pages, the README, the skill |
| `test` | checks and their negative controls |
| `refactor` | the same behaviour, a different shape |
| `perf` | measured speed or size |
| `build` | packaging, dependencies, the image |
| `ci` | workflows and the gate |
| `chore` | everything else that keeps the tree working |

Scopes in use in this tree:

| scope | what it covers |
|---|---|
| `brain` | the trace-reading rules in flang, `ouroboros/brain/` |
| `languages` | the per-language backends, `ouroboros/languages/` |
| `clangtools` | the clangd and clang-tidy tools |
| `treeflags` | compile-flag discovery for a tree |
| `trace` | writing and reading the records |
| `sandbox` | the project layout, `draft` and `clean` |
| `mcp` | the MCP server |
| `guards` | the checks under `scripts/` |
| `scripts` | the rest of the tooling under `scripts/` |
| `docs` | the documentation pages and what generates them |
| `site` | the landing page and the site build |
| `packaging` | the Homebrew formula, the asdf plugin, the image |
| `bench` | the benchmark harness under `bench/` |
| `research` | the trace-help experiment under `scripts/measure/` |
| `release` | a version bump |
| `ci` | the workflows |

A single documentation page may stand as its own scope when the change is only
about it — `docs(install)`, `docs(measurements)`, `docs(why)`, `docs(skill)`.
Leave the scope out when the change runs across the tree.

Nothing enforces this. There is no commit-message hook and no step in
`scripts/qa.sh` that reads the log; the rule holds by agreement. The whole
history was rewritten into this form in one pass — the original Russian messages
are kept, unchanged, on the branch `archive/history-in-russian`, which also
carries the `v0.6.0` tag.

## Code

The gate is `scripts/qa.sh`, and it must be green before a change is done. What
it checks and why is written in the script itself, step by step.
