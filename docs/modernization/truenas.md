# SOPDS on TrueNAS SCALE: one YAML

Target: **TrueNAS SCALE 24.10+ / Community Edition, linux/amd64** with Docker Apps.
Use [the complete release YAML](../../infra/truenas/compose.yaml). Fill in its clearly
marked `x-sopds-settings` block and paste the **whole file** into **Apps → Discover
Apps → ⋮ → Install via YAML**, application name `sopds`. TrueNAS supplies the
Compose project name. No checkout, archive, external `.env`, host initialization
script or Python command is needed on the NAS.

Preparation and publication do not deploy anything. This release has not been
installed on a real NAS. The existing local stack and its data are separate.
The [offline/configuration kit](truenas-kit.md) remains supported as an alternative;
its `prepare.py`/`control.py` instructions apply only to that kit.

## Prepare storage and permissions

Choose an Apps pool in TrueNAS first. Reserve the NAS IPv4 address and an unused
HTTPS port (example: 9444); it must not conflict with the NAS management UI.
Create **all** host directories before installation. Missing paths are rejected;
the YAML never creates/chowns datasets or modifies books.

| YAML setting | Example | Required owner/access |
|---|---|---|
| `books` | `/mnt/tank/books` | Keep existing ownership; UID 10001 needs read on files and read/traverse on every directory, including parents |
| `pgdata` | `/mnt/tank/apps/sopds/pgdata` | 999:999, 0700; empty for first installation |
| `static` | `/mnt/tank/apps/sopds/static` | 10001:10001, 0755; init writes, other services read |
| `tls_data` | `/mnt/tank/apps/sopds/caddy-data` | 10001:10001, 0700; permanent CA and TLS private keys |
| `tls_config` | `/mnt/tank/apps/sopds/caddy-config` | 10001:10001, 0700 |

Use separate datasets/directories for books and application state. Data paths
must be distinct and outside the books tree. Prefer POSIX permissions for the
private application datasets. Check effective ACLs and parent traversal in the
TrueNAS permission editor: an inherited SMB ACL can prevent access despite the
numeric owner/mode. Do not make the library writable or apply recursive ownership
changes to existing books. Dataset encryption must be unlocked before Apps start.

The library is mounted read-only everywhere. PostgreSQL, application configuration,
accounts, catalog and reading positions persist in `pgdata`; static files and TLS
state have their own persistent binds. All runtime processes use non-root UIDs,
drop capabilities and forbid privilege escalation; app/Caddy filesystems are read-only.

## Authenticate private images in TrueNAS

