<!-- freshness verified=2026-08-21 baseline=2026-08-30 -->
<!-- probe: scrapy | curl -s https://pypi.org/pypi/scrapy/json | python3 -c "import sys,json;print(json.load(sys.stdin)['info']['version'])" | 2.18.0 -->
<!-- probe: crawlee | npm view crawlee version | 3.18.1 -->
<!-- probe: playwright | npm view playwright version | 1.62.1 -->
# scrapers scaffold-first reference (researched 2026-08-21, round 5)

## Official setup commands

| Step | Command | What it generates |
|---|---|---|
| Shell for a Scrapy project | `uv init --bare <name>` | `pyproject.toml` only — `--bare` on purpose, so Scrapy's own generator can populate the directory without fighting the `src/<pkg>` layout, extra `README.md`, `.python-version`, `.gitignore`, or git-repo init that `uv init`'s non-bare modes would add |
| Add Scrapy | `uv add scrapy` | pins `scrapy` and its dependency tree (twisted, parsel, itemadapter, w3lib, protego, …) in `pyproject.toml` / `uv.lock`, syncs `.venv` |
| Generate a Scrapy project | `uv run scrapy startproject <name> .` (the trailing `.` scaffolds into the current directory instead of nesting a new one) | `scrapy.cfg`, `<name>/__init__.py`, `items.py`, `middlewares.py`, `pipelines.py`, `settings.py`, `<name>/spiders/__init__.py` |
| List Scrapy spider templates | `uv run scrapy genspider -l` | prints `basic`, `crawl`, `csvfeed`, `xmlfeed` — no files written |
| Generate a Scrapy spider | `uv run scrapy genspider [-t basic\|crawl\|csvfeed\|xmlfeed] <spider_name> <domain>` | `<name>/spiders/<spider_name>.py` — a class skeleton subclassing `scrapy.Spider` (`basic`) or `CrawlSpider`/`CSVFeedSpider`/`XMLFeedSpider` for the other templates |
| Scaffold a Crawlee for Python project | `uvx 'crawlee[cli]' create <name> --crawler-type <type> --http-client <client> --package-manager uv --start-url <url> --no-apify --no-install` (see Choosing for the `<type>`/`<client>` values; every flag is required for unattended use — see Traps) | a runnable project: `pyproject.toml`, `Dockerfile`, `.dockerignore`, `README.md`, `<pkg>/__init__.py`, `__main__.py`, `main.py`, `routes.py` |
| Install the scaffolded project | `cd <name> && uv sync` (skip if `--install` was passed to `crawlee create` instead of `--no-install`) | `uv.lock`, `.venv` |
| Add Playwright to a uv project | `uv add playwright` | pins `playwright` in `pyproject.toml` / `uv.lock`, syncs `.venv` |
| Install Playwright's browser binaries | `uv run playwright install [chromium\|firefox\|webkit] [--with-deps] [--dry-run]` | downloads browser binaries into the OS cache dir (`~/Library/Caches/ms-playwright` on macOS) — not a project file, so nothing to commit |

