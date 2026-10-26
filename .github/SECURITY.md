# Security policy

Tactiq is pre-release; no enterprise SLA or independently certified security posture is claimed. The protected reference runtime is single-host token RBAC. Microsoft identity and distributed cloud controls are planned/in progress until verified.

## Reporting

Do not publish credentials, private footage, signed URLs, or exploitable details in public issues. If the repository's **Security → Advisories → Report a vulnerability** option is enabled, use that private channel:

https://github.com/wenakanew/tactiq/security/advisories

If private reporting is unavailable, open a minimal public issue requesting a private reporting channel **without technical exploit details or sensitive data**. No unverified contact address or response-time guarantee is promised.

Include affected release/commit, a sanitized description, impact, and reproduction details only through the agreed private channel. Do not test against other users' workspaces or perform unauthorized cloud/billable operations.

## Handling an exposure

Preserve sanitized evidence, restrict admission, revoke affected credentials through authorized operations, and review access/history. Removing a secret or private asset from the latest tree does not erase Git history or revoke a credential.

See [privacy and security](../docs/operations/privacy-and-security.md) and the [incident runbook](../docs/operations/runbook.md) for the reference operational boundaries.
