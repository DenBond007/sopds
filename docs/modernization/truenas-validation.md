# Single-YAML TrueNAS verification (M11)

Local and CI verification passed on 2026-10-18. The delivered YAML is byte-for-byte
the successful private publisher artifact, built from source `9b27b662f137682c66d8773b43075308a34a5ba5`.
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
| PostgreSQL placeholder before first initdb | Rejected before PG_VERSION exists; correct credential can initialize normally |
| Missing host library path | Rejected without creating a host directory |
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
rejection test was added to the smoke; the final full CI and smoke both passed.
Final verification succeeded:

| CI / release check | Result |
|---|---|
| [Source CI](https://github.com/DenBond007/sopds/actions/runs/39147639184) | 236/236 in 81.359 s, Chromium smoke executed |
| [Private publisher](https://github.com/DenBond007/sopds-images/actions/runs/39147640015) | All jobs successful; 236/236 in 73.413 s, Chromium executed |
| Published-image standalone YAML smoke | All first/repeat/placeholder/missing-path/mount/wrong-password/SIGKILL cases passed; only synthetic data |
| Package privacy | Both packages private before application-layer upload and after publication |
| Anonymous access | Both rejected after upload in CI and independently from the developer host |
| Immutable images | All three exact digests pulled and linux/amd64 identity checked in CI |
| Artifact | Archive/file SHA256, allowlist, source commit, clean checkout and uv.lock identity checked after download |
| Final YAML | Exact renderer/artifact match and local Compose validation; no real credentials |
| Final targeted regression group | 27/27 registry/offline/YAML/backup tests |

Artifacts are saved locally in `.local/m11-published-39147640015/`, outside Git.
The NAS needs only [compose.yaml](../../infra/truenas/compose.yaml).

| Component | Published immutable reference |
|---|---|
| app | `ghcr.io/denbond007/sopds-nas-app@sha256:c497315352afe8d1550cc0e394a92e22206dd8153e907bcb19a01080b7fb34e1` |
| caddy | `ghcr.io/denbond007/sopds-nas-caddy@sha256:1c5f4f665baf06d30614f9a33d80e0611fbeadb197c130cef1ecaeb83712eaaa` |
| db | `postgres:17.11-bookworm@sha256:91eb910c44c7ed13f7f1a4ccadaa9ca72ef14cddc04cacb6e070e48eb44731a3` |

SHA256 of the unpersonalized YAML:
`67f0089870938795376641dc0d643ad410c8ef8867a3ad1ecc623eb75308ef19`.
Editing administrator settings naturally changes this hash.
The source branch is `codex/library-operations`; legacy `master` was not modified.
The existing local services stayed running and healthy at their original ports.

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
