<!-- freshness verified=2026-08-21 baseline=2026-08-30 -->
# Swift Scaffolding Reference (verified 2026-08-21, round 3)

## Official setup commands

**Create a new Swift package** — run this instead of writing `Package.swift`, `Sources/`, and `Tests/` by hand:

```
swift package init --type <TYPE> [--name <PackageName>]
```

`--name` is optional; if you leave it out, the package takes the name of the folder you're in.

The `--type` values, straight from the Swift Package Manager's own docs:

| `--type` value | What it makes |
|---|---|
| `library` (default template) | A library package: `Package.swift`, `Sources/<Target>/`, `Tests/<Target>Tests/` |
| `executable` | A runnable command-line program, no argument parsing library attached |
| `tool` | A runnable command-line program pre-wired with Apple's `swift-argument-parser`. Use this one, not `executable`, if the tool needs real flags/options |
| `macro` | A Swift macro package: the macro target, a client executable that uses it, and a macro test target |
| `build-tool-plugin` | A package that vends a build tool plugin |
| `command-plugin` | A package that vends a command plugin |
| `empty` | Just a bare `Package.swift` manifest, nothing else |

**Build and test the freshly scaffolded package** (confirms the template actually compiles before you touch it):

```
swift build
swift test
```

**Generate a swift-format config** instead of typing one from memory:

```
swift-format dump-configuration > .swift-format
```

On Xcode 16 / Swift 6 toolchains, swift-format ships inside the toolchain and the same thing is spelled with a space:

```
swift format dump-configuration > .swift-format
```

Then hand-edit only the specific rules you want to change — the dump is your starting point, not your enemy.

**Create an Xcode app project — humans at a terminal, not agents.** There is no CLI or MCP-bridge tool that creates a new Xcode project unattended: the bridge's documented tool set (build/test/read/write/preview/doc-search) has no project-creation tool. A person has to open Xcode and use File → New → Project — that's the "official generator" for an app target here, same as `swift package init` is for a package. Don't hand-build a `.xcodeproj` bundle as text. Once the project exists and is open, an agent can connect to it through Xcode's MCP bridge (see AI and agent resources below) for everything else — build, test, read, write, preview, doc search — just not creation itself.

## Choosing

**`--type executable` vs `--type tool`.** Both make a runnable command-line program. Pick `tool` the moment the program takes real flags or options (`--verbose`, `--output <path>`, subcommands) — it comes pre-wired with Apple's swift-argument-parser, which is also what Apple's own docs point to for argument parsing. Pick `executable` only for something with no arguments to parse (a script-like entry point). If you start with `executable` and later need flags, you end up hand-adding swift-argument-parser yourself — just start with `tool`.

**A Swift package vs an Xcode app project.** `swift package init` is for things without an app UI: libraries, command-line tools, macros, build/command plugins. The moment you need a UI (SwiftUI/UIKit views, an app icon, entitlements, App Store distribution, asset catalogs), that's an Xcode App target, created through File → New → Project — there is no SwiftPM `--type` for "app." Many real projects are both: an Xcode app project that depends on one or more local SwiftPM packages for the non-UI logic.

**Standalone `swift-format` vs the toolchain-bundled `swift format`.** If the project's Swift tools version is on Xcode 16 / Swift 6 or newer, the formatter ships inside the toolchain already — use the space-separated `swift format` and skip adding a dependency. On an older toolchain, add `apple/swift-format` as a package dependency (or install the standalone binary) and use the hyphenated `swift-format` instead. Don't assume one spelling works everywhere; it depends on the toolchain version in use.

**Xcode's built-in agent vs an external agent over the MCP bridge.** If you're working inside Xcode's own Intelligence pane, its built-in Claude/ChatGPT integration and `/`-slash-command skills (like `/plan`) need no setup — use those. If you're driving from a terminal-first agent like Claude Code or Codex instead, connect it to Xcode via `xcrun mcpbridge` so it can build, test, and search docs against the real project graph instead of shelling out to `xcodebuild` and screen-scraping output. The bridge requires the project already open in Xcode and the "Allow external agents to use Xcode tools" toggle turned on first — it's not a way to create or open a project.

