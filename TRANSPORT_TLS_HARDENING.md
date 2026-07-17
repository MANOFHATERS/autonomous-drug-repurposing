# Transport TLS Hardening (IN-073)

**Status:** Production override `docker-compose.tls.yml` implements full
transport encryption. The base `docker-compose.yml` uses `sslmode=prefer`
(Postgres) + `bolt://` (Neo4j) on an `internal: true` data network (no
outbound internet) — a defensible dev/CI posture. For FDA 21 CFR Part 11 /
Gxp production, apply the TLS override.

## Apply

```bash
cp .env.example .env && edit .env   # secrets (compose fails fast if unset)
docker-compose -f docker-compose.yml -f docker-compose.tls.yml --profile full-stack up -d
```

## What the override does

### Postgres
- Switches to `postgres:16` (Debian — has `openssl`; alpine lacks it).
- Generates a self-signed cert on first start (`/var/lib/postgresql/server.crt` + `.key`).
- Enables `ssl=on` + `ssl_min_protocol_version=TLSv1.2` via server command flags.
- All Postgres URIs: `sslmode=prefer` → `sslmode=require` (refuse plaintext).

**CA-verified TLS (higher bar):** mount a CA-signed cert (CN=postgres) into
the container, point `ssl_cert_file`/`ssl_key_file` at it, and change every
URI to `sslmode=verify-full` + `sslrootcert=/path/to/ca.pem`.

### Neo4j
- `NEO4J_server_ssl_bolt_enabled=true` + `policy=REQUIRED`.
- Neo4j 5.x auto-generates a self-signed Bolt cert on first start.
- All Neo4j URIs: `bolt://` → `bolt+ssc://` (TLS, accept self-signed —
  correct for an internal Docker network).

**CA-verified TLS:** mount a CA-signed cert at
`/var/lib/neo4j/certificates/bolt/` and use `bolt+s://` (verifies cert
against the system CA store).

### MLflow
MLflow does not support native TLS. For production, run a TLS-terminating
reverse proxy (Caddy or Envoy) in front of it:

```yaml
# Example Caddy sidecar (add to docker-compose.tls.yml for production)
mlflow-proxy:
  image: caddy:2.8
  ports: ["5043:5043"]
  command: caddy reverse-proxy --from :5043 --to mlflow:5000
  networks: [app]
```

Then point `MLFLOW_TRACKING_URI` at `https://mlflow-proxy:50443`.

## Verification

```bash
# YAML parses (CI gate)
python -c "import yaml; yaml.safe_load(open('docker-compose.tls.yml'))"

# Postgres cert generation runs (manual, requires Docker)
docker-compose -f docker-compose.yml -f docker-compose.tls.yml run --rm postgres \
  bash -c "ls -la /var/lib/postgresql/server.crt && openssl x509 -in /var/lib/postgresql/server.crt -noout -subject"

# Neo4j Bolt TLS enabled
docker-compose -f docker-compose.yml -f docker-compose.tls.yml exec neo4j \
  cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "CALL dbms.listConfig() YIELD name, value WHERE name STARTS WITH 'server.ssl.bolt' RETURN name, value"
```

## Why an override (not the default)

The base compose is optimized for dev/CI: fast startup, no cert management,
no openssl dependency. Forcing TLS by default would break the dev/CI loop
(cert generation adds ~2s startup; `sslmode=require` fails if SSL isn't
configured). The override is the standard Docker Compose production-hardening
pattern — opt-in via `-f docker-compose.tls.yml`.

The `internal: true` data network in the base compose already prevents
egress (no outbound internet), so the plaintext-internal-traffic risk is
contained to the host's Docker bridge — not exposed to the network. The
override closes the in-bridge sniffing gap for production.
