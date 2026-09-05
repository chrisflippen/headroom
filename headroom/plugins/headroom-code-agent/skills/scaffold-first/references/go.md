<!-- freshness verified=2026-08-30 baseline=2026-09-04 -->
<!-- probe: go-stable | curl -s 'https://go.dev/dl/?mode=json' | python3 -c "import sys,json;print(json.load(sys.stdin)[0]['version'])" | go1.27.1 -->
<!-- probe: go-chi | curl -s https://proxy.golang.org/github.com/go-chi/chi/v5/@latest | python3 -c "import sys,json;print(json.load(sys.stdin)['Version'])" | v5.3.2 -->
# go scaffold-first reference (researched 2026-08-30)

Covers **Go** services and CLIs. Verified live this session (macOS arm64, Go 1.27.0 from the official tarball): toolchain installed from go.dev's feed, module initialized, a standard-library HTTP server written, vetted, tested, booted, and hit; the chi router added via `go get` and tested the same way.

Go has no app scaffolder and doesn't want one — `go mod init` plus files you write IS the official path. What the standard enforces here is the toolchain install path, the module/dependency commands, and never touching `go.sum`.

## Installing the toolchain (the scriptable official path)

Resolve the CURRENT version from Go's own feed — never from memory:

```bash
curl -s 'https://go.dev/dl/?mode=json' | python3 -c "import sys,json; s=json.load(sys.stdin)[0]; print(s['version']); [print('https://go.dev/dl/'+f['filename']) for f in s['files'] if f['os']=='darwin' and f['arch']=='arm64' and f['filename'].endswith('.tar.gz')]"
```

Observed this session: `go1.27.0`. Download the tarball, extract, put `<dir>/go/bin` on PATH. (This machine also had a Homebrew go1.26.6 — a stale inherited install; the tarball is Go's documented path and gives you current, no sudo needed. The `.pkg` variant needs admin.)

## Official commands (all run live)

| Step | Command | Notes |
|---|---|---|
| New module | `go mod init <module-path>` | creates `go.mod`; the module path is the import path, decide it up front |
| Add a dependency | `go get <pkg>@latest` | never hand-type versions into `go.mod` |
| Sync/prune deps | `go mod tidy` | after adding/removing imports; also fixes `// indirect` markers once code actually imports the package |
| Static checks | `go vet ./...` and `gofmt -l .` | both ship with the toolchain, zero config |
| Test | `go test ./...` | stdlib `testing` + `httptest` — no framework needed for HTTP handler tests |
| Run / build | `go run .` / `go build ./...` | |

Server baseline verified here: the standard library alone (`net/http` with Go 1.22+ method-and-pattern routing like `mux.HandleFunc("GET /{$}", ...)`) served JSON and passed an `httptest` unit test. Router when routes outgrow stdlib: **chi** (`go get github.com/go-chi/chi/v5@latest` → v5.3.2 observed) — stdlib-compatible `http.Handler`, tested here via `httptest.NewServer`.

## Never hand-write

- `go.sum` — checksum database, owned entirely by the go tool.
- Version strings in `go.mod` — `go get`/`go mod tidy` write them. Editing the `go` directive line to change language version is legitimate (`go mod edit -go=<v>` is the tool-owned way).
- `vendor/` (if vendoring) — `go mod vendor` regenerates it.

## Thorough setup checklist

1. Toolchain from the feed above; `go version` to confirm which go is first on PATH (Homebrew leftovers shadow it — see Traps).
2. `go mod init` with the real import path (github.com/org/repo for anything that will be pushed).
3. Write `main.go` + a `_test.go` beside it before feature work; `go vet ./... && go test ./...` green from the first commit.
4. `gofmt -l .` must print nothing; wire vet+test+gofmt into CI per `ci-github-actions.md`.
5. Dependencies only via `go get`; run `go mod tidy` before every commit that touched imports.
6. `git init` yourself; commit `go.mod` AND `go.sum`.

## Traps

**A Homebrew go shadows the official one on PATH.** This machine had go1.26.6 at `/opt/homebrew/bin/go` while current stable was 1.27.0 — an inherited install, quietly one minor version behind. Check `which go` + `go version` before assuming; put the tarball's `go/bin` earlier on PATH. (Observed 2026-08-30.)

**`go get` before the code imports the package marks it `// indirect`.** Not an error: the marker corrects itself on the next `go mod tidy` after a real import exists. Don't hand-edit the comment. (Observed 2026-08-30.)

**`go run .` keeps running as a compiled binary under a temp path** — `pkill` by module name may miss it; kill by port or by the `exe/<name>` pattern. (Observed 2026-08-30: the process died as `signal: terminated` under `exe/main`.)

## AI and agent resources

- `https://go.dev/dl/?mode=json` — machine-readable current releases (used by the probe above).
- `https://proxy.golang.org/<module>/@latest` — machine-readable latest version of any module (used by the chi probe).
- `go doc <pkg>` works offline from the toolchain itself; prefer it over memory for stdlib APIs. No llms.txt at go.dev at this check (404).
