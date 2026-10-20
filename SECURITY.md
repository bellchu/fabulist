# Security

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private vulnerability reporting
(repository **Security** tab → **Report a vulnerability**) and include the affected version
(`version` in `/api/meta`), reproduction steps and impact. Expect an acknowledgement within a
few working days.

## Scope and design

The README's **Security** section describes the controls in place: SSO and role-based access,
CSRF protection, response headers, encrypted secrets at rest, SSRF guards for repository URLs and
personal-data masking. Dependencies are checked with `pip-audit` in `scripts/ci.sh` and nightly
in the cluster.
