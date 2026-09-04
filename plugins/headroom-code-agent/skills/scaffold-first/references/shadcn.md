<!-- freshness verified=2026-08-29 baseline=2026-09-04 -->
<!-- probe: shadcn | npm view shadcn version | 4.21.0 -->
<!-- probe: shadcn-svelte | npm view shadcn-svelte version | 1.6.0 -->
# shadcn scaffold-first reference (researched 2026-08-29)

Covers component generation for **React** (`shadcn` CLI) and **Svelte**
(`shadcn-svelte` CLI). Neither ships a component as an npm dependency —
both copy the component's source into your repo (`components/ui/` by
default) so you own and edit the code directly. Never hand-write a
shadcn-style component from memory; add it, then edit the generated file.
Verified live on this machine (macOS, Node v26.7.0) with stdin closed.

## React: the `shadcn` CLI (v4.21.0 at time of writing — resolve `@latest` fresh)

| Step | Command | What it does |
|---|---|---|
| Initialize a project | `npx shadcn@latest init [components...]` | Detects your framework, writes `components.json`, sets up Tailwind CSS variables, installs the `cn` package and writes `export { cn } from "cn"` into `lib/utils`, and can install starter components in the same step |
| Add one or more components | `npx shadcn@latest add <component...>` | Copies component source + its Radix/Base UI deps into your project |
| Add every available component | `npx shadcn@latest add --all` | Same, for the whole catalog |
| Preview without writing | `npx shadcn@latest add <component> --dry-run` | Shows what would change |
| See a component's docs/usage inline | `npx shadcn@latest docs <component...>` | Prints API reference + usage examples — use this instead of guessing prop names |
| Search the registry | `npx shadcn@latest search` / `list` | Lists components across configured registries |
| Check project health | `npx shadcn@latest info` | Reports detected framework, config, installed components |
| List the available migrations | `npx shadcn@latest migrate --list` | Prints every migration the installed CLI knows about — run this instead of guessing a migration name |
| Run a migration | `npx shadcn@latest migrate <migration> [path]` | Verified live on 4.21.0, `--list` reports five: `cn` (migrate clsx and tailwind-merge to cn), `icons`, `base-color`, `radix`, `rtl`. Takes `-f/--from` and `-t/--to` for the icon-library and base-color migrations, `-y/--yes` to skip the confirmation, and an optional path or glob to scope it |

Key `init` flags: `-t/--template <next|start|vite|react-router|laravel|astro>` (auto-detected if omitted), `-b/--base <base|radix|aria>` (which primitive library backs the components — default `radix`), `-p/--preset <name>`, `-d/--defaults` (non-interactive: `--template=next --preset=base-nova`). Non-interactive scaffolding: `-y/--yes` defaults to true already; add `-d` for a fully unattended first run.

### Wire the MCP server for Claude Code (this is the "auto-invoked" path — do this, don't rely on the CLI alone)

```
npx shadcn@latest mcp init --client claude
```

This registers a project-scoped shadcn MCP server so Claude can search the
registry and pull component source directly as tool calls instead of
shelling out — run it once per project right after `init`. Other
`--client` values: `cursor`, `vscode`, `codex`, `opencode`.

## Svelte: the `shadcn-svelte` CLI

| Step | Command | What it does |
|---|---|---|
| Initialize a project | `npx shadcn-svelte@latest init` | Writes `components.json`, wires Tailwind CSS variables for SvelteKit |
| Add one or more components | `npx shadcn-svelte@latest add <component...>` | Copies component source into `src/lib/components/ui/` |
| Apply a preset to an existing project | `npx shadcn-svelte@latest apply <preset>` | Bulk-applies a themed preset |

`shadcn-svelte` has no `mcp` subcommand as of this writing — for Svelte
projects the Svelte MCP server + `svelte-file-editor` subagent (see the
project's Svelte setup) is the auto-invoked path; use `shadcn-svelte add`
as the scaffolding step underneath it.

## Traps

- Never install a shadcn component from npm (`npm install @shadcn/button` —
  does not exist as a real package on the official registry). The whole
  point of shadcn is that the source lands in your repo, not `node_modules`.
- `shadcn init` without `-d/--defaults` prompts interactively for style,
  base color, and CSS variables — pass `-d` (or a `-p/--preset`) for
  unattended scaffolding, same policy as every other generator in this skill.
- Re-running `add` on an already-added component is a no-op unless you pass
  `--overwrite` (React) — it won't clobber your edits by accident, but it
  also won't pick up upstream changes without it.
- `diff` is deprecated in the React CLI in favor of `add <component> --diff`.
- **`cn` moved into its own package in shadcn 4.21.0.** `init` now installs a package literally named `cn` and generates `lib/utils` as a re-export (`export { cn } from "cn"`), and registry components import `cn` from that package rather than from your local `lib/utils`. Projects initialized before 4.21.0 still have the hand-rolled `clsx` + `tailwind-merge` helper; `npx shadcn@latest migrate cn` is the supported way across. Don't hand-write the old helper into a newly initialized project. (Source: shadcn 4.20.0 and 4.21.0 release notes.)
