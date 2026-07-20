# Security Policy — Autonomous Drug Repurposing Platform

> **v129 TM16 ROOT FIX (Task 16.6 — IN-044/IN-045/IN-046/IN-047):**
> Institutional-grade security posture for a pharma patient-safety platform.
> This document is the canonical reference for how the repo is configured
> to detect, prevent, and respond to security vulnerabilities.

## 1. Defense in Depth — Layered Security Controls

| Layer | Control | Owner | Status |
|-------|---------|-------|--------|
| **Source code** | CodeQL semantic analysis (Python + JS/TS) | TM16 | `.github/workflows/codeql.yml` |
| **Source code** | Bandit security linter (Python) | TM16 | `.github/workflows/ci.yml` job `bandit` |
| **Source code** | ESLint security plugin (frontend) | TM11/TM12 | `frontend/eslint.config.mjs` |
| **Dependencies** | Dependabot (pip + npm + Docker + GHA) | TM16 | `.github/dependabot.yml` |
| **Dependencies** | pip-audit (Python CVE scan) | TM16 | `.github/workflows/ci.yml` job `security-scan` |
| **Dependencies** | npm audit (frontend CVE scan) | TM16 | `.github/workflows/ci.yml` job `security-scan` |
| **Containers** | Trivy image scan | TM16 | `.github/workflows/ci.yml` job `security-scan` |
| **Supply chain** | CycloneDX SBOM generation | TM16 | `.github/workflows/ci.yml` job `security-scan` |
| **Secrets** | GitHub Secret Scanning | TM16 | Repo setting (documented below) |
| **Secrets** | GitHub Push Protection | TM16 | Repo setting (documented below) |
| **Runtime** | Sentry error tracking (PHI redaction) | TM16 | `shared/observability/__init__.py::_sentry_before_send` |
| **Runtime** | Prometheus + Alertmanager (24/7 paging) | TM16 | `observability/alerts.yml` |

---

## 2. GitHub Secret Scanning — REQUIRED REPO SETTING

**What it does:** Scans every commit (in every branch, including drafts)
for known secret formats (AWS access keys, Stripe keys, GitHub PATs,
private keys, etc.). When a match is found, GitHub:
1. Alerts the repo Security tab.
2. Notifies the partner (e.g. AWS, Stripe) so they can auto-revoke.
3. (With push protection enabled) BLOCKS the push before it lands.

**Required configuration (repo admin must run once):**

```bash
# Enable secret scanning on the repo (default: disabled).
gh api -X PUT /repos/MANOFHATERS/autonomous-drug-repurposing \
  -f security_and_analysis.secret_scanning.enabled=true

# Enable push protection (blocks commits containing known secrets).
gh api -X PUT /repos/MANOFHATERS/autonomous-drug-repurposing \
  -f security_and_analysis.secret_scanning_push_protection.enabled=true

# Verify the settings took effect.
gh api /repos/MANOFHATERS/autonomous-drug-repurposing \
  --jq '.security_and_analysis'
```

**Expected output:**
```json
{
  "secret_scanning": { "status": "enabled" },
  "secret_scanning_push_protection": { "status": "enabled" }
}
```

**Verification command (CI gate):**
```bash
gh api /repos/MANOFHATERS/autonomous-drug-repurposing \
  --jq '.security_and_analysis.secret_scanning.status' # must == "enabled"
gh api /repos/MANOFHATERS/autonomous-drug-repurposing \
  --jq '.security_and_analysis.secret_scanning_push_protection.status' # must == "enabled"
```

**If a secret is leaked despite the above:**
1. The committer rotates the secret IMMEDIATELY (assume it is public).
2. File a Security Advisory via the GitHub Security tab.
3. Run `git filter-repo` to purge the commit from history (force-push).
4. Notify the team lead — patient-safety incidents are reportable.

---

## 3. Dependabot — Dependency Monitoring

**Config file:** `.github/dependabot.yml`

Monitors 4 ecosystems on a weekly schedule (Monday 09:00 UTC):

| Ecosystem | Directories | Open-PRs Limit | Labels |
|-----------|-------------|----------------|--------|
| pip (Python) | `/`, `/phase1`, `/phase2/drugos_graph`, `/graph_transformer`, `/rl` | 5 each | dependencies, python |
| npm (frontend) | `/frontend` | 10 | dependencies, npm, frontend |
| github-actions | `/` | 5 | dependencies, github-actions |
| docker | `/` | 5 | dependencies, docker |

