"""Who is the client? Throttling and rate limiting key on the answer, so it must not be something the client can choose."""
from fastapi import Request

from . import config


def client_ip(request: Request) -> str:
    """Cloudflare's header when the deployment says it is behind Cloudflare (TRUST_CF_CONNECTING_IP), else the address the nearest trusted
    proxy appended to X-Forwarded-For (TRUSTED_PROXY_HOPS from the right: everything to its left is the client's own claim), else the peer."""
    if config.TRUST_CF_CONNECTING_IP:
        cf = request.headers.get("cf-connecting-ip", "").strip()
        if cf:
            return cf
    chain = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    hops = max(1, config.TRUSTED_PROXY_HOPS)
    if len(chain) >= hops:
        return chain[-hops]
    return request.client.host if request.client else ""
