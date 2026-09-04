<!-- freshness verified=2026-08-30 baseline=2026-09-04 -->
<!-- probe: rails | curl -s https://rubygems.org/api/v1/gems/rails.json | python3 -c "import sys,json;print(json.load(sys.stdin)['version'])" | 8.1.3.1 -->
<!-- probe: laravel/framework | curl -s https://repo.packagist.org/p2/laravel/framework.json | python3 -c "import sys,json;print(json.load(sys.stdin)['packages']['laravel/framework'][0]['version'])" | v13.30.1 -->
# rails-laravel scaffold-first reference (researched 2026-08-30)

Covers the batteries-included classics: **Ruby on Rails** and **Laravel** — the fastest idea-to-full-CRUD-product lanes in their languages. Both verified live this session (macOS arm64): Rails 8.1.3.1 on Homebrew Ruby 4.0.6 — scaffolded, resource generated, served, hit, 7 generated tests passing; Laravel 13.29.0 on PHP 8.5.10 — scaffolded, served, hit, its 2 shipped tests passing.

## Toolchains (resolved live)

- **Ruby**: Homebrew ruby 4.0.6 was present and Rails installs cleanly on it (`gem install rails --no-document` — the Ruby-4-compatibility question was checked live, it works). Always `export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8` first — same Ruby-4 encoding crash class as CocoaPods (`react-native.md` Traps).
- **PHP + Composer**: `brew install php composer` (PHP 8.5.10, Composer 2.10.3 observed). Laravel's macOS-official Herd app is a GUI install; Homebrew is the scriptable path and is what worked here.

## Official setup commands (all run live)

| Step | Command | Notes |
|---|---|---|
| Rails scaffold | `rails new <dir> --skip-git` | defaults: sqlite, Puma, importmap, Dockerfile included |
| Rails per-project gems | `bundle config set --local path 'vendor/bundle' && bundle install` | REQUIRED on this machine — see Traps |
| Rails resource | `bin/rails generate scaffold post title:string body:text && bin/rails db:migrate` | generates model, controller, views, routes, AND tests |
| Rails serve / test | `bin/rails server -p <port>` / `bin/rails test` | see the port trap |
| Laravel scaffold | `composer create-project laravel/laravel <dir> --no-interaction` | creates the app, generates the key, and RUNS the initial sqlite migrations itself (observed) |
| Laravel serve / test | `php artisan serve --port <port>` / `php artisan test` | artisan test now emits machine-readable JSON summary |
| Laravel artifacts | `php artisan make:model\|controller\|migration <name>` | artisan owns boilerplate |

Boot checks that worked here: Rails welcome page title + `/posts` index rendered on :3080; `bin/rails test` → 7 runs, 0 failures (from the generated scaffold — a FRESH `rails new` has zero tests, so generate a resource before judging the test setup). Laravel welcome title on :8100; `php artisan test` → 2 passed (PHPUnit 12 — this Laravel does not default to Pest; check `composer.json` before assuming either).

## Never hand-write

- `Gemfile.lock`, `composer.lock` — machine-owned (guard-enforced).
- Rails migrations' schema portion and `db/schema.rb` — written by generators/migrate.
- Laravel's `bootstrap/`, `artisan`, and config skeletons — scaffold-owned; edit config values in place.
- `.env` in Laravel is generated with the app key — regenerate keys via `php artisan key:generate`, never paste one.

## Thorough setup checklist

1. Locale exported before ANY Ruby tooling runs (Rails, CocoaPods, fastlane — all crash the same way without it).
2. Rails: vendor the bundle (Traps), then boot + generated-resource test pass before feature work.
3. Laravel: `composer create-project`, then confirm the migrations it auto-ran (`php artisan migrate:status`).
4. Both ship a Dockerfile — start from it, don't write one from memory (`docker.md`).
5. Laravel generates `AGENTS.md` AND `CLAUDE.md` — keep and honor them per the SKILL.md rule; don't overwrite.
6. `git init` yourself (Rails with `--skip-git`; Laravel creates no repo).

## Traps

**Bundler crashes with `Permission denied ... /opt/homebrew/lib/ruby/gems/4.0.0/plugins/rdoc_plugin.rb` on this machine.** The Homebrew gem tree isn't fully writable for bundler's plugin step even though `gem install` itself works. Fix that worked: per-project vendoring — `bundle config set --local path 'vendor/bundle'` before `bundle install`. Do this on every new Rails app here. (Observed 2026-08-30.)

**Docker Desktop squats on 127.0.0.1:3000.** Rails' default port collides and Puma dies with EADDRINUSE — while Node servers appear to work on :3000 because they bind IPv6. Serve Rails on an explicit port (`-p 3080`). Check `lsof -nP -iTCP:3000 -sTCP:LISTEN` when anything acts weird on 3000. (Observed 2026-08-30.)

**A fresh `rails new` has zero tests, and `bin/rails test` exits 0 on nothing.** "Tests pass" on an untouched Rails scaffold proves nothing — generate a scaffold resource first; its 7 tests are the real smoke check. (Observed 2026-08-30.)

**`composer create-project` runs database migrations during scaffolding.** Don't be surprised that the sqlite DB already has tables; and don't run it pointed at a real database configuration. (Observed 2026-08-30.)

## AI and agent resources

- Laravel's scaffold generates `AGENTS.md` + `CLAUDE.md` in the project — the framework's own agent guidance, first-party. Read them.
- `https://rubygems.org/api/v1/gems/<gem>.json` and `https://repo.packagist.org/p2/<vendor>/<pkg>.json` — machine-readable latest versions (used by this page's probes).
- Rails/Laravel doc questions: Context7. llms.txt not probed for either site this session.
