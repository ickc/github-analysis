# Repository notes for future agents

- Use `pixi` for all Python environment and task execution in this repository.
- If `pixi` is unavailable, install it with:
  - `curl -fsSL https://pixi.sh/install.sh | sh`
- Prefer commands of the form:

```bash
pixi run --manifest-path /path/to/github-analysis/pyproject.toml python ...
```

- Do not run bare `python` for project tasks unless the user explicitly asks for
  it.
- Keep this package organization-agnostic. Organization names, private data
  paths, billing assumptions, and one-off repository quirks should live in a
  consuming repository's config, not in this library.
