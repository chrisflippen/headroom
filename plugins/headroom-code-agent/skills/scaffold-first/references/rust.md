<!-- freshness verified=2026-08-21 baseline=2026-09-04 -->
<!-- probe: rust-lang/rust | curl -s https://api.github.com/repos/rust-lang/rust/releases/latest | python3 -c "import sys,json;print(json.load(sys.stdin)['tag_name'])" | 1.98.1 -->
# Rust scaffolding reference (verified 2026-08-21, round 3)

## Official setup commands

| Command | What it generates |
|---|---|
| `cargo new <name>` | A new folder called `<name>` containing `Cargo.toml`, `src/main.rs` (or `src/lib.rs` with `--lib`), and a `.gitignore`. It also runs `git init` for you unless you pass `--vcs none`, or the directory is already inside an existing VCS repo (then it's skipped automatically). Default edition written into `Cargo.toml` is **2024** on current stable Rust; pass `--edition 2021` if you need an older one. |
| `cargo init [path]` | Same as `cargo new`, but works inside a folder you already have (uses the folder name as the package name, or `--name` to override). Use this instead of `cargo new` when the directory already exists. |
| `cargo add <crate>` | Adds a dependency line to `[dependencies]` in `Cargo.toml`, picking the latest compatible version from the registry and writing a semver requirement like `"1.4"` — you never type the version number yourself. Flags: `--dev` (dev-dependency), `--build` (build-dependency), `--path`/`--git` (local or git source), `--features <list>`, `--dry-run` to preview. Run `cargo add foo@2` to pin a specific major version if you truly need one. |
| `cargo remove <crate>` | Removes a dependency line cleanly (companion to `cargo add`; don't hand-delete the line). |
| `cargo build` / `cargo check` / `cargo add` | Any of these (re)writes `Cargo.lock` to match the current dependency graph. This file is 100% machine-generated — never open and edit it by hand. |
| `cargo fmt` | Formats the code according to `rustfmt.toml` / `.rustfmt.toml` if present, or Rust's default style if not. There is no "cargo fmt init" — the config file itself is meant to be hand-written by you when you want non-default settings; it's a settings file, not a generated artifact. |
| `cargo clippy` | Runs the linter. Reads `clippy.toml` / `.clippy.toml` (also hand-written, same reasoning as rustfmt) plus any `[lints.clippy]` / `[workspace.lints.clippy]` table inside `Cargo.toml`. `cargo clippy --fix --allow-dirty` auto-applies safe suggestions — the `--allow-dirty` flag (or `--allow-staged` if changes are staged, or `--allow-no-vcs` outside a VCS repo) is required because the underlying `cargo fix` machinery refuses to touch a working tree with uncommitted changes, which is the normal state mid-scaffold. |
| (no CLI generator — write by hand) | A workspace root `Cargo.toml` with a `[workspace]` section (`members = [...]`, `resolver = "3"`). There is no official "cargo workspace init" command; the Cargo Book itself documents this file as something you write directly. Each member package underneath is still created with `cargo new`/`cargo init`. |

## Choosing

**cargo new vs. cargo init.** Use `cargo new <name>` only when the target directory does not exist yet — it hard-refuses (errors, doesn't touch anything) the instant the directory is already there. Use `cargo init [path]` for a directory that already exists, whether it's empty or already has files in it (it'll pick up an existing `src/main.rs`/`src/lib.rs` instead of overwriting it). There's no real judgment call here — pick based on whether the folder exists, not preference.

**Which resolver version for a workspace.** A virtual workspace `Cargo.toml` (no root package) has no `package.edition` to infer a resolver from, so the Cargo Book says you must set `resolver` explicitly or Cargo silently falls back to `"1"` — the oldest, weakest feature-unification behavior, with no error or warning. Pick `resolver = "3"` for any new workspace: it's what edition-2024 packages already default to, and it fixes feature unification for dev/build dependencies and matches how each member crate (scaffolded with `cargo new`, which now defaults to edition 2024) already resolves on its own. Only reach for `"2"` if the workspace genuinely has to stay compatible with pre-1.84 Rust.

**Pinning a dependency version.** Plain `cargo add <crate>` always grabs latest-compatible and writes a semver range (e.g. `"1.4"`) — that's the right default. Only add `@<version>` (e.g. `cargo add foo@2`) when you have a specific reason to pin a major version; don't type a version number into `Cargo.toml` by hand either way.

**Commit Cargo.lock or not.** The Cargo team retired the binary-yes/library-no rule in Aug 2023 and now says to do what's best for your project, suggesting committing `Cargo.lock` as a reasonable starting point. `cargo new` stopped auto-`.gitignore`-ing `Cargo.lock` for libraries as of nightly-2023-08-24, so tooling nudges toward committing, but there is no official mandate to commit by default. (Source: https://blog.rust-lang.org/2023/08/29/committing-lockfiles/)

**rustfmt.toml / clippy.toml: write one or skip it.** Neither file is generated by any Cargo command — they're plain hand-written config files that only need to exist if you want non-default formatting or lint behavior. If default style is fine, don't create either file; `cargo fmt` and `cargo clippy` work with zero config present.

**One thing this page does *not* cover: rust-lang/rust's own AGENTS.md rules are scoped to that one repo.** They govern how an agent may touch the *compiler itself* (no LLM-authored diagnostics/docs, soundness-critical code off-limits, use `./x` not bare `cargo`). None of that applies to an ordinary Rust application or library project — don't import those compiler-contribution rules into a general Rust scaffold.

## Never hand-write

- **`Cargo.lock`** — fully machine-generated by `cargo build`/`check`/`add`/`update`. Includes exact pinned versions and checksums. Hand-editing it desyncs it from `Cargo.toml` and corrupts checksum verification.
- **The `target/` directory** — entirely build output from `cargo build`. Never create or edit files inside it.
- **Dependency version strings in `Cargo.toml`** — always add/change these through `cargo add <crate>[@<req>]` or `cargo remove <crate>` (or `cargo update` to bump within existing requirements), never by typing a version number directly into the `[dependencies]` table.
- **The `.gitignore` produced by `cargo new`/`cargo init`** — this file is a starting scaffold, not something to blindly overwrite; if you need to change it, edit it as a normal tracked file, but don't regenerate it by re-running `cargo new` over an existing project.

## Thorough setup checklist

- [ ] Package created with `cargo new <name>` (new directory) or `cargo init` (existing directory) — never a hand-typed `Cargo.toml` from scratch.
- [ ] Every dependency added with `cargo add`, not hand-typed into `[dependencies]`.
- [ ] `Cargo.lock` committed to git (Cargo team's 2023 guidance leaves this to project judgment but suggests committing as a sensible starting point; no longer auto-`.gitignore`d for libraries since nightly-2023-08-24).
- [ ] For multi-crate projects, a workspace root `Cargo.toml` with `[workspace]` and an explicit `resolver = "3"` (required because a virtual manifest has no package edition to infer the resolver from), with each member still scaffolded via `cargo new`/`cargo init`.
- [ ] `rustfmt.toml` (optional — only needed if you want non-default formatting) reviewed/added by hand, and `cargo fmt --check` runs clean.
- [ ] `clippy.toml` (optional) and any `[lints]` / `[workspace.lints]` table in `Cargo.toml` set up by hand, and `cargo clippy` (ideally `--all-targets --all-features`) runs clean.
- [ ] `.gitignore` from the scaffold command is in place and covers `/target`.
- [ ] CI (if present) runs `cargo fmt --check` and `cargo clippy` as separate steps from `cargo test`/`cargo build`, so formatting and lint failures are distinguishable from real bugs.

## Traps

**`cargo clippy --fix` fails on a dirty working tree, no flag, no fix applied.** `cargo fix` (the machinery clippy's `--fix` uses) refuses to touch a repo with uncommitted or staged changes by default — the exact state an agent is normally in mid-scaffold — and errors out instead of doing anything. Pass `--allow-dirty` (or `--allow-staged`, or `--allow-no-vcs` outside a VCS repo) to run it unattended. (Source: https://doc.rust-lang.org/cargo/commands/cargo-fix.html)

**Omitting `resolver` from a virtual workspace doesn't error — it silently downgrades feature unification.** A workspace root `Cargo.toml` with `[workspace]` and no `resolver` field doesn't fail; it quietly falls back to resolver `"1"`, the oldest behavior, with no warning. Since a virtual manifest has no `package.edition` to infer a resolver from, you have to write `resolver = "3"` (or `"2"`) yourself. (Source: https://doc.rust-lang.org/cargo/reference/resolver.html and https://doc.rust-lang.org/cargo/reference/workspaces.html)

**"Don't commit Cargo.lock for libraries" is outdated advice from before August 2023.** The Cargo team retired that old binary-yes/library-no rule in a 2023-08-29 blog post, but didn't replace it with a new blanket default — they now say to do what's best for your project, suggesting committing `Cargo.lock` as a starting point. `cargo new` stopped auto-`.gitignore`-ing it for libraries as of nightly-2023-08-24. Old tutorials/blog posts asserting the old binary-only rule are stale. (Source: https://blog.rust-lang.org/2023/08/29/committing-lockfiles/)

**There is no first-party Rust docs-retrieval channel for agents.** `doc.rust-lang.org/llms.txt` returns 404, and there's no official rust-lang MCP server for Cargo/Rust API docs — only community projects (docs.rs MCP wrappers, rustdoc-llms, etc.) fill that gap. Don't assume an agent can pull current Rust docs through an official feed; it has to use web fetches or third-party tooling. (Source: direct fetch of https://doc.rust-lang.org/llms.txt, 404; web search turned up only community projects, no official rust-lang MCP server)

**The rust-lang/rust LLM policy is brand-new and scoped to five teams, not the whole language ecosystem.** It was adopted August 5, 2026 — about two weeks before this audit — by five teams inside the rust-lang/rust monorepo specifically, explicitly framed by its authors as a first step for gathering data rather than a settled, mature policy. Treat AGENTS.md/the Forge policy as compiler-repo-specific and still evolving, not as general Rust community consensus. (Source: https://blog.rust-lang.org/inside-rust/2026/08/05/rust-langrust-is-adopting-an-llm-policy/)

**`cargo new` on an existing directory hard-refuses instead of clobbering anything.** It doesn't overwrite hand-written `src/main.rs`/`src/lib.rs` — it errors out with "destination already exists" before writing a single file, the moment the target directory exists, empty or not. Use `cargo init` for a directory that already exists instead of retrying `cargo new` with force or workarounds. (Source: rust-lang/cargo source, `src/ops/cargo_new.rs`, function `new`: https://github.com/rust-lang/cargo/blob/master/src/ops/cargo_new.rs)

**Hand-typing a `Cargo.toml` `[dependencies]` block with a guessed or remembered version number instead of running `cargo add`.** This risks a version that doesn't exist, is yanked, or is already outdated. (Source: https://doc.rust-lang.org/cargo/commands/cargo-add.html)

**Hand-editing `Cargo.lock` to "fix" a version conflict.** Run `cargo update -p <crate>` or adjust the `Cargo.toml` requirement and let Cargo regenerate the lock instead. (Source: https://doc.rust-lang.org/cargo/commands/cargo-update.html)

**Skipping `cargo fmt`/`cargo clippy` entirely and hand-formatting code to "look right."** The formatter and linter are the actual source of truth for style in a Rust repo — trust them instead of eyeballing it. (Source: https://rust-lang.github.io/rustfmt/ and https://doc.rust-lang.org/clippy/usage.html)

## AI and agent resources

Rust does not (as of August 2026) publish an `llms.txt`/`llms-full.txt` on doc.rust-lang.org (checked directly — not there), and there is no official rust-lang MCP server (the docs.rs/rust-analyzer MCP servers you'll find online are community projects, not first-party). What Rust does have, first-party, is centered on governing *how* agents may contribute to the compiler itself, not on feeding docs into context:

- **`AGENTS.md` in rust-lang/rust** ([link](https://github.com/rust-lang/rust/blob/master/AGENTS.md)) — a real, detailed instruction file at the repo root for any coding agent working on the compiler. It sets hard rules: no LLM-authored PR descriptions, docs, or diagnostics; a failing test must exist and be observed before any fix; soundness-critical code (type checking, MIR, borrow checking, codegen) is off-limits for LLM implementation; use `./x`, not bare `cargo`, as the build entrypoint. An agent should read this before making any change in that repo and stop at the gates it defines.
- **Official LLM usage policy** ([forge.rust-lang.org/policies/llm-usage.html](https://forge.rust-lang.org/policies/llm-usage.html)) — the project-wide rule set the AGENTS.md file points back to: LLMs are fine for private review, distillation, and discovery that a human verifies, but not as the author of anything posted publicly, and code changes need pre-arranged reviewer sign-off plus disclosure.
- **rustc dev guide's LLM guidance page** ([rustc-dev-guide.rust-lang.org/llm-guidance.html](https://rustc-dev-guide.rust-lang.org/llm-guidance.html)) — the practical companion piece ("Writing LLM-created code" / "Reviewing with LLMs") for contributors and agents.

Net effect: Rust's official agent-facing surface is a compliance/permission layer for the rust-lang/rust repo, not a docs-retrieval layer. If you want an agent to pull current Cargo/Rust API docs into context, there's no official channel for that yet — that gap is currently filled only by third-party MCP servers, which fall outside what this section covers.
