<!-- freshness verified=2026-08-21 baseline=2026-08-30 -->
# GitHub Repo Layer — Setup Reference (verified 2026-08-21, round 6)

This covers the "repo layer" of a GitHub project: creating the repo, issue/PR templates, CODEOWNERS, dependency-update bots, branch/tag protection, labels, and release automation. It does not cover language-specific tooling (that's a separate ecosystem page).

## Official setup commands

These are real `gh` CLI commands, verified against the current GitHub CLI manual and GitHub REST API docs.

| What you want | Command | What it generates/does |
|---|---|---|
| Create the repo | `gh repo create <name> --public\|--private\|--internal [--add-readme] [--gitignore <template>] [--license <key>] [--clone] [--push]` | Creates the GitHub repository itself (optionally with README, `.gitignore`, `LICENSE`) |
| Create the repo from a standard template | `gh repo create <name> --template <owner>/<template-repo> --public\|--private\|--internal` | Copies an existing template repo's full contents — this is the *real* way to stamp out CODEOWNERS/issue templates/PR template consistently across many repos, since there's no dedicated generator for those individual files (see below). The visibility flag is required alongside `--template` — supplying `--template` (or any flag, or a name argument) without `--public`, `--private`, or `--internal` makes the command exit immediately (exit code 1) with the error "`--public`, `--private`, or `--internal` required when not running interactively" — it does not hang. Only the bare `gh repo create` with zero arguments and zero flags enters interactive mode, and even that errors immediately rather than hanging when stdin isn't a TTY (source: cli/cli `pkg/cmd/repo/create/create.go`, gh CLI v2.97.0; recorded local test run, this audit). |
| Create/manage labels | `gh label create <name> --color <hex> --description "<text>"` / `gh label clone <source-owner/repo>` / `gh label edit <name> [--color <hex>] [--description <text>] [--name <new-name>]` / `gh label delete <name> --yes` / `gh label list` | Repo labels, or a full copy of another repo's label set. `delete` needs `--yes` when run unattended: without it, a non-interactive shell exits immediately with an error (`--yes required when not running interactively`) rather than hanging. |
| Create a branch/tag ruleset (modern replacement for legacy branch protection) | `gh api --method POST -H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28" /repos/{owner}/{repo}/rulesets -f name="<name>" -f target=branch -f enforcement=active -f "conditions[ref_name][include][]=refs/heads/main" -f "rules[][type]=deletion" -f "rules[][type]=non_fast_forward"` | Creates a repository ruleset (required checks, required PRs, block force-push, block deletion, etc.) |
| Inspect existing rulesets | `gh ruleset list` / `gh ruleset view <id>` / `gh ruleset check <branch>` | Read-only inspection of what's already configured. These run unattended as long as you always pass the id/branch argument — the interactive prompt only triggers when it's omitted. |
| Cut a release with auto-generated notes | `gh release create <tag> --generate-notes [--notes-start-tag <tag>]` | Creates the GitHub Release and asks GitHub's Release Notes API to write the title and changelog from merged PRs — don't hand-write the changelog when this exists |

**Important gap, verified directly against the CLI manual:** `gh ruleset` currently only has `check`, `list`, and `view` — **there is no `gh ruleset create`, `edit`, or `delete` subcommand** (confirmed: `cli.github.com/manual/gh_ruleset_create` 404s, and `cli/cli` issue #8019 is an open feature request asking for exactly this). So the *official* way to create a ruleset today is `gh api` against the REST rulesets endpoint, not a dedicated ruleset subcommand. Do not assume a `gh ruleset create` command exists.

**Humans at a terminal — installing Renovate.** Onboarding Renovate (the alternative to Dependabot) starts with a repo/org admin installing the Renovate GitHub App through GitHub's web consent flow (the GitHub Marketplace or the app's own install page). That first-time install grant has no `gh` command and no documented REST endpoint — it's a human, browser-based step, not something an agent can run. Once a human has installed it, agents do have a documented capability for what comes after — but it's narrower than "manage repo access": the GitHub App installation-auth REST endpoint lets an agent request an installation access token scoped (via the `repositories`/`repository_ids` body parameters) to a subset of the repos the installation already has access to (source: docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation). It does not let an agent add or remove repos from that installation's access — the endpoint that does that ("Add/remove a repository to/from an app installation") only works for a human's own classic PAT with the `repo` scope, not for an app or installation token (source: docs.github.com/en/rest/apps/installations). After install, Renovate opens an "onboarding" pull request that contains the first `renovate.json` — don't hand-author that first file yourself.

### Files with no official generator

These are real, important files, but GitHub does not ship a CLI or `gh` command that generates them. The scaffold-first rule still applies in spirit: pull the current schema/docs and copy the structure exactly, rather than typing keys from memory.

- **`.github/dependabot.yml`** — no generator command exists. Required top-level keys: `version` (currently `2`) and `updates` (a list, each entry needs `package-ecosystem`, `directory`, and `schedule.interval`). Validate against the SchemaStore `dependabot.json` schema before committing.
- **`CODEOWNERS`** (in `.github/`, repo root, or `docs/`, checked in that order) — no generator command. Syntax is glob-pattern-plus-owners, similar to `.gitignore`, but three gitignore features don't work here: `!` negation, `[ ]` character ranges, and escaping a leading `#` with `\` to force it to be read as a pattern instead of a comment. Last matching line wins; inline comments are supported (e.g. `*.js    @js-owner    # inline comment`); file must be under 3 MB. (source: docs.github.com — About code owners)
- **`.github/ISSUE_TEMPLATE/*.yml` and `.github/ISSUE_TEMPLATE/config.yml`** — no CLI generator. GitHub's web UI has a "Set up templates" wizard that writes the YAML forms for you (Settings → General → Issue templates) — prefer that over hand-typing the YAML forms schema. `config.yml` controls `blank_issues_enabled` and `contact_links`.
- **`.github/pull_request_template.md`** (or `.github/PULL_REQUEST_TEMPLATE/*.md` for multiple templates) — plain markdown, no generator, no official schema beyond "it's a markdown file."
- **`.github/labeler.yml`, custom Actions workflow YAML** — hand-authored against the `actions/labeler` or relevant Action's documented input schema; not part of `gh` itself.

## Choosing

**Dependabot vs. Renovate for dependency updates.** Both are current, first-class options — pick one, not both, or you get duplicate update PRs fighting each other. Dependabot wins when you want something built into GitHub with zero extra setup: no app to install, config lives in one `.github/dependabot.yml` file, and it's already trusted in every org's security settings. Renovate wins when you want more control — grouping rules, custom schedules, more package ecosystems, and a config format (JSON5, with comments) that's easier to hand-edit later. Renovate needs a GitHub App installed by an org admin first (a human, browser step); Dependabot doesn't.

**Legacy branch protection vs. rulesets.** Rulesets are GitHub's current recommended model and the only one getting new features (layered rules, tag protection, bypass lists, evaluation mode to test before enforcing). Legacy branch protection (the old `PUT .../branches/{branch}/protection` API) still works but is the older, less flexible system. Default to rulesets unless you're maintaining an existing repo that already has legacy protection wired into other tooling.

**`gh repo create` direct vs. from a template.** Use the direct form (`--public`/`--private`/`--internal` plus `--add-readme`/`--gitignore`/`--license`) for a one-off repo. Use `--template <owner>/<template-repo>` when you want CODEOWNERS, issue templates, PR templates, labeler config, and workflows to come pre-populated and consistent across many repos — there's no dedicated `gh` generator for those individual files, so a template repo is the only way to stamp them out repeatably instead of hand-authoring each one per repo.

**Org-wide `.github` defaults vs. per-repo files.** The org-level `.github` repo can supply default `CONTRIBUTING.md`, `SECURITY.md`, `SUPPORT.md`, `FUNDING.yml`, `CODE_OF_CONDUCT.md`, `GOVERNANCE.md`, and issue/PR templates for any repo that doesn't override them — use it when you have many repos and want one place to maintain shared policy files. `CODEOWNERS` is not on that supported list, so it always has to be added per repo regardless.

**`gh release create --generate-notes` vs. a dedicated release-automation tool.** For a repo that just tags and ships, `--generate-notes` (backed by GitHub's Release Notes API) is enough and keeps the changelog accurate to actually-merged PRs. Reach for a dedicated tool like release-please only when you need version-bump automation tied to conventional commits — and check its current maintenance status before adopting it, since that space moves fast.

## Never hand-write

- **Release notes/body when `gh release create --generate-notes` is used.** Let GitHub's Release Notes API produce the title/changelog from merged PRs instead of typing a changelog by hand.
- **The first `renovate.json`, if using Renovate.** It is written by the Renovate App's onboarding pull request, not typed from scratch. (After that PR merges, humans do edit it — that's expected and fine.)
- **Ruleset JSON payloads copied from an existing ruleset.** If replicating a ruleset to another repo, export the existing one with `gh api /repos/{owner}/{repo}/rulesets/{ruleset_id}` — a plain GET that returns the ruleset as JSON directly — and adapt that JSON for the `gh api` POST, rather than re-typing rule objects from memory. `gh ruleset view` has no `--format=json` flag; its manual page lists only `--org`, `--parents`, and `--web` (plus the inherited `--repo`). That syntax was lifted from the *proposed, not-yet-shipped* export/import feature in the open request `cli/cli#8019` — don't mistake it for something that ships today. (source: cli.github.com/manual/gh_ruleset_view ; github.com/cli/cli/issues/8019)

## Thorough setup checklist

What a properly set-up repo has before feature work starts, at the repo-layer only:

1. Repository created via `gh repo create` (or from an org template repo) with the correct visibility (`--public`/`--private`/`--internal`)
2. `LICENSE` and `.gitignore` set at creation time via `--license`/`--gitignore`, not added later by hand
3. `README.md` present
4. `CODEOWNERS` file added, matching the actual team/ownership structure (not copied blind from another repo)
5. Issue templates in `.github/ISSUE_TEMPLATE/` (built via the GitHub web wizard) plus a `config.yml` deciding whether blank issues are allowed
6. A pull request template at `.github/pull_request_template.md`
7. Dependency updates configured — either `.github/dependabot.yml` (validated against the SchemaStore schema) or the Renovate GitHub App installed and its onboarding PR merged. Pick one, not both.
8. Branch/tag protection in place via a ruleset (`gh api …/rulesets`), covering at minimum: block force-push and branch deletion on the default branch, and required status checks before merge
9. Labels created or cloned (`gh label clone`) from a standard set, not invented ad hoc per repo
10. `SECURITY.md` present (how to report a vulnerability)
11. If the org wants these defaults applied automatically to every new repo that doesn't override them: a public `.github` repo at the org level carrying the shared `CONTRIBUTING.md`, `SECURITY.md`, `SUPPORT.md`, `FUNDING.yml`, and default issue/PR templates. **Note: CODEOWNERS is not covered by this org-wide default mechanism — it must exist per-repo.**
12. Release process decided: `gh release create --generate-notes`, or a dedicated release-automation tool (e.g. release-please) — verify current status of any such tool before adopting it, don't assume it's still the recommended choice.

## Traps

**Typing `gh ruleset create` (or `edit`/`delete`).** It doesn't exist — `gh ruleset` only has `check`, `list`, and `view`. `cli.github.com/manual/gh_ruleset_create` 404s, and `cli/cli` issue #8019 is an open feature request asking for exactly this plus export/import. Create and modify rulesets through `gh api` against `/repos/{owner}/{repo}/rulesets` instead.

**Trying to export a ruleset with `gh ruleset view <id> --format=json`.** That flag doesn't exist on `gh ruleset view` (confirmed on the command's own manual page — only `--org`, `--parents`, `--web` are supported). It's the wished-for syntax from the same open issue #8019, not a shipped feature. To get a ruleset as JSON, call `gh api /repos/{owner}/{repo}/rulesets/{ruleset_id}` directly (recorded-run finding, this audit).

