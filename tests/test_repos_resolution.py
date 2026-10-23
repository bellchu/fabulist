"""Name resolution of a repository host, with the real resolver (test_repos.py fakes it for everything else)."""
import socket

import pytest

from app import config, repos


def test_a_host_that_does_not_resolve_is_reported_by_name(monkeypatch):
    monkeypatch.setattr(config, "ALLOW_PRIVATE_REPO_HOSTS", False)  # the SSRF guard is the code that resolves the name

    def nxdomain(*a, **k):
        raise socket.gaierror("Name or service not known")
    monkeypatch.setattr(repos.socket, "getaddrinfo", nxdomain)
    with pytest.raises(repos.RepoError, match="Cannot resolve repository host git.nowhere.example"):
        repos.check_public_host("https://git.nowhere.example/org/brds.git")
