"""Low-level wrapper around the gh CLI."""

from __future__ import annotations

import json
import subprocess
import time
from typing import Any


def _run_gh(args: list[str]) -> str:
    result = subprocess.run(
        ["gh"] + args,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _parse_pages(raw: str, response_key: str | None) -> list[Any]:
    """Parse concatenated JSON values from gh api --paginate output."""
    items: list[Any] = []
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(raw):
        while pos < len(raw) and raw[pos].isspace():
            pos += 1
        if pos >= len(raw):
            break
        obj, consumed = decoder.raw_decode(raw, pos)
        pos += consumed
        if response_key and isinstance(obj, dict):
            items.extend(obj.get(response_key, []))
        elif isinstance(obj, list):
            items.extend(obj)
        else:
            items.append(obj)
    return items


def gh_api(endpoint: str, **params: str) -> Any:
    """Single request to the GitHub API."""
    args = ["api", endpoint]
    for k, v in params.items():
        args += ["-F", f"{k}={v}"]
    return json.loads(_run_gh(args))


def gh_api_paginate(
    endpoint: str,
    response_key: str | None = None,
    retry_on_rate_limit: bool = True,
    **params: str,
) -> list[Any]:
    """Paginated requests returning all items.

    Args:
        endpoint: GitHub API endpoint path.
        response_key: If the response is a dict, extract items from this key
            (e.g. "workflow_runs", "jobs"). If None, expects a direct list.
        retry_on_rate_limit: Sleep and retry once on HTTP 429/403 rate limit.
        **params: Query parameters passed as -F key=value to gh.
    """
    args = ["api", "--paginate", endpoint]
    for k, v in params.items():
        args += ["-F", f"{k}={v}"]

    for attempt in range(2):
        try:
            raw = _run_gh(args)
            return _parse_pages(raw, response_key)
        except subprocess.CalledProcessError as e:
            if attempt == 0 and retry_on_rate_limit and "rate limit" in e.stderr.lower():
                time.sleep(60)
                continue
            raise
    return []  # unreachable