**Running `gh repo create <name> --template <owner>/<repo>` with no visibility flag.** It does not hang waiting on an interactive prompt. Any invocation with a name argument or any flag (including `--template`) but no `--public`/`--private`/`--internal` exits immediately (exit code 1) with the error "`--public`, `--private`, or `--internal` required when not running interactively." Only the bare `gh repo create` with zero arguments and zero flags enters interactive mode — and even that errors immediately rather than hanging when stdin isn't a TTY. Pass a visibility flag alongside `--template` anyway, since the command still fails without one (source: cli/cli `pkg/cmd/repo/create/create.go`, gh CLI v2.97.0; recorded local test run, this audit).

**Running `gh label delete <name>` unattended without `--yes`.** In a non-interactive shell it exits immediately with the error `--yes required when not running interactively` - a fail-fast, not a hang - so always pass `--yes` in scripts. (cli.github.com/manual/gh_label_delete; recorded test run 2026-08-21, gh 2.97.0.)

**Guessing `dependabot.yml` keys from memory.** The required shape is `version: 2` plus an `updates` list where each entry needs `package-ecosystem`, `directory` (or the newer `directories` for globbing), and `schedule.interval` — and the schema has grown fields over time (grouping, registries), so stale memory silently drops parts of the config (validated against `json.schemastore.org/dependabot-2.0.json`, GitHub Dependabot v2 config schema).

