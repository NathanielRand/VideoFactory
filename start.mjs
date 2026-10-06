// One-command launcher for every OS: `node start.mjs` (or `npm start`) from the repo root.
// Installs UI dependencies on first run (or when the lockfile changes), then starts dev mode.
import { spawnSync } from 'node:child_process'
import { existsSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ui = join(dirname(fileURLToPath(import.meta.url)), 'ui')

const run = (cmd, args) =>
  spawnSync(cmd, args, { cwd: ui, stdio: 'inherit', shell: true }).status ?? 1

// Use pnpm if present, otherwise the copy bundled with Node via corepack.
const hasPnpm = spawnSync('pnpm', ['--version'], { shell: true, stdio: 'ignore' }).status === 0
const pnpm = hasPnpm ? ['pnpm'] : ['corepack', 'pnpm']

const stamp = join(ui, 'node_modules', '.modules.yaml')
const stale =
  !existsSync(stamp) || statSync(join(ui, 'pnpm-lock.yaml')).mtimeMs > statSync(stamp).mtimeMs
if (stale && run(pnpm[0], [...pnpm.slice(1), 'install']) !== 0) process.exit(1)

process.exit(run(pnpm[0], [...pnpm.slice(1), 'run', 'dev']))
