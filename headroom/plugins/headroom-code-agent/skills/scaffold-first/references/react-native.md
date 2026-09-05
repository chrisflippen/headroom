<!-- freshness verified=2026-08-30 baseline=2026-08-30 -->
<!-- probe: react-native | npm view react-native version | 0.87.1 -->
<!-- probe: @react-native-community/cli | npm view @react-native-community/cli version | 20.2.0 -->
# react-native scaffold-first reference (researched 2026-08-30)

Covers **bare React Native** via `@react-native-community/cli`. First, the ruling that matters: **React Native's own docs recommend starting with a framework — which means Expo — and Expo is already the house-verified RN path** (see `references/js-web-extended.md` for `create-expo-app`, verified 2026-08-21). Reach for bare RN only when the project must own its native projects directly (custom native code day one, an out-of-tree platform, or a hard no-Expo constraint). This page verifies that bare path live on this machine (macOS arm64, Node v26.7.0, beta Xcode 27 via `DEVELOPER_DIR`): scaffolded, built, booted on an iPhone 17 simulator with the welcome screen rendered (screenshot inspected), scaffold test passing.

## Official setup commands

| Step | Command | What it generates / does |
|---|---|---|
| Scaffold | `npx @react-native-community/cli@latest init <Name> --install-pods true` | `App.tsx`, `index.js`, `__tests__/`, `android/`, `ios/` (with `Podfile`, `Gemfile` for the vendored CocoaPods), `app.json`, `babel.config.js`, `metro.config.js`, `jest.config.js`, `tsconfig.json`, a git repo — and it attempts `pod install` (see Traps: that failed here and needed a locale fix) |
| Useful flags | `--pm npm\|yarn\|bun`, `--package-name com.example.app`, `--skip-install`, `--skip-git-init`, `--directory`, `--template <npm-pkg>` | from the CLI's own `--help`, run live |
| Repair pods | `LANG=en_US.UTF-8 bundle exec pod install --project-directory=ios` | writes `ios/Pods/`, `ios/Podfile.lock`, and the `.xcworkspace` |
| Run on a simulator | `npx react-native run-ios --udid <udid>` | builds with xcodebuild, installs, launches |
| Start the JS bundler | `npx react-native start` | Metro on :8081 — REQUIRED for a debug build to show anything (Traps) |
| Test | `npm test` | the scaffold's jest render test |

Verified versions at this check: `@react-native-community/cli@20.2.0`, `react-native@0.87.1` (Hermes engine, confirmed on the rendered welcome screen).

## Never hand-write

- `ios/Podfile.lock`, `ios/Pods/`, the `.xcworkspace` — CocoaPods-owned; regenerate with `pod install`.
- `Gemfile.lock`, `package-lock.json` — machine-owned lockfiles.
- The `android/` and `ios/` project skeletons — scaffold-owned; native edits go in the designated files (e.g. `AppDelegate`, `MainActivity`), never by rebuilding the tree from memory.
- `metro.config.js` / `babel.config.js` / `jest.config.js` skeletons — generated; extend the exported config, don't retype it.

## Thorough setup checklist

1. Default to Expo (`references/js-web-extended.md`) unless a concrete constraint demands bare RN — record that constraint in the README when it does.
2. Scaffold with `--package-name` decided up front (bundle id / Android package in one flag).
3. Confirm `ios/Pods` and the `.xcworkspace` actually exist before calling setup done — init can exit 0 with pods broken (Traps).
4. Boot check: Metro running (`curl -s localhost:8081/status` → `packager-status:running`), then `run-ios`, then `xcrun simctl io <udid> screenshot` and look at it.
5. `npm test` on the untouched scaffold must pass.
6. ESLint/Prettier come preconfigured (`.eslintrc.js`, `.prettierrc.js` in the scaffold); wire them plus `npm test` into CI.
7. Android needs the Android SDK — not present on this machine; surface as a gap rather than half-installing it.

## Traps

**`init` can report success while CocoaPods failed — check for `ios/Pods`.** Observed live: the vendored `pod install` (CocoaPods 1.15.2 under Homebrew Ruby 4.0) crashed with `Unicode Normalization not appropriate for ASCII-8BIT (Encoding::CompatibilityError)` — the CLI even printed the hint (`CocoaPods requires your terminal to be using UTF-8 encoding`) — yet `init` exited 0 and printed cheerful run instructions. The fix that worked: `export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8`, then `bundle exec pod install --project-directory=ios` — same vendored CocoaPods, clean install, workspace created. Set the locale BEFORE the first `init` and this never bites. (Recorded run: 2026-08-30.)

**A debug build with no Metro shows a red error screen, not your app.** `run-ios` said "Successfully launched the app," and the simulator showed "No script URL provided. Make sure the packager is running." Metro normally auto-opens in a new terminal window — in a headless/background session it doesn't. Start it yourself (`npx react-native start`, confirm `curl localhost:8081/status`), then relaunch the app (`xcrun simctl terminate` + `simctl launch` with the bundle id, `org.reactjs.native.example.<Name>` by default). (Recorded run: 2026-08-30.)

**Xcode 27 BETA has no `Simulator.app`, and `run-ios` dies on a `devices://` URL if nothing claims that scheme.** Observed: `kLSApplicationNotFoundErr` for `devices://device/open?id=<udid>` before any build started. The Xcode 27.0 beta replaced Simulator.app with **DeviceHub.app** (`/Applications/Xcode*.app/Contents/Applications/DeviceHub.app`). `open` that app once, then `run-ios` proceeds. Scope: this is a beta-27 observation — release Xcode 26.6 on the same machine still ships `Contents/Applications/Simulator.app` (checked by ls, 2026-08-30). A properly `xcode-select`ed full Xcode registers the scheme system-wide; the `DEVELOPER_DIR` override alone does not. (Recorded run: 2026-08-30, Xcode 27.0 beta.)

**Beta-Xcode machines: `DEVELOPER_DIR` substitutes for `sudo xcode-select` for builds** — same workaround as Flutter, see `references/flutter.md` Traps. It covered xcodebuild, simctl, and pod install here. (As with Flutter's note: a full release Xcode was installed and selected on this machine later the same day, so the override is now the beta-only fallback, not this machine's state.)

## AI and agent resources

- `https://reactnative.dev/llms.txt` was not verified this session — resolve RN doc questions through Context7 or reactnative.dev directly; the flag table above came from running the CLI's own `--help`.
- Expo's agent resources (llms.txt with stale-training corrections, official MCP server, Claude Code plugin) are documented in `references/js-web-extended.md` and apply to the recommended RN path.