**Copying a CODEOWNERS file verbatim from another repo.** Ownership resolves per the last matching pattern in the file (docs.github.com/.../about-code-owners), so path patterns and usernames that don't exist in the new repo silently leave code owned by nobody, with no error raised.

**Assuming a GitHub App like Renovate can be installed by an agent running a CLI command.** The first-time install grant is a human consent action through GitHub's web UI (Marketplace or the app's install page) — there is no documented `gh` or REST endpoint for it. API-based installation-token auth only works after a human has already installed the app (docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation).

**Hand-writing a changelog in the release body** when `--generate-notes` would have produced a more accurate one from actual merged PRs, and then having the two drift. (source: https://cli.github.com/manual/gh_release_create)

**Assuming CODEOWNERS is covered by the org's default `.github` repo.** It's not included in that org-wide default file list and must be added to every repo individually. (source: docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/creating-a-default-community-health-file)

**Treating the uv repo's `renovate.json5` as evidence Dependabot is deprecated.** It's a project-level tooling choice (uv uses Renovate), not a GitHub platform deprecation of Dependabot — both remain first-class, current options; don't infer a platform-wide default from one repo's choice. (source: https://docs.github.com/en/code-security/dependabot/dependabot-version-updates/about-dependabot-version-updates)

## AI and agent resources

GitHub ships several official, first-party resources for AI coding agents working against GitHub and repos hosted there:

- **GitHub MCP server** (`github/github-mcp-server`, hosted at `https://api.githubcopilot.com/mcp/`, or self-hosted via `ghcr.io/github/github-mcp-server`) -- GitHub's own MCP server. Connect to it instead of hand-rolling REST/GraphQL calls whenever an agent needs to browse code, open or update issues/PRs, read CI/Actions results, or check security alerts. Auth is OAuth, a personal access token, or a GitHub App.

- **AGENTS.md** -- the open, vendor-neutral standard for repo-level agent instructions (stewarded by the Agentic AI Foundation, not GitHub-owned). GitHub's Copilot cloud agent (renamed from "Copilot coding agent" on April 1, 2026; source: github.blog/changelog/2026-04-01-research-plan-and-code-with-copilot-cloud-agent ; docs.github.com/en/copilot/concepts/agents) officially supports it: put one at the repo root, or nest one deeper in the tree for a subproject, and the agent picks up the nearest one automatically. Use this as the default place to put build/test/style guidance meant for any agent, not just Copilot.

- **`.github/copilot-instructions.md` and `.github/instructions/*.instructions.md`** -- GitHub's own repo-custom-instructions convention. The first applies repo-wide; the second scopes instructions to matching files/paths via an `applyTo` glob. Copilot folds these into every relevant request automatically -- keep them current rather than repeating context in prompts.

- **GitHub Copilot CLI instruction discovery** -- Copilot CLI additionally reads user-level instructions from `$HOME/.copilot/copilot-instructions.md` and `$HOME/.copilot/instructions/**/*.instructions.md`, on top of the same repo-level files above, walking up from the file being edited to find them.

- **`docs.github.com/llms.txt`** -- an official, auto-generated index of GitHub's documentation built for LLMs, exposing a small API to list doc pages/versions/languages and fetch article content as structured Markdown/JSON. Fetch this before doing anything that depends on current GitHub docs, instead of trusting training data or scraping rendered HTML.

Note: GitHub's Copilot cloud agent also recognizes `CLAUDE.md` and `GEMINI.md` as agent-instruction files, but those are conventions of other vendors' agents, not GitHub-original resources -- listed here only because GitHub's own docs confirm it reads them.
