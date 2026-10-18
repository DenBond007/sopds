# Single-YAML TrueNAS verification (M11)

Local verification passed on 2026-10-18; private publication/CI verification is pending.
No deployment to TrueNAS is authorized or performed.

The developer/CI harness `scripts/truenas/smoke-compose.py` uses fresh synthetic
host directories owned by the image UIDs, new Compose project names, unique
networks and loopback ephemeral ports. It removes only its own resources.
It never uses the current local Compose project, datasets, books or credentials.

Command used locally:

```sh
python3 scripts/truenas/smoke-compose.py \
  --app-image sopds-app:m11-candidate --caddy-image sopds-caddy:m11-candidate \
  --logs .local/logs/m11 --full-suite
```

The NAS does not need this script. The full suite uses a separate Django test DB
inside the disposable smoke PostgreSQL server, with Chromium explicitly enabled.

| Local check | Result |
|---|---|
| Single-YAML parse, first installation and dependency failure | Passed; invalid admin credentials keep web/scanner/annotations/Caddy stopped |
| Repeat initialization | Passed; passwords, email, custom settings/genres, book IDs, shelves, positions and TLS CA preserved |
| Static mount permission failure and retry | Passed; readiness cleared, runtime guard rejects startup, corrected retry preserves data |
| Incorrect existing DB password | Rejected; original password remains usable, no role/account reset |
| SIGKILL inside initial seed transaction | Passed; uncommitted genre rolled back, retry migrates/seeds/starts automatically |
| HTTPS and authentication | Trusted generated CA; anonymous OPDS 401, administrator 200; static assets present |
| Runtime isolation | UID 10001 app/Caddy, UID 999 PostgreSQL; book write rejected |
| Full Django suite | **236/236, 95.633 s**, no skips; real Chromium login/pagination/lists/scroll/mobile smoke |
| Django/source/vendor/schema | Passed; no model/migration changes required |
| Locked dependencies/static/CI syntax | uv frozen/offline; npm ci offline/CSS reproducible; Node/shell syntax; actionlint passed |
| Offline kit | Separate archive exported; archive/file SHA256, amd64/image identity and generated bootstrap/full YAML validated |

Local logs: `.local/logs/m11/`; offline kit: `.local/sopds-truenas-m11-offline.tar.gz`.
Both are excluded from Git. All smoke containers/networks/synthetic directories
are cleaned up; existing local services/data are not used or changed.
Source CI 39146844920 ran all 236 tests, including Chromium, and found one
Compose-version test serialization error: Compose v2 omits false booleans from
normalized JSON. The assertion now accepts that representation while checking
all five explicit `create_host_path: false` declarations. A real missing-path
rejection test is added to the smoke; full CI will rerun before publication.
Private publisher CI, final image digests, package privacy and anonymous denial
will be recorded after publication.

Initial synthetic runs found and corrected URL checks persisting Constance
settings before seeding, Caddy CMD inheritance after a new ENTRYPOINT, and a
test harness attempting to publish an internal-only database to the host runner.
The synthetic database now also joins the test ingress network; production DB
stays internal and unpublished. Failed attempts
are not counted as passed validation.

Remaining NAS acceptance: exact SCALE version and Apps UI behavior, effective
ACLs and encrypted-dataset unlock, GHCR authentication/pull through that UI,
physical-reader CA trust/search/download and cold reboot. Real library/database
migration and NAS snapshot restore have not been tested. ARM64 is outside scope.