**Committing `Package.resolved` or not.** For an app (or any product that ships/deploys), commit it, at `<App>.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved` for an Xcode app or at the package root for a standalone package — this pins dependency versions so builds are reproducible. Libraries meant to be consumed by other packages commonly leave it out, since the consuming package resolves its own versions anyway.

## Never hand-write

These are machine-generated. Don't type them from scratch, and don't hand-edit their internals as raw text:

- `Package.resolved` — anywhere it appears (root of an SPM package, or inside `<App>.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved` for an Xcode app). It's written by `swift package resolve`, `swift build`, `swift package update`, or by Xcode resolving dependencies.
- `*.xcodeproj/` internals — `project.pbxproj`, `contents.xcworkspacedata`, `xcshareddata/`, `xcuserdata/`. Only ever touch these through Xcode's UI (or `xcodebuild`), never as raw text.
- `*.xcworkspace/` internals — same rule, Xcode owns these files.
- `.build/` — SwiftPM's build output and dependency checkouts. Never commit it, never hand-edit it.
- `DerivedData/` — Xcode's build cache. Always regenerated, never committed.
- The initial `Sources/<Target>/` and `Tests/<Target>Tests/` skeleton that `swift package init` writes — get it from the command once, then edit it like normal source code afterward.
- `xcuserdata/*.xcuserdatad/` — per-developer breakpoints, scheme state, window layout. Never commit, never hand-edit.

## Thorough setup checklist

- [ ] Package scaffolded with `swift package init --type <TYPE>`, matching the actual kind of project (used `tool` instead of `executable` if it takes real CLI arguments)
- [ ] `swift build` and `swift test` both run clean on the untouched template before adding any feature code
- [ ] `Package.swift` opened and edited by hand afterward for real settings — platforms, dependencies, products. This file is meant to be hand-maintained once it exists; that's different from hand-*writing* it from nothing
- [ ] `.swift-format` created via `swift-format dump-configuration` (or `swift format dump-configuration`), not typed free-hand
- [ ] For an app target: the Xcode project itself came from File → New → Project, not from someone assembling `.xcodeproj` XML by hand
- [ ] `.gitignore` based on GitHub's official `Swift.gitignore` template — its active patterns are `xcuserdata/`, `*.hmap`, `*.ipa`, `*.dSYM.zip`, `*.dSYM`, `timeline.xctimeline`, `playground.xcworkspace`, `.build/`, `Carthage/Build/`, and the `fastlane/*` lines (`*.xcuserstate` is only caught because it lives inside `xcuserdata/`, not as its own line). Not retyped from memory. The template does **not** cover `DerivedData/` — add that line yourself (see Traps) ([github/gitignore Swift.gitignore](https://raw.githubusercontent.com/github/gitignore/main/Swift.gitignore), fetched 2026-08-21)
- [ ] For an Xcode app with package dependencies: `Package.resolved` is committed (at `<App>.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved`), while the rest of `xcuserdata/` and per-user `xcshareddata` stay out of git
- [ ] Any stricter or repo-specific formatting rules are layered on top of the dumped `.swift-format` defaults, not built from scratch

## Traps

**A scaffold guard can false-positive on `swift-format dump-configuration > .swift-format`.** The command is the correct, non-interactive way to generate a starting config, but a naive "never hand-write generated files" guard can mistake the shell redirect (`>`) for the agent hand-writing the config itself, and block the official generator. Fix the guard's heuristic to recognize a redirect from an approved generator command, not the command (recorded test run 2026-08-21).

