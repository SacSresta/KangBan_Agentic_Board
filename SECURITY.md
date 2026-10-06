# Security

This is a local MVP for a trusted user and trusted selected repositories. It binds to `127.0.0.1` and does not provide authentication or agent registration. Host/origin checks constrain browser requests; other local processes can call the API. Do not expose the service through a public tunnel or network proxy without adding appropriate access controls.

File tools are scoped to dedicated worktrees and reject traversal, credential files, Git internals, and symlink escapes. Approved test commands are exact argument arrays executed without a shell, with timeouts. They still run repository code with the current user's OS permissions. Worktrees and the command allowlist are not an OS sandbox.

Credentials belong in the application's gitignored `.env` or process environment. Settings store environment-variable names rather than keys. API responses and durable model/tool activity redact configured credentials. Avoid sharing local data directories, traces, or logs containing private repository content.

## Reporting

Use the repository's GitHub private vulnerability reporting facility when enabled. If it is unavailable, open an issue asking the maintainer for a private reporting channel without including exploit details, credentials, or private data. Do not post sensitive reports in public issue forms.

Only the current development branch receives fixes; there is no released support policy yet.
