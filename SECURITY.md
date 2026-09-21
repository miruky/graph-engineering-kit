# Security model

Use project configuration and commands only after review. The toolkit is a local development controller, not a security sandbox for malicious executables. Read [the trust boundaries](docs/ARCHITECTURE.md) before granting tools or credentials to a worker.

Do not post credentials, private customer data or exploitable secrets in a public issue. For a sensitive vulnerability, use the repository hosting service's private vulnerability-reporting option when available; otherwise contact the repository maintainer through an appropriate private channel without including the exploit payload publicly.

Dependency source hashes and preserved licenses are recorded in `vendor-provenance.json` and `THIRD_PARTY_NOTICES.md` where third-party code is included. No runtime CDN or package download is required.
