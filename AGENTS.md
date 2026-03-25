# Repository notes for future agents

- Use `pixi` for all Python environment and task execution in this repo.
- If `pixi` is unavailable, install it with:
  - `curl -fsSL https://pixi.sh/install.sh | sh`
- Build/update the static dashboard with:
  - `~/.pixi/bin/pixi run build-dashboard`
- Dashboard output is committed at `docs/index.html` for GitHub Pages hosting.

When running Python for this repository, always use the submodule's `pixi`
environment. Prefer commands of the form:

```bash
pixi run --manifest-path /home/kolen/git/private/github-analysis-workspace/github-analysis/pyproject.toml python ...
```

Do not run bare `python` for project tasks unless the user explicitly asks for
it.
