# Privacy and security

[Documentation](../README.md) · [Runbook](runbook.md) · [Report a vulnerability](../../.github/SECURITY.md)

## Reference access model

The current protected reference uses digest-token RBAC, not Microsoft organizational/personal-account sign-in. Configure `TACTIQ_AUTH_ENABLED=true` and `TACTIQ_API_TOKENS_JSON` entries containing only `token_sha256`, `tenant_id`, `subject`, and `role`. Generate/distribute high-entropy credentials outside source control.

| Role | Reference permissions |
| --- | --- |
| Viewer | Read tenant-owned results/media; request explanations and speech |
| Analyst | Viewer permissions plus create/analyze/control and commentary audit |
| Admin | Analyst permissions plus operations and inactive-match deletion |

The browser exchanges a credential for an HttpOnly, SameSite session cookie; production requires Secure. Same-origin proxying is preferred. Cookie writes and WebSocket connections validate Origin. Unknown/foreign-tenant resources return not found.

Session-cookie expiry does not revoke an independently distributed token. Remove/replace its digest and restart the reference API to revoke it. Local mode is visibly unauthenticated and must never be exposed as a protected public service.

## Data boundaries

- Private media routes validate registered match-owned paths, reject symlinks/cross-match associations, and retain stream pins.
- Signed source URLs are downloaded before persistence; YouTube jobs retain canonical IDs/managed paths rather than ephemeral signed URLs.
- Resolver children use a bounded credential-free environment without browser cookies, plugins, or access-restriction bypasses.
- Only active match/job references are retained in tab-scoped session storage; sign-out clears remembered work and displayed private data.
- Reference job history can outlive deleted matches. There is no promise of automatic 24-hour erasure in the current reference.
- Pause jobs before coordinated database/media backup. Retention must cover terminal job records and recoverable backups as well as live media.

## Approved identity/trial target — planned/in progress

The target uses Microsoft authorization-code/OIDC and server-managed sessions supporting intended personal/work/school accounts. Directory ID is not workspace ownership.

An opaque HttpOnly guest session owns results. A keyed pseudonymous verified ingress IP controls only a shared allowance: three new analyses in a 24-hour window; the fourth requires sign-in before storage/work admission. Replay, Listen, and reopening a result do not consume another trial use, but remain subject to paid-usage safeguards.

Admission must be atomic/idempotent across replicas; malformed requests and retries must not duplicate consumption. Different browsers behind one office/school IP must never see one another's results. Promotion requires proof of the same guest session, never just the IP.

Guest access is intended to expire after 24 hours unless promoted; deletion must avoid promotion races and disclose backup/soft-delete retention. These are requirements, **not verified deployed guarantees**.

## Responsible AI and public release

Model input should be selected server evidence, not arbitrary untrusted instructions. Do not expose unrestricted public text-to-model/Speech proxies. Preserve number/entity/evidence validation and explicit provider failure labels. Independent football, model, language, security, and capacity evaluations remain required.

Never publish tokens, private footage, signed URLs, raw sensitive request bodies, or environment files. Any credential previously exposed in chat/logs needs owner-authorized rotation before release. Removing tracked media/logs from the latest tree does not purge prior Git history; history rewriting needs separate owner approval.
