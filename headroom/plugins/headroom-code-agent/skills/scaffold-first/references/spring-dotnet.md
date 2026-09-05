<!-- freshness verified=2026-08-30 baseline=2026-08-30 -->
<!-- probe: spring-boot | curl -s -H 'Accept: application/json' https://start.spring.io/metadata/client | python3 -c "import sys,json;print(json.load(sys.stdin)['bootVersion']['default'])" | 4.1.1.RELEASE -->
<!-- probe: dotnet-lts | curl -s https://builds.dotnet.microsoft.com/dotnet/release-metadata/releases-index.json | python3 -c "import sys,json;print([r['latest-sdk'] for r in json.load(sys.stdin)['releases-index'] if r.get('release-type')=='lts' and r.get('support-phase')=='active'][0])" | 10.0.400 -->
# spring-dotnet scaffold-first reference (researched 2026-08-30)

Covers the enterprise lanes: **Spring Boot** (Java) and **ASP.NET Core** (C#). Both verified live this session (macOS arm64): Spring Boot 4.1.1 generated from the Initializr API, built and tested on the machine's Temurin 25 LTS JDK, Tomcat booted and answering; .NET SDK 10 (LTS) installed from Microsoft's official script, web API scaffolded, booted, endpoint returning JSON, xunit test project passing.

## Toolchains (resolved live)

- **JDK**: Temurin 25 (LTS) was already at `/Library/Java/JavaVirtualMachines`; `/usr/libexec/java_home` finds it. Gradle also auto-provisions toolchains (seen in `kotlin-multiplatform.md`), so a missing JDK is rarely a blocker for Gradle projects.
- **.NET**: `curl -sL https://dot.net/v1/dotnet-install.sh | bash -s -- --channel LTS --install-dir <dir>` — Microsoft's official install script, no sudo, installs a self-contained SDK (10.0.400 observed). Set `DOTNET_ROOT=<dir>` and put it on PATH. `DOTNET_CLI_TELEMETRY_OPTOUT=1` for agents.

## Official setup commands (all run live)

| Step | Command | Notes |
|---|---|---|
| Spring scaffold | `curl -s https://start.spring.io/starter.zip -d type=gradle-project -d language=java -d javaVersion=25 -d dependencies=web -d name=<app> -d artifactId=<app> -o app.zip && unzip -q app.zip -d <dir>` | The Initializr REST API is the scriptable official scaffolder — the same engine as the website. Query `https://start.spring.io/metadata/client` (Accept: application/json) for current versions and dependency ids instead of guessing. |
| Spring build/test/run | `chmod +x gradlew && ./gradlew test` / `./gradlew bootRun` | first build downloads Gradle + deps (~minutes); the zip loses gradlew's exec bit — same trap as KMP |
| .NET scaffold | `dotnet new webapi -o <dir>` | minimal-API template; `dotnet new list` shows the full template catalog (webapi, blazor, console, classlib, xunit, mstest, ...) |
| .NET run/test | `dotnet run` / `dotnet test` | test lives in its own project: `dotnet new xunit -o <dir>.Tests` |

Boot checks that worked here: Spring `./gradlew bootRun` → Tomcat on :8080 — a BARE web starter has no routes, so `/` returns the whitelabel 404; the 404 page IS proof the server answers (add a controller before expecting 200s). .NET `dotnet run` → `/weatherforecast` returned the sample JSON on the port printed in the run output. Tests: Spring's generated context-loads test passed (`./gradlew test`); a fresh xunit project passed (`dotnet test`).

## Stale-training corrections (verified live)

- **Spring Boot 4 is current** (4.1.1 is the Initializr default) — training-era "Spring Boot 3.x" is a major version behind; expect API and namespace movement, verify against current docs.
- **.NET 10 is the active LTS** — and `dotnet new webapi` scaffolds the minimal-API style, not the MVC-controller style of old templates.
- **Initializr's Java default is still 17** — pin `javaVersion` explicitly (25 here) or you silently target an old language level.

## Never hand-write

- The Gradle wrapper (`gradlew`, `gradle/wrapper/*`) — Initializr/Gradle own it; regenerate with `gradle wrapper`.
- `.csproj` package versions — add packages with `dotnet add package <id>`, never hand-type versions.
- `build.gradle` dependency coordinates — copy them from Initializr metadata or Spring docs, and prefer re-generating from Initializr when adding starters early in a project's life.

## Thorough setup checklist

1. Resolve versions live: Initializr metadata for Spring; the dotnet releases-index JSON (this page's probe) for .NET.
2. Scaffold via the API/CLI as above; never hand-assemble a Gradle or MSBuild skeleton.
3. Boot check with the caveats above (Spring bare starter 404s; .NET picks its port — read it from the run output, don't assume 5000).
4. Tests green before feature work: `./gradlew test` / a scaffolded `.Tests` project with `dotnet test`.
5. `git init` yourself; neither scaffolder creates a repo. Wire tests into CI per `ci-github-actions.md`.

## Traps

**The Initializr zip ships `gradlew` without the execute bit** (unzip drops it) — `chmod +x gradlew` first, same as the Kotlin wizard's zip. (Observed 2026-08-30.)

**Spring's first `./gradlew test` is ~45s of downloads on a warm network** — Gradle distribution + dependency graph; not a hang. (Observed 2026-08-30.)

**`dotnet run` picks a port from the launch profile and prints it** — 5001 here, not the 5000/5080 of memory. Parse `http://localhost:<port>` from the output. (Observed 2026-08-30.)

**A bare Spring web starter returning 404 on `/` looks like failure but is success** — Tomcat is up; there are just no mappings. Check the log line `Tomcat initialized with port 8080` and treat the whitelabel page as the boot proof. (Observed 2026-08-30.)

## AI and agent resources

- `https://start.spring.io/metadata/client` — machine-readable Initializr catalog (versions, dependency ids); the authority for what to pass the API.
- `https://builds.dotnet.microsoft.com/dotnet/release-metadata/releases-index.json` — machine-readable .NET channel/SDK versions (used by this page's probe).
- Doc questions for both: Context7. llms.txt not probed for either this session.
