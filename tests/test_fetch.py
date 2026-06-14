"""Tests for the fetch layer that do not hit the network."""

from __future__ import annotations

import pytest
from github.GithubException import GithubException

from github_analysis import fetch


def test_list_account_repos_uses_org_endpoint(monkeypatch):
    calls = []

    def fake_paginate(endpoint, **params):
        calls.append(endpoint)
        return [{"name": "repo-a"}, {"name": "repo-b"}]

    monkeypatch.setattr(fetch, "github_api_paginate", fake_paginate)
    repos = fetch.list_account_repos("some-org")

    assert [r["name"] for r in repos] == ["repo-a", "repo-b"]
    assert calls == ["/orgs/some-org/repos"]


def test_list_account_repos_falls_back_to_user_endpoint(monkeypatch):
    calls = []

    def fake_paginate(endpoint, **params):
        calls.append(endpoint)
        if endpoint.startswith("/orgs/"):
            raise GithubException(404, {"message": "Not Found"}, None)
        return [{"name": "user-repo"}]

    monkeypatch.setattr(fetch, "github_api_paginate", fake_paginate)
    repos = fetch.list_account_repos("ickc")

    assert [r["name"] for r in repos] == ["user-repo"]
    assert calls == ["/orgs/ickc/repos", "/users/ickc/repos"]


def test_list_account_repos_reraises_non_404(monkeypatch):
    def fake_paginate(endpoint, **params):
        raise GithubException(403, {"message": "Forbidden"}, None)

    monkeypatch.setattr(fetch, "github_api_paginate", fake_paginate)
    with pytest.raises(GithubException):
        fetch.list_account_repos("blocked")
