<!-- freshness verified=2026-08-30 baseline=2026-08-30 -->
# shipping-desktop-mobile scaffold-first reference (researched 2026-08-30)

Covers turning a scaffolded app into a **distributable**: installers for Electron and Tauri, a device build for Flutter iOS, and where the Apple credential gate sits. Everything before the gate was run live this session (macOS arm64): a real Tauri `.dmg` + `.app`, a real Electron Forge `.zip` distributable, and an unsigned Flutter iOS `Runner.app` — each produced app verified to actually launch from its bundle. Signing and notarization themselves need Christopher's Apple Developer account and are exercised in a supervised session (he does the credential steps; agents never see credentials).

## What was built and proven (all run live)

| Stack | Command | Artifact observed | Launch check |
|---|---|---|---|
| Tauri | `npm run tauri build` | `src-tauri/target/release/bundle/dmg/<app>_0.1.0_aarch64.dmg` and `.../macos/<app>.app` | release binary ran (process alive) |
| Electron (Forge 8) | `npm run make` | `out/make/zip/darwin/arm64/<app>-darwin-arm64-<v>.zip` | app from the zip ran |
| Flutter iOS | `flutter build ios --no-codesign` | `build/ios/iphoneos/Runner.app` (14.4MB) | n/a — device artifact, gate next |

Both desktop apps come out **ad-hoc signed** (`codesign -dv` → `Signature=adhoc`): they run locally but Gatekeeper will block them on anyone else's Mac. Ad-hoc is the automatic no-identity state, not a choice.

## The credential gate (what needs the Apple Developer account)

1. **macOS distribution**: a "Developer ID Application" certificate in the keychain → sign (Tauri: `APPLE_SIGNING_IDENTITY` env; Forge: osx-sign config) → notarize (`xcrun notarytool submit --keychain-profile <name> --wait`) → staple. The keychain profile is created ONCE by Christopher interactively (`xcrun notarytool store-credentials`) — after that agents can drive builds against the stored profile without ever seeing the secret.
2. **iOS distribution**: an Apple Distribution certificate + provisioning profile via Xcode signed in to the team → `flutter build ipa` (or `xcodebuild -exportArchive`) → upload via Transporter/`altool` successor.
3. Windows/Linux installers: not exercised — this machine builds darwin/arm64 only; cross-platform packaging is CI work, surface it as a gap.

Agents: never type Apple credentials, never create certificates, never click through Xcode sign-in. Ask Christopher to do the one-time steps, then use the stored profile.

## Never hand-write

- `Info.plist` inside built bundles, embedded provisioning profiles, entitlements derived files — the build tools own them; author entitlements only in the project source (`src-tauri/`, forge config, Xcode target settings).
- Forge maker configs from scratch — start from the generated `forge.config.*` maker list and extend (the zip maker is the macOS default; the dmg maker is an add-on package).

## Thorough shipping checklist

1. Build the unsigned artifact FIRST (commands above) and launch it — packaging bugs surface here, before any signing complexity.
2. Check the signature state explicitly: `codesign -dv <app>` — expect `adhoc` pre-gate, a Developer ID authority post-gate; `spctl -a -vv <app>` tells you what Gatekeeper will do.
3. Desktop: version numbers come from `tauri.conf.json` / `package.json` — bump there, never rename artifacts.
4. iOS: keep `--no-codesign` builds in CI for early breakage detection; the signed `build ipa` only works after the gate.
5. Licensing before first public artifact — see `references/licensing.md`.

## Traps

**`codesign -s <identity>` from an agent session hangs on a keychain dialog.** This machine has two "Apple Development" identities, and signing with one blocked indefinitely — macOS pops a keychain-access prompt only the user can click, and a headless run just hangs (the app stayed ad-hoc). Even pre-notarization signing is an interactive step until Christopher grants codesign keychain access (clicking "Always Allow" on that dialog once). Also note: Apple Development certs sign local/dev builds only — distribution needs a "Developer ID Application" cert, which this keychain does not have yet. (Observed 2026-08-30.)

**Electron Forge 7's `make`/`package` are broken on Node 26** (silent empty output) — the Forge 8 alpha fix from `electron.md` Traps applies to shipping too; this session's working `make` ran on Forge 8. (Observed 2026-08-30.)

**Tauri's release build recompiles everything from zero** — several minutes even after a dev build, because release is a separate cargo profile. Background it. (Observed 2026-08-30.)

**An ad-hoc app "works on my machine" — that's the trap.** It launches fine locally, so nothing looks wrong until another Mac quarantines it. Always read `codesign -dv` output before calling an artifact shippable. (Observed 2026-08-30.)

## AI and agent resources

- `xcrun notarytool --help` and `man codesign` are local and current — trust them over memory.
- Tauri's signing docs and Forge's osx-sign/osx-notarize docs via Context7 when the gate work starts.