**Security updates** are always opened IMMEDIATELY regardless of the
weekly schedule — Dependabot opens a PR within minutes of a CVE
disclosure against any installed package.

**Grouping:** minor + patch updates are grouped into a single PR (less
noise). Major version bumps get their own PR (they may have breaking
changes that need manual review).

---

## 4. CodeQL — Semantic Code Analysis

**Config files:**
- `.github/workflows/codeql.yml` — workflow definition
- `.github/codeql/python-config.yml` — Python path config
- `.github/codeql/javascript-config.yml` — JS/TS path config

**Triggers:**
- Every push to `main` touching `*.py`, `*.ts`, `*.tsx`, `*.js`, `*.jsx`
- Every PR to `main` touching the same
- Weekly schedule (Monday 09:00 UTC) — catches new query pack releases
- Manual dispatch (for incident response)

**Query suite:** `security-extended` (200+ queries beyond the default).
Catches:
- SQL/Cypher injection (Python analyzer covers SQL; Cypher covered by
  custom tests in `tests/test_cypher_injection.py`)
- Path traversal in file operations
- Hardcoded credentials (high-entropy string detection)
- Unsafe deserialization (pickle, yaml.load without SafeLoader)
- SSRF (server-side request forgery)
- XSS in React/Next.js (via the JS/TS analyzer)

**Results:** uploaded to the repo Security tab. Findings can be
dismissed as "false positive" or "used in tests" directly from the UI.

---

## 5. Container Security — Trivy + SBOM

**Trivy** scans every Docker image built in CI for:
- OS package vulnerabilities (Debian/Ubuntu CVE database)
- Language-specific vulnerabilities (pip, npm)
- Misconfigurations (CIS Docker Benchmark)
- Exposed secrets (high-entropy string scan)

**CycloneDX SBOM** (Software Bill of Materials) is generated for every
build and uploaded as a CI artifact. Pharma partners + enterprise SOX
auditors require an SBOM for supply-chain compliance — this is not
optional for an institutional-grade platform.

**SBOM location:** `gh run download <run-id> -n sbom-cyclonedx`

---

## 6. Runtime Security — Sentry + PII Redaction

**Sentry SDK** is initialized in every FastAPI service via
`shared/observability/__init__.py::configure_app(app, service_name)`.

**PII redaction** (HIPAA/GDPR compliance):
- `send_default_pii=False` — Sentry never sends IP, cookies, form data
- `before_send` hook redacts Authorization, Cookie, X-API-Key, X-Auth-Token,
  X-CSRF-Token, Proxy-Authorization, Set-Cookie headers
- `before_send` hook strips `query_string`, `data`, `cookies` from request
  context (may contain patient identifiers — drug names, disease names,
  OMIM IDs)
- `before_send` hook drops `asyncio.CancelledError` + `KeyboardInterrupt`
  (not real errors — would flood Sentry during deploys)

**Sentry DSN configuration:**
```bash
# Dev (no Sentry — shared/observability gracefully no-ops)
unset SENTRY_DSN

# Production:
export SENTRY_DSN="https://<key>@sentry.io/<project>"
export SENTRY_ENVIRONMENT="production"
export SENTRY_RELEASE="$(git rev-parse HEAD)"
export SENTRY_TRACES_SAMPLE_RATE=0.01   # 1% performance sampling
```

---

## 7. Reporting a Vulnerability

**If you find a security vulnerability in this codebase:**

1. **DO NOT open a public GitHub issue.** Public disclosure before a
   fix is available puts patient safety at risk.
2. File a **private security advisory** via the GitHub Security tab:
   `https://github.com/MANOFHATERS/autonomous-drug-repurposing/security/advisories/new`
3. Include: affected version, repro steps, impact assessment.
4. The team acknowledges within 24h and targets a fix within 7 days
   for HIGH severity, 30 days for MEDIUM.

**If you find a vulnerability in a third-party dependency:**
1. Check if Dependabot already opened a PR (it usually does within
   minutes of disclosure).
2. If not, file a Dependabot security advisory via the same URL above.
3. GitHub will auto-generate a CVE once confirmed.

---

## 8. Audit Trail

| Date | Version | Change | Author |
|------|---------|--------|--------|
| 2026-07-20 | v129 | Initial security policy. CodeQL + Dependabot + Trivy + pip-audit + npm audit + SBOM + Sentry + Secret Scanning + Push Protection | TM16 (Cosmic) |
