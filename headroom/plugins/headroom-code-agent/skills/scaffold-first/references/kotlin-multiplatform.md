<!-- freshness verified=2026-08-30 baseline=2026-08-30 -->
<!-- probe: JetBrains/kotlin | curl -s https://api.github.com/repos/JetBrains/kotlin/releases/latest | python3 -c "import sys,json;print(json.load(sys.stdin)['tag_name'])" | v2.4.10 -->
# kotlin-multiplatform scaffold-first reference (researched 2026-08-30)

Covers **Kotlin Multiplatform (KMP)** with **Compose Multiplatform** — JetBrains' route to sharing Kotlin logic (and optionally UI) across Android, iOS, desktop (JVM), and web. Everything below was run live on this machine (macOS arm64, system JDK 25): project generated from JetBrains' own wizard via its HTTP endpoint, the Compose Desktop app built and launched to a real window, and the scaffold's own shared-logic test executed.

## The official scaffold — a web wizard with a curl-able endpoint

JetBrains ships NO CLI scaffolder for KMP. The official paths are the IntelliJ IDEA / Android Studio project wizard and the web wizard at `kmp.jetbrains.com`. The web wizard is scriptable: its Download button issues a plain GET (captured live from the wizard's own network traffic, then re-fetched with curl — a valid zip both times):

```bash
curl -sL -o kmp.zip 'https://kmp.jetbrains.com/generateKmtProject?name=KotlinProject&id=org.example.project&spec=%7B%22template_id%22%3A%22kmt%22%2C%22targets%22%3A%7B%22android%22%3A%7B%22ui%22%3A%5B%22compose%22%5D%7D%2C%22ios%22%3A%7B%22ui%22%3A%5B%22compose%22%5D%7D%2C%22desktop%22%3A%7B%22ui%22%3A%5B%22compose%22%5D%7D%7D%2C%22include_tests%22%3Atrue%7D&locale=en-us'
unzip -q kmp.zip
```

The `spec` parameter is URL-encoded JSON: `{"template_id":"kmt","targets":{"android":{"ui":["compose"]},"ios":{"ui":["compose"]},"desktop":{"ui":["compose"]}},"include_tests":true}`. Adjust `name`, `id`, and the target set (the wizard also offers `web` and `server`; iOS with native UI instead of Compose is `"ios":{"ui":[]}` — regenerate through the wizard UI and re-capture if unsure rather than guessing spec syntax). This endpoint is observed behavior, not documented API — if it 404s someday, fall back to the wizard in a browser; do not hand-assemble the Gradle files.

Generated layout (observed): `shared/` (common logic + per-target source sets, with tests when `include_tests` is on), `androidApp/`, `iosApp/` (an Xcode project consuming `shared` as a framework), `desktopApp/` (Compose for Desktop), Gradle wrapper (`gradlew`), `build.gradle.kts`, `settings.gradle.kts`, `gradle/libs.versions.toml`. The zip nests everything under a `<name>/` directory.

## Build, boot, and test (what was verified)

| Step | Command | Observed |
|---|---|---|
| Launch the desktop app | `./gradlew :desktopApp:run` | first run downloads Gradle + a JDK toolchain + Compose deps (minutes), then a real 800×600 window titled "KotlinProject" opened (verified via the macOS window list: process `java`, window `KotlinProject`) |
| Run shared-module tests | `./gradlew :shared:jvmTest` | BUILD SUCCESSFUL — the wizard's own `SharedLogicDesktopTest` passed |
| List what exists | `./gradlew :shared:tasks` | the JVM target's tasks are named `jvm*`; iOS produces `iosArm64*` / `iosSimulatorArm64*` binaries |

`chmod +x gradlew` first — the zip does not preserve the execute bit (hit live).

Not verified this session (known gaps, don't improvise): the Android app build (no Android SDK on this machine), the iOS app (an Xcode-project build via the generated `iosApp`), and the web target.

## Never hand-write

- `gradlew`, `gradlew.bat`, `gradle/wrapper/` — Gradle wrapper is machine-owned; upgrade with `./gradlew wrapper --gradle-version <v>`, never by editing.
- The generated `build.gradle.kts` / `settings.gradle.kts` skeletons and `gradle/libs.versions.toml` — versions catalog entries get updated deliberately (Renovate handles them), not retyped from memory.
- `local.properties` — machine-local paths, git-ignored, regenerated per machine.
- The `iosApp` Xcode project structure — owned by the wizard/Xcode, same rule as every `.pbxproj`.

## Thorough setup checklist

1. Decide the sharing tier first — JetBrains documents three: share logic + UI (Compose everywhere), share logic only (native SwiftUI/Compose per platform), or share a small slice. The wizard's iOS "Share UI / Do not share UI" choice encodes this and it's structural — pick before generating.
2. Generate via the endpoint above with real `name`/`id`; unzip; `chmod +x gradlew`; `git init` yourself (no repo in the zip).
3. Boot the desktop target once and run `:shared:jvmTest` before feature work — desktop is the cheapest full loop and needs only a JDK.
4. Android target needs the Android SDK; iOS target needs Xcode. Surface missing ones as gaps.
5. Gradle provisions its own JDK toolchain (observed: it downloaded Azul Zulu 21 despite system JDK 25) — don't fight it; the system JDK only has to be good enough to start Gradle.
6. CI runs `./gradlew build` (which compiles and tests all wired targets) from the committed wrapper.

## Traps

**There is no `desktopTest` task even though the module is `desktopApp`.** The shared module's JVM target is named `jvm`, so the test task is `:shared:jvmTest`; `:shared:desktopTest` fails with BUILD FAILED. List tasks before guessing names. (Recorded run: 2026-08-30.)

**The first `./gradlew` invocation downloads the world silently-ish.** Gradle distribution + JDK toolchain + Kotlin/Compose dependencies — minutes with sparse output. Background it and poll the log; don't conclude it hung. (Recorded run: 2026-08-30.)

**The Compose Desktop window belongs to a process named `java`, not your project name.** Window-level checks must look for the window title (set in `desktopApp`'s `main.kt`, "KotlinProject" in the template), not the process name. Its macOS accessibility tree is also nearly opaque (a few AXButtons and an AXGroup) — verify logic with `:shared:jvmTest`, not GUI scraping. (Recorded run: 2026-08-30.)

**The zip loses the execute bit on `gradlew`.** `./gradlew` → permission denied until `chmod +x gradlew`. (Recorded run: 2026-08-30.)

## AI and agent resources

- No `llms.txt` checked as existing for kotlinlang.org this session — resolve KMP doc questions through Context7 (`/jetbrains/kotlin-multiplatform-dev-docs` was used to confirm the wizard-or-IDE scaffold story and the module layout).
- The wizard also has a Templates Gallery tab (`kmp.jetbrains.com`) with more app shapes — vet any of them like every third-party template: generate, grade, then admit (see `references/template-catalog.md`).
