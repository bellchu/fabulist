"""Throttling and rate limiting key on the client address, so a client must not be able to choose it."""
from types import SimpleNamespace

from app import config, netaddr


def req(headers=None, peer="10.0.0.9"):
    return SimpleNamespace(headers=headers or {}, client=SimpleNamespace(host=peer) if peer else None)


def test_a_client_cannot_pick_its_address_by_sending_a_forwarded_for_header(monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_PROXY_HOPS", 1)
    # the proxy appends the address it saw: the left part is whatever the client claimed
    assert netaddr.client_ip(req({"x-forwarded-for": "6.6.6.6, 203.0.113.7"})) == "203.0.113.7"
    assert netaddr.client_ip(req({"x-forwarded-for": "203.0.113.7"})) == "203.0.113.7"


def test_two_proxies_in_front(monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_PROXY_HOPS", 2)
    assert netaddr.client_ip(req({"x-forwarded-for": "6.6.6.6, 203.0.113.7, 172.16.0.4"})) == "203.0.113.7"


def test_without_the_header_the_peer_is_the_client(monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_PROXY_HOPS", 1)
    assert netaddr.client_ip(req()) == "10.0.0.9"
    assert netaddr.client_ip(req(peer=None)) == ""
    assert netaddr.client_ip(req({"x-forwarded-for": ""})) == "10.0.0.9"


def test_cloudflares_header_is_used_when_trusted_and_ignored_when_not(monkeypatch):
    headers = {"cf-connecting-ip": "198.51.100.5", "x-forwarded-for": "198.51.100.5, 104.16.0.1"}
    monkeypatch.setattr(config, "TRUST_CF_CONNECTING_IP", True)
    assert netaddr.client_ip(req(headers)) == "198.51.100.5"
    monkeypatch.setattr(config, "TRUST_CF_CONNECTING_IP", False)
    monkeypatch.setattr(config, "TRUSTED_PROXY_HOPS", 1)
    assert netaddr.client_ip(req(headers)) == "104.16.0.1"


def test_failed_sign_in_throttling_cannot_be_dodged_by_rotating_the_header(client, monkeypatch):
    from app import users
    monkeypatch.setattr(config, "BASIC_USER", "root")
    monkeypatch.setattr(config, "BASIC_PASSWORD", "root-password-123")
    monkeypatch.setattr(config, "TRUST_CF_CONNECTING_IP", False)
    monkeypatch.setattr(config, "TRUSTED_PROXY_HOPS", 1)
    users._cache.clear()
    users._failures.clear()
    codes = [client.get("/api/projects", auth=("nobody%d" % i, "wrong-password-1"), headers={"x-forwarded-for": "9.9.9.%d, 203.0.113.50" % i}).status_code
             for i in range(users.FAIL_LIMIT + 3)]
    assert codes[-1] == 429, codes
