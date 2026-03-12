"""Low-level wrapper around the GitHub REST API using PyGithub."""

from __future__ import annotations

from functools import lru_cache
import time
from pathlib import Path
from typing import Any

from github import Auth, Github
from github.GithubException import GithubException, RateLimitExceededException

_PER_PAGE = 100
_HOSTS_PATH = Path.home() / ".config" / "gh" / "hosts.yml"


def _token_from_gh_config() -> str | None:
    if not _HOSTS_PATH.exists():
        return None

    in_github_dot_com = False
    for raw_line in _HOSTS_PATH.read_text().splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if not line.startswith(" "):
            in_github_dot_com = line.strip() == "github.com:"
            continue
        if in_github_dot_com and line.strip().startswith("oauth_token:"):
            return line.split(":", 1)[1].strip()
    return None


def _resolve_token() -> str:
    import os

    for env_var in ("GITHUB_TOKEN", "GH_TOKEN"):
        token = os.environ.get(env_var)
        if token:
            return token

    token = _token_from_gh_config()
    if token:
        return token

    raise RuntimeError(
        "No GitHub token found. Set GITHUB_TOKEN or GH_TOKEN, or login with the gh CLI "
        "so ~/.config/gh/hosts.yml contains an oauth_token."
    )


@lru_cache(maxsize=1)
def _client() -> Github:
    return Github(auth=Auth.Token(_resolve_token()), per_page=_PER_PAGE)


def _request(
    endpoint: str,
    *,
    params: dict[str, Any] | None = None,
    retry_on_rate_limit: bool = True,
) -> Any:
    requester = _client()._Github__requester
    path = endpoint if endpoint.startswith("/") else f"/{endpoint}"

    for attempt in range(2):
        try:
            _, payload = requester.requestJsonAndCheck("GET", path, parameters=params)
            return payload
        except RateLimitExceededException:
            if attempt == 0 and retry_on_rate_limit:
                time.sleep(60)
                continue
            raise
        except GithubException as exc:
            if attempt == 0 and retry_on_rate_limit and exc.status == 403:
                time.sleep(60)
                continue
            raise

    return None


def github_api(endpoint: str, **params: str) -> Any:
    """Single GET request to the GitHub API."""
    return _request(endpoint, params=params)


def github_api_paginate(
    endpoint: str,
    response_key: str | None = None,
    retry_on_rate_limit: bool = True,
    **params: str,
) -> list[Any]:
    """Paginated GET requests returning all items."""
    items: list[Any] = []
    page = 1

    while True:
        payload = _request(
            endpoint,
            params={**params, "per_page": str(_PER_PAGE), "page": str(page)},
            retry_on_rate_limit=retry_on_rate_limit,
        )

        if response_key is None:
            if not isinstance(payload, list):
                raise TypeError(f"Expected list payload for {endpoint}, got {type(payload)!r}")
            batch = payload
        else:
            if not isinstance(payload, dict):
                raise TypeError(f"Expected dict payload for {endpoint}, got {type(payload)!r}")
            batch = payload.get(response_key, [])
            if not isinstance(batch, list):
                raise TypeError(
                    f"Expected list payload under response_key={response_key!r} "
                    f"for {endpoint}, got {type(batch)!r}"
                )

        items.extend(batch)
        if len(batch) < _PER_PAGE:
            break
        page += 1

    return items
