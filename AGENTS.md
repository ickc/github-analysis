# Repository notes for future agents

- Use `uv` for Python environment, packaging, and task execution in this
  repository.
- Prefer commands of the form:

```bash
uv run python ...
uv run github-analysis ...
```

- Do not run bare `python` for project tasks unless the user explicitly asks for
  it.
- Keep this package organization-agnostic. Organization names, private data
  paths, billing assumptions, and one-off repository quirks should live in a
  consuming repository's config, not in this library.
