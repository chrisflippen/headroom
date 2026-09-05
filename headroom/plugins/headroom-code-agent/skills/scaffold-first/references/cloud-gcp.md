<!-- freshness verified=2026-08-30 baseline=2026-09-04 -->
<!-- probe: gcloud-sdk | curl -s https://dl.google.com/dl/cloudsdk/channels/rapid/components-2.json | python3 -c "import sys,json;print(json.load(sys.stdin)['version'])" | 583.0.0 -->
# cloud-gcp scaffold-first reference (researched 2026-08-30)

Covers **Google Cloud** — the ruled long-term hosting direction (2026-08-20: Railway isn't sustainable for growth; GKE named; no destination formally ruled). Everything marked *run live* was executed this session on Christopher's real account. Same cloud rule as Railway: **reads free; anything that creates billable resources or changes project state needs Christopher's explicit go in the moment.**

## State on this machine (checked live)

- `gcloud` 581.0.0 installed via Homebrew (current channel version at this check: 583.0.0 — two behind; see Traps). Components installed: gcloud, bq, gsutil, bundled python. (Re-checked live 2026-09-04: still 581.0.0 locally — the Homebrew install has not moved while the channel has advanced twice.)
- Auth already live: `christopher@digital1group.com` active, plus a merchant-center service account. `gcloud auth list` is the first command of any GCP session.
- Default project: `d1-admanagement`. Eight+ projects listed (`d1-identity`, several `gen-lang-client-*`, ...). Always pass `--project` explicitly rather than trusting the default.
- Cloud Run reads worked (`gcloud run services list --project d1-admanagement` → two services). The account is real and live — treat every mutating command as production.

## What agents do freely (reads — run live)

```bash
gcloud auth list
gcloud projects list
gcloud config get-value project
gcloud run services list --project <id>
```
Plus any `list`/`describe`/`get-iam-policy` style command. `--format='value(...)'` and `--format=json` make output machine-readable.

## What needs Christopher's explicit go

- `gcloud projects create`, enabling APIs (`services enable` — many start billing), anything `create`/`deploy`/`delete` (Cloud Run deploys, GKE clusters, buckets, service accounts, IAM changes).
- `gcloud auth login` / new credentials — Christopher does interactive auth himself; agents never handle the OAuth flow.
- Infrastructure-as-code applies (Terraform/OpenTofu) — the IaC tool for the GCP migration is NOT yet chosen or verified; surface it as a decision when the migration work actually starts, don't default it.

## Traps

**Homebrew's gcloud can't self-update and lags the channel.** `gcloud components update` is disabled under Homebrew-managed installs (the package manager owns it), and this machine sat one version behind the live channel. Update via `brew upgrade google-cloud-sdk` — or, for a Google-official scriptable install, the tarball at `https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/` (the probe above reads the channel's current version). An inherited Homebrew install is fine for reads; know which owner updates it. (Observed 2026-08-30.)

**The default project is a loaded gun.** `d1-admanagement` is a production ad-management project; a mutating command without `--project` lands there. Always name the project. (Observed live: that IS the configured default.)

**Two credentialed accounts exist** (the user account and a service account) — check which is ACTIVE before acting; `gcloud config set account` switches. (Observed 2026-08-30.)

## AI and agent resources

- `https://dl.google.com/dl/cloudsdk/channels/rapid/components-2.json` — machine-readable current SDK version (this page's probe).
- `gcloud <group> --help` is generated from the live API surface and beats memory; `gcloud interactive` exists for humans.
- GCP doc questions: Context7 or `gcloud topic` pages; don't answer IAM/billing questions from memory.
