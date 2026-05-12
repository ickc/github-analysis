# Repository notes for future agents

- Use `uv` for PyPI packaging and release checks in this repository.
- The repository also defines a `pixi` environment in `pyproject.toml`; keep
  package runtime dependencies mirrored in `[project.dependencies]` and
  `[tool.pixi.dependencies]` so uv resolves from PyPI while pixi resolves from
  conda-forge.
- Prefer commands of the form:

```bash
uv run python ...
uv run github-analysis ...
pixi run check
```

- Do not run bare `python` for project tasks unless the user explicitly asks for
  it.
- Keep this package organization-agnostic. Organization names, private data
  paths, billing assumptions, and one-off repository quirks should live in a
  consuming repository's config, not in this library.
