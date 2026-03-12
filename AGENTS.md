When running Python for this repository, always use the submodule's `pixi`
environment. Prefer commands of the form:

```bash
pixi run --manifest-path /home/kolen/git/private/github-analysis-workspace/github-analysis/pyproject.toml python ...
```

Do not run bare `python` for project tasks unless the user explicitly asks for
it.