(Source for the `--bare` row: `uv init --help` output, uv 0.12.5; https://docs.astral.sh/uv/reference/cli/#uv-init — "`--bare`: Only create a `pyproject.toml`. Disables creating extra files like `README.md`, the `src/` tree, `.python-version` files, etc."; confirmed by running `uv init --bare test1` in a fresh directory outside any git repo — only `pyproject.toml` was written, no `.python-version`, `.gitignore`, or `.git`.)

All three tools resolve their own current version through `uv add` / `uvx` at install time — on this machine, right now, that resolved to `scrapy==2.18.0`, `crawlee==1.9.2` (with the `playwright` extra), and `playwright==1.62.0`. Don't hardcode those numbers; let the resolver pick current at time of use.

### Politeness, retry, and throttling settings — read from installed source, not from memory

**Scrapy** (from `scrapy/settings/default_settings.py` inside the installed `scrapy==2.18.0` package, cross-checked against `uv run scrapy startproject`'s generated `settings.py`):

| Setting | Library default | Value `scrapy startproject` writes | What it controls |
|---|---|---|---|
| `ROBOTSTXT_OBEY` | `False` | `True` (uncommented) | Whether `RobotsTxtMiddleware` (priority 100 in `DOWNLOADER_MIDDLEWARES_BASE`) fetches and honors each domain's robots.txt |
| `CONCURRENT_REQUESTS` | `16` | `16` (left commented, so the default applies) | Max simultaneous requests, all domains combined |
| `CONCURRENT_REQUESTS_PER_DOMAIN` | `8` | `1` (uncommented) | Max simultaneous requests to one domain |
| `DOWNLOAD_DELAY` | `0` | `1` (uncommented) | Minimum seconds between consecutive requests to the same domain |
| `DOWNLOAD_TIMEOUT` | `180` | not in the template (default applies) | Seconds the downloader waits before timing out a request |
| `RETRY_ENABLED` | `True` | not in the template (default applies) | Whether `RetryMiddleware` runs at all |
| `RETRY_TIMES` | `2` | not in the template (default applies) | Retries after the first attempt — 3 total requests |
| `RETRY_HTTP_CODES` | `[500, 502, 503, 504, 522, 524, 408, 429]` | not in the template (default applies) | Response status codes that trigger a retry |
| `RETRY_PRIORITY_ADJUST` | `-1` | not in the template (default applies) | How much a retried request's scheduling priority drops |
| `AUTOTHROTTLE_ENABLED` | `False` | `False`, left commented as a template block | Turns on the AutoThrottle extension, which sizes the delay from measured response latency instead of a fixed `DOWNLOAD_DELAY` |
| `AUTOTHROTTLE_START_DELAY` | `5.0` | `5`, commented | Initial delay (seconds) before AutoThrottle has latency data to react to |
| `AUTOTHROTTLE_MAX_DELAY` | `60.0` | `60`, commented | Ceiling AutoThrottle backs off to under high latency |
| `AUTOTHROTTLE_TARGET_CONCURRENCY` | `1.0` | `1.0`, commented | Average parallel requests per domain AutoThrottle aims to hold |
| `AUTOTHROTTLE_DEBUG` | `False` | `False`, commented | Logs the throttling math on every response received |

(Sources: https://raw.githubusercontent.com/scrapy/scrapy/master/scrapy/settings/default_settings.py — read directly, line numbers confirmed on 2026-08-21; https://docs.scrapy.org/en/latest/topics/autothrottle.html; template content confirmed by actually running `scrapy startproject` on this machine and reading the output `settings.py`.)

### Type-checking a Scrapy spider's `parse` override — one sanctioned targeted ignore

`scrapy/spiders/__init__.py` in the installed `scrapy==2.18.0` package declares `parse` as a `CallbackT`-typed class **attribute** under `if TYPE_CHECKING:` (`parse: CallbackT`), not as a plain method — the runtime method used when type-checking is off is a separate, differently-typed definition a few lines below, also gated behind `TYPE_CHECKING`. Because pyrefly sees the attribute form, it treats a subclass's real `parse(self, response: Response, **kwargs: Any) -> Iterator[...]` implementation as *narrowing a mutable attribute's type* rather than as an ordinary covariant method override, and raises `bad-override-mutable-attribute`. This is a real quirk in how Scrapy's stub-style typing interacts with pyrefly's override check, not a mistake in the subclass — confirmed on this machine by running `uv run pyrefly check` both with and without the ignore on a real generated spider: removing it reproduces the exact `bad-override-mutable-attribute` error on the `parse` line; restoring it returns to `0 errors (1 suppressed)`.

**Sanctioned fix:** a single targeted ignore comment directly above every Scrapy spider's `parse` override, with a comment citing this section:

```python
def parse(  # pyrefly: ignore[bad-override-mutable-attribute]
    self, response: Response, **kwargs: Any
) -> Iterator[SomeItem | scrapy.Request]:
    ...
```

Do not silence this class of error project-wide (e.g. in `[tool.pyrefly]` config) — the ignore is specific to overriding Scrapy's `TYPE_CHECKING`-only `parse` attribute, and a blanket suppression would also hide unrelated override mistakes elsewhere in the project. Every spider's `parse` override needs its own copy of this same targeted ignore; there is no way to fix it once in `settings.py` or a base class.

(Sources: `scrapy/spiders/__init__.py` in the installed `scrapy==2.18.0` package — `parse: CallbackT` under `if TYPE_CHECKING:`, confirmed by reading the file directly on 2026-08-22; `uv run pyrefly check` run on this machine on 2026-08-22 with and without the ignore, reproducing `bad-override-mutable-attribute` exactly when it's removed.)

**Crawlee for Python** (from `BasicCrawler.__init__` in the installed `crawlee==1.9.2` source — every crawler class, including `PlaywrightCrawler`, inherits these):

| Parameter | Default | What it controls |
|---|---|---|
| `max_request_retries` | `3` | Retries allowed per request when the handler fails (session-rotation retries are counted separately) |
| `max_session_rotations` | `10` | Times the crawler swaps to a fresh session (proxy/cookies/fingerprint) for one request before giving up |
| `request_handler_timeout` | `timedelta(minutes=1)` | How long one page's handler may run before it counts as failed |
| `max_requests_per_crawl` | `None` (no cap) | Hard limit on total pages opened during the run |
| `respect_robots_txt_file` | `False` | When `True`, fetches and honors each domain's robots.txt and skips disallowed URLs |
| `concurrency_settings` | `ConcurrencySettings(min_concurrency=1, max_concurrency=100, desired_concurrency=10, max_tasks_per_minute=inf)` | Feeds Crawlee's `AutoscaledPool`, which raises or lowers parallel task count from live CPU/memory/error-rate signals instead of a fixed number |

(Sources: https://raw.githubusercontent.com/apify/crawlee-python/master/src/crawlee/crawlers/_basic/_basic_crawler.py — read directly from the installed package source on 2026-08-21; https://crawlee.dev/python/api/class/ConcurrencySettings; https://crawlee.dev/python/docs/guides/scaling-crawlers.)

**Playwright for Python has none of these settings — verified absent, not just undocumented.** It is a browser-automation library, not a crawling framework: there is no retry count, no backoff, no request-rate throttle, and no robots.txt handling anywhere in its API. A direct text search of the `BrowserContext` API reference for "throttl", "retry", and "delay" returns zero matches. If a scraper needs those behaviors on top of Playwright, get them from a framework that wraps Playwright (Crawlee's `PlaywrightCrawler`, or `scrapy-playwright` on top of Scrapy) rather than hand-rolling a sleep loop — see Choosing. (Source: https://playwright.dev/python/docs/api/class-browsercontext, checked 2026-08-21.)

## Choosing

**Scrapy, Crawlee for Python, or bare Playwright — pick by whether you need JavaScript rendering and how much crawl-management you need built in.** Scrapy is the mature choice for large HTML-only crawls: its retry/throttle/robots machinery above is battle-tested and the defaults are genuinely polite once you run `startproject` (robots obeyed, 1 request/domain, 1-second delay — see the table). It does not render JavaScript itself; pair it with `scrapy-playwright` if a target needs a real browser, rather than writing your own async layer inside a Scrapy spider. Crawlee for Python is the newer, async-native option and its `--crawler-type` flag is the actual decision point: pick an HTTP-only type (`beautifulsoup`, `parsel`) for static pages at Scrapy-like speed, an `adaptive-*` type when you don't know in advance whether a target needs JS and want Crawlee to detect and switch automatically, a `playwright*` type when you know you need a real browser, or `stagehand` only if the project is already committed to Browserbase's Stagehand for AI-driven browser actions — that one is a narrower, newer integration and not a default pick. Bare Playwright (`uv add playwright`, no crawling framework) is the right call only for a one-off script or interactive automation with no queue, no dedup, and no retry/backoff requirement; the moment a task needs the politeness behavior this page documents, wrap Playwright in Crawlee or `scrapy-playwright` instead of reimplementing retries and delays by hand.

**Crawlee's `--http-client`: `impit` is the documented default and needs no extra install.** The docs state plainly: "The default HTTP client is `ImpitHttpClient`... it's included with the base Crawlee installation and requires no additional packages," while `httpx` and `curl-impersonate` are optional extras. Pick `impit` unless the project already depends on `httpx`'s API or specifically needs `curl-impersonate`'s TLS fingerprint spoofing.

**Crawlee's `--package-manager`: always `uv` under this standard.** The flag also accepts `poetry` and `pip` — pick `uv` to match the Python spine ruling; the generated `pyproject.toml` and its `uv sync` step are otherwise identical in shape to any other uv project.

**AutoThrottle vs a fixed `DOWNLOAD_DELAY` in Scrapy.** A freshly generated project ships a fixed 1-second `DOWNLOAD_DELAY` and leaves `AUTOTHROTTLE_ENABLED` commented out. Turn AutoThrottle on (and optionally raise `AUTOTHROTTLE_TARGET_CONCURRENCY` above its default `1.0`) for a crawl against a target of unknown or variable latency — it reacts to measured response times instead of a number you picked in advance. Keep the fixed delay for a target with a known, published rate limit, where a specific number is the actual requirement rather than a guess.

**Enabling `respect_robots_txt_file` in Crawlee vs `ROBOTSTXT_OBEY` in Scrapy.** Both default to obeying nothing (`False`) at the library level — Scrapy only flips this to `True` in the file `startproject` writes for you. Crawlee never flips it automatically; if a scraper built on Crawlee needs to honor robots.txt, pass `respect_robots_txt_file=True` explicitly to the crawler constructor. Don't assume a scaffolded Crawlee project already respects robots.txt the way a scaffolded Scrapy one does.

## Never hand-write

- `scrapy.cfg` — from `scrapy startproject`; it is a deploy-configuration pointer file and is not meant to be edited beyond adding a `[deploy]` target
- The initial skeleton of `<name>/spiders/<spider_name>.py` — from `scrapy genspider`; write your `parse` logic into the generated class, don't type the `class ...(scrapy.Spider): name = ... allowed_domains = ... start_urls = ...` boilerplate from memory, since template choice (`basic`/`crawl`/`csvfeed`/`xmlfeed`) changes the base class and generated methods
- A Crawlee for Python project's initial `pyproject.toml`, `Dockerfile`, `.dockerignore`, `main.py`, `routes.py`, `__main__.py` — from `crawlee create`; it wires the chosen crawler type, HTTP client, and start URL together correctly. Edit the contents afterward freely — routes and handlers are meant to be filled in — but don't type the initial skeleton from scratch, since the shape differs per `--crawler-type`
- `uv.lock` (both projects) — machine-managed by uv; never hand-edited, per uv's own docs

## Thorough setup checklist

1. `uv init --bare <name>` (Scrapy) or run `crawlee create` directly, which makes its own project directory (Crawlee)
2. `uv add scrapy` / the `crawlee create` flags already picked the crawler type and HTTP client — no separate add step needed for Crawlee unless adding extra libraries later
3. `uv run scrapy startproject <name> .` (Scrapy only)
4. `uv sync` once, so `.venv` matches the lockfile before writing any spider/handler code
5. Confirm `ROBOTSTXT_OBEY` (Scrapy) is actually what the target site's terms require — the generator sets it `True`, but verify it wasn't since turned off
6. For Scrapy: decide fixed `DOWNLOAD_DELAY`/`CONCURRENT_REQUESTS_PER_DOMAIN` vs `AUTOTHROTTLE_ENABLED = True`, and write the choice into `settings.py` explicitly rather than leaving the commented defaults ambiguous for the next reader
7. For Crawlee: explicitly set `respect_robots_txt_file` (it does not default on) and tune `ConcurrencySettings` if the target needs slower-than-default crawling — the `desired_concurrency=10` default is aggressive for a single small site
8. `uv run scrapy genspider <name> <domain>` per spider (Scrapy), then fill in `parse` (or `parse_item`/`parse_start_url` for `crawl` template spiders)
9. For any JavaScript-rendering path: `uv add playwright` (bare) or pick a `playwright*` `--crawler-type` (Crawlee) or add `scrapy-playwright` (Scrapy), then `uv run playwright install [--with-deps]` before the first real crawl
10. Run one real crawl against a single low-risk page first, with `AUTOTHROTTLE_DEBUG = True` (Scrapy) or normal logging (Crawlee), to see the actual delay/retry behavior before pointing the scraper at the full target
11. README documents the exact commands to rebuild the environment from a clean clone, plus the project's robots.txt/rate-limit stance in one sentence

## Traps

**`uvx 'crawlee[cli]' create <name>` with no flags crashes under a closed stdin instead of hanging or failing cleanly.** Run today with `< /dev/null` (no TTY), it printed the first interactive prompt ("Please select the Crawler type") and then crashed with `termios.error: (19, 'Operation not supported by device')` — a Python traceback through `inquirer`'s console renderer trying to read raw keypresses from a device that isn't a terminal. This happened again after supplying `--crawler-type`, `--http-client`, and `--package-manager` but omitting `--start-url` — it re-prompted for the next unset field and crashed the same way. Every prompted field must be passed explicitly for unattended use: `--crawler-type`, `--http-client`, `--package-manager`, `--start-url`, and `--apify`/`--no-apify`, `--install`/`--no-install`. (Recorded run: this machine, 2026-08-21, `crawlee==1.9.2` via `uvx 'crawlee[cli]' create`.)

**Playwright's browser binaries install into a machine-wide cache, not the project — so a bare `playwright install` can silently install for the wrong Playwright version.** On this machine, `uv add playwright` inside a fresh project pinned `playwright==1.62.0` in that project's `uv.lock`, but running the bare `playwright` command (found elsewhere on `PATH`, outside the project's `.venv`) reported `Version 1.50.0` — a different, unrelated install. Running `playwright install` unprefixed would download browser binaries matched to whichever `playwright` your shell happens to resolve first, which may not be the version the project actually locked. Always run it as `uv run playwright install`, the same rule the python-uv reference gives for `pre-commit` and `alembic`. (Recorded run: this machine, 2026-08-21 — `uv run playwright --version` reported `1.62.0`, bare `playwright --version` reported `1.50.0` in the same shell.)

**`scrapy genspider` silently no-ops on a name collision instead of erring.** Running it twice for the same spider name prints "Spider 'example' already exists in module: ..." and exits `0` — it does not overwrite the existing file and does not raise a nonzero exit code an agent's error-checking would catch. Check for the file before calling `genspider`, or check the spider's actual content after the call, rather than trusting the exit code. (Recorded run: this machine, 2026-08-21, `scrapy==2.18.0`.)

**`ROBOTSTXT_OBEY`'s library default is `False` — the polite `True` only exists because `scrapy startproject` writes it into your `settings.py`.** An agent that adds Scrapy as a bare dependency (`uv add scrapy`) and configures a spider/`Settings()` object by hand, without ever running `startproject`, gets the permissive `False` default and silently ignores robots.txt unless it sets the value itself. The politeness in this reference's Scrapy table describes the *generated project's* file, not the library's own default. (Source: https://raw.githubusercontent.com/scrapy/scrapy/master/scrapy/settings/default_settings.py, `ROBOTSTXT_OBEY = False` in the shipped defaults, vs. `ROBOTSTXT_OBEY = True` in `scrapy/templates/project/module/settings.py.tmpl`, both read directly from the `scrapy==2.18.0` package source on 2026-08-21.)

**`respect_robots_txt_file` on a Crawlee crawler defaults off and stays off even when scaffolded with `crawlee create`.** Unlike Scrapy's generator, `crawlee create` does not add a robots-respecting line to the generated `main.py` — the crawler constructor call it writes has no `respect_robots_txt_file` argument at all, so the crawler runs at the library default (`False`) until someone adds the argument by hand. Don't assume a scaffolded Crawlee project is any more polite about robots.txt than a bare `BasicCrawler()` call. (Recorded run: this machine, 2026-08-21 — generated `my_crawler/main.py` from `crawlee create ... --crawler-type playwright ...` contains no `respect_robots_txt_file` argument; default confirmed at `crawlee/crawlers/_basic/_basic_crawler.py:304`, `crawlee==1.9.2`.)

**Playwright's own docs show a bare `playwright install` with no package-manager prefix — that line is written for the `pytest-playwright` testing workflow, not project-locked scraping use.** Copying it verbatim into a uv-spine scraper project reproduces the version-drift trap above. The docs page itself never shows `uv run playwright install`; that prefix is this standard's uv-spine rule, not something Playwright's docs state. (Source: https://playwright.dev/python/docs/intro, checked 2026-08-21 — every command on that page uses bare `playwright install`, regardless of whether the preceding install step used `pip`, `poetry`, or `uv`.)

## AI and agent resources

**Scrapy** ships an official `llms.txt` (and `llms-full.txt`) at the docs root:
- `https://docs.scrapy.org/llms.txt`
- `https://docs.scrapy.org/llms-full.txt`

Both returned `200` on 2026-08-21. The Scrapy GitHub repo (`scrapy/scrapy`) has no `AGENTS.md` or `CLAUDE.md` at its root (both `404`), and there is no official Scrapy MCP server — only unrelated third-party wrappers exist, which don't count as official.

**Crawlee for Python** ships an official `llms.txt` and `llms-full.txt` under its Python docs section:
- `https://crawlee.dev/python/llms.txt`
- `https://crawlee.dev/python/llms-full.txt`

Both returned `200` on 2026-08-21. The `apify/crawlee-python` GitHub repo keeps a single source-of-truth file, `.rules.md`, with both `AGENTS.md` and `CLAUDE.md` at the repo root set up as symlinks to it — but that file is contributor-facing (it documents `uv run poe check-code`, `uv run poe lint`, and other commands for developing Crawlee itself), not guidance for downstream projects that merely use Crawlee. Apify (Crawlee's maintainer) does run an official MCP server at `mcp.apify.com`, but it fronts the Apify Actor platform's marketplace of scrapers, not Crawlee-the-library — don't conflate the two when a task specifically means local Crawlee for Python development.

**Playwright** has no official `llms.txt` (`https://playwright.dev/llms.txt` and the `/python/llms.txt` variant both `404` as of 2026-08-21) and no `AGENTS.md` in `microsoft/playwright`'s repo root (`404`). It does have official first-party agent tooling of a different kind: `microsoft/playwright-mcp`, Microsoft's own MCP server that lets an agent drive a real browser through Playwright's accessibility tree (deterministic, no vision model needed) rather than through code. Install/run it with `npx @playwright/mcp@latest` — note this is a Node/npx tool, not part of the Python spine, and it's built for interactive agent browsing/testing, not for running a scraping crawl; for actual scraping automation, drive the `playwright` Python package directly (or through Crawlee/`scrapy-playwright`) as documented above.