The two private packages are `ghcr.io/denbond007/sopds-nas-app` and
`ghcr.io/denbond007/sopds-nas-caddy`. Use a **GitHub PAT classic with `read:packages`**,
owned by an account that can read both packages; enable organization SSO if required.
Keep this token in TrueNAS registry settings, separate from application passwords.
Do not paste it into this YAML or commit it. See [GitHub's GHCR authentication guide](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry#authenticating-to-the-container-registry).

**SCALE 25.04 and later:** **Apps → Configuration → Sign-in to a Docker registry →
Add Registry → Other Registry**. URI `ghcr.io`, display name `SOPDS GHCR`, username
the GitHub account, password the PAT. Save and confirm success. These are saved
Apps credentials. [TrueNAS 25.04 registry UI](https://www.truenas.com/docs/scale/25.04/scaleuireference/apps/#docker-registries).
The [25.04 middleware](https://github.com/truenas/middleware/blob/TS-25.04.2.6/src/middlewared/middlewared/plugins/apps/compose_utils.py)
explicitly runs Compose using `/etc/docker` configuration; a login in an SSH
user's default Docker config is a different context.

**SCALE 24.10:** the supported UI is **Apps → Configuration → Manage Container
Images → Pull Image**, which accepts a username/password for each pull, rather
than the newer saved-registry screen. Pre-pull **each exact private release image**
from the YAML using that dialog and the PAT. Put the complete `ghcr.io/…@sha256:…`
reference in **Image Name** and **clear Image Tag** (its default is `latest`). The
[24.10 form](https://github.com/truenas/webui/blob/TS-24.10.2.2/src/app/pages/apps/components/docker-images/pull-image-form/pull-image-form.component.ts)
sends that full reference unchanged when the tag is empty. Alternatively use the
release's exact `sha-<source-commit>` tag, which identifies the same amd64 manifest.
The [24.10 UI reference](https://www.truenas.com/docs/scale/24.10/scaleuireference/apps/#pull-image)
and [pull implementation](https://github.com/truenas/middleware/blob/TS-24.10.2.2/src/middlewared/middlewared/plugins/apps_images/images.py)
confirm per-request authenticated downloads. The YAML uses `pull_policy: missing`,
so these cached immutable images can start without assuming persistent login.
Re-pull new release images before every update, and after image-cache deletion.
Avoid a force-pull operation unless Apps has credentials for that request.

A shell `docker login` alone is **not** the documented Apps authentication method.
Actual registry login/pull in your NAS UI remains an acceptance check. If private
pulls fail, fix registry credentials/access before installing; keep packages private.

## Fill in the YAML and install

In `x-sopds-settings`, replace the example IP, port, timezone, all five paths,
three infrastructure secrets and initial administrator credentials. Generate
**three independent random hex secrets of at least 64 characters** in a password
manager for Django, PostgreSQL administrator and the application database role.
Use a strong initial admin password of at least 16 characters and a valid email.
The initializer rejects credential placeholders and weak admin passwords.

Keep YAML strings quoted. Compose processes `$` even inside single quotes: use
`$$` for each literal dollar in a password. Hex secrets avoid this issue. Keep a
private backup of the personalized YAML; it and the TrueNAS configuration contain
credentials. No real credentials are included in Git or built images.

Paste and save the complete YAML. The automatic sequence is:

1. PostgreSQL initializes its persistent directory and becomes healthy.
2. The one-shot `init` service creates the least-privileged `sopds` database role
   only if absent, applies migrations, atomically seeds a **new** library's genres,
   container paths and authenticated defaults, and creates the administrator.
3. `init` collects static files and records completion for the pinned app release.
4. Web, scanner and annotations start after `init` exits successfully. Caddy also
   waits for healthy web. Only Caddy's HTTPS port is published.

`init` normally shows **Exited (0)**, while db/web/Caddy become healthy.
A failed init exits nonzero and blocks dependent services. Inspect its Apps logs,
correct the settings/permissions and stop/start the same app to retry. Never delete
`pgdata` or initialization checkpoints to fix an error. A durable database checkpoint
precedes migrations; fixture/default/admin creation is one transaction. SIGKILL or
failed static collection can be retried. Existing passwords/settings/catalog rows
are preserved; existing databases without a checkpoint receive migrations/static,
**no fixtures or automatic account changes**. Runtime readiness guards also cover
Docker daemon restarts, which do not themselves enforce Compose dependencies.

Open `https://<nas_ip>:<https_port>/web/` and `/admin/`; the OPDS URL is `/opds/`.
Caddy issues certificates with its private CA. Copy the **public** certificate
`<tls_data>/caddy/pki/authorities/local/root.crt` through the NAS file interface to
trusted clients/readers and trust that CA. Keep CA private keys on the protected
dataset. Verify 401 without credentials, 200 with credentials, web login, static
assets, search and download; create an ordinary reader in admin. Trust verification
must stay enabled. Check physical-reader compatibility and NAS reboot separately.

## Update, backup and recovery

Before an update, **stop the app in Apps** and take a consistent backup/snapshot
of `pgdata`, static and both TLS directories, plus the personalized YAML and books
(or their independently verified backup). Pause external writers to books during
its snapshot. Replicate backups off the pool. The bare YAML does not install a
backup schedule; configure snapshot/replication tasks in TrueNAS separately.

Authenticate/pre-pull the new private release images as above. Edit the **same**
app with the new complete release YAML, copying your settings block unchanged.
Keep both database passwords and Django secret, paths, port and timezone; change
admin passwords through the application UI. YAML admin values apply only to a new
library and never reset a previously created account. Preserve all image pins and
the generated readiness ID together. Start/save; migrations and static collection
run automatically before services. Check init exit status, health and HTTPS.

If an update fails, leave the app stopped while investigating. Retrying the same
release is safe. Schema rollback requires the matching backup and old YAML/images;
simply selecting an older tag does not undo a database migration. Restore to new
separate datasets with the original owners, preserving the matching database,
TLS CA and secrets. Do not overwrite the source datasets before acceptance. An
existing imported database must already have valid container library/temp/log
settings and its original `sopds` role/password; init does not rewrite them.
PostgreSQL major-version upgrades require a separate migration.

## Release and verification

Maintainers publish the tested source commit from `codex/library-operations` via
[the private publisher](https://github.com/DenBond007/sopds-images/actions/workflows/publish-truenas.yml).
Legacy `master` is not used. Publication still checks package privacy **before**
application layers, checks privacy again and rejects anonymous access afterward.
The private workflow emits `sopds-truenas.yaml` directly in its artifact alongside
the optional existing kit. The renderer couples the app digest and readiness ID.

[Verification results](truenas-validation.md) distinguish automated synthetic tests
from NAS acceptance. No NAS deployment, real library migration, NAS ACL validation,
private pull through the NAS UI, physical-reader CA check or NAS reboot is claimed.