**GitHub's official Swift.gitignore template does not exclude `DerivedData/`.** Neither `Swift.gitignore` nor the `Global/Xcode.gitignore` it's built from has a `DerivedData/` line — if a project's build system setting points DerivedData in-tree (common in some CI setups), pulling the stock template and stopping there lets Xcode's build cache get committed, which is exactly the mistake this same page warns against elsewhere. Add `DerivedData/` by hand on top of the template (source: [github/gitignore Swift.gitignore](https://raw.githubusercontent.com/github/gitignore/main/Swift.gitignore), fetched 2026-08-21).

**The Xcode MCP bridge only works with the project already open in Xcode.** Apple's own docs are explicit: "Before prompting an external agent (outside of Xcode), be sure to open your project in Xcode." Registering the bridge (`claude mcp add --transport stdio xcode -- xcrun mcpbridge`) and then immediately calling a build/test tool with no project open in the Xcode UI, or before the human has flipped "Allow external agents to use Xcode tools" in Settings > Intelligence, fails — and neither prerequisite can be satisfied by the agent itself (source: [Giving external agents access to Xcode](https://developer.apple.com/documentation/xcode/giving-external-agents-access-to-xcode.md)).

**Hand-typing `Package.swift` from a remembered template** instead of running `swift package init --type library` (or whatever type fits) and editing the real output. Memory-written manifests drift from what the current SwiftPM version actually expects (target syntax, platform syntax, and available manifest APIs change across Swift tool versions) (source: [swift package init reference](https://docs.swift.org/swiftpm/documentation/packagemanagerdocs/packageinit/)).

**Using `--type executable` for a CLI tool that takes flags**, then hand-adding `swift-argument-parser` wiring that `--type tool` would have set up correctly the first time (source: [swift package init reference](https://docs.swift.org/swiftpm/documentation/packagemanagerdocs/packageinit/)).

**Deleting or ignoring `Package.resolved`** for an app project because it "looks auto-generated" — for apps and CI-built products it should be committed so builds are reproducible; only libraries commonly leave it out (source: [Adding package dependencies to your app](https://developer.apple.com/documentation/xcode/adding-package-dependencies-to-your-app)).

**Writing a `.swift-format` JSON config from memory** instead of running `swift-format dump-configuration` first, which produces keys/values that actually match the installed tool version — hand-written configs commonly use stale or renamed rule names (source: [apple/swift-format README](https://github.com/apple/swift-format), dump-configuration section).

**Skipping `swift build`/`swift test` right after scaffolding**, so a broken template (wrong Swift tools version, missing platform minimum) isn't caught until much later, mixed in with real feature work (source: [Swift.org — Building a Swift package](https://www.swift.org/getting-started/library-swiftpm/), which runs `swift test` immediately after `swift package init`).

## AI and agent resources

Apple's official AI/agent-facing surface for Swift is built into Xcode 26.3+, not a separate llms.txt or standalone Apple MCP server. There is no official `llms.txt` at swift.org (it came up as a community forum suggestion, not a shipped feature) and no first-party "Apple Developer Docs" MCP server — the ones you'll find under that name are third-party community projects, not Apple's.

- **Xcode's built-in MCP bridge (`xcrun mcpbridge`).** As of Xcode 26.3, Xcode itself is an MCP server. Turn on "Allow external agents to use Xcode tools" in Xcode > Settings > Intelligence, open your project, then register the bridge with your agent — for example `claude mcp add --transport stdio xcode -- xcrun mcpbridge`. Reach for this whenever an agent working on a Swift/iOS/macOS project needs to build a scheme, run tests, or search Apple's documentation and get structured results back, instead of shelling out to `xcodebuild` and parsing text output.
- **AGENTS.md / CLAUDE.md at the project root.** Apple's own docs point developers here for giving any connected agent context about the project. Keep this current in every Swift repo an agent touches — Xcode's built-in agents and anything connected through the MCP bridge both read it.
- **Xcode's built-in agent skills and slash commands.** Type `/` in Xcode's coding-assistant prompt field to see built-in skills (e.g. `/plan` for a plan-only mode, automatic localization subagents). Use these instead of re-deriving a workflow by hand when working inside Xcode. Project-specific plug-ins (installed via Intelligence settings > Agents > Plug-ins) can add more subagents, MCP servers, and skills scoped to that project.
- **Agent Client Protocol (ACP) and Chat-Completions-API support.** Xcode's Intelligence settings accept any ACP-compatible agent ("Add an Agent") and any model provider that serves the standard `/v1/models` and `/v1/chat/completions` endpoints (self-hosted or third-party), on top of built-in Claude and ChatGPT integrations. Use this if you're wiring up an agent or model that isn't one of Apple's built-ins.
- **Swift-DocC Markdown output (`--enable-experimental-markdown-output`, shipped in Swift 6.3).** Running `docc convert` with this flag emits a plain-Markdown `.md` file next to every page in a documentation archive (plus an optional manifest with `--enable-experimental-markdown-output-manifest`). If you maintain a Swift package's docs, turn this on so agents fetching your documentation get readable Markdown instead of JS-rendered JSON.
