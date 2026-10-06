// One-command launcher for every OS: `node start.mjs` (or `npm start`) from the repo root.
//
// First run walks through setup, one step at a time, then starts the app:
//   1. system tools (Python 3.11, Node 22+, FFmpeg, Ollama), installed with Homebrew on macOS
//   2. the Python environment (.venv: PyTorch, then requirements.txt)
//   3. the language model (ollama pull)
//   4. the UI dependencies (pnpm install)
// Every step checks whether it is already done and skips if so, so running this again is cheap.
//
//   node start.mjs            set up whatever is missing, then launch
//   node start.mjs --check    report what is missing, change nothing
//   node start.mjs --setup    redo the Python environment even if it looks current
//   node start.mjs --yes      do not ask before installing (for scripts)
//   node start.mjs --no-model skip the model download
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { createInterface } from 'node:readline/promises'
import { fileURLToPath } from 'node:url'

const root = dirname(fileURLToPath(import.meta.url))
const ui = join(root, 'ui')
const args = new Set(process.argv.slice(2))
const CHECK = args.has('--check')
const FORCE = args.has('--setup')
const YES = args.has('--yes')
const NO_MODEL = args.has('--no-model')

const OS = process.platform // 'darwin' | 'win32' | 'linux'
const MODEL = 'gemma:7b' // the model the scoring is tuned on (README)
const venvPython =
  OS === 'win32' ? join(root, '.venv', 'Scripts', 'python.exe') : join(root, '.venv', 'bin', 'python')
const venvStamp = join(root, '.venv', '.setup-stamp')

// ---- output ---------------------------------------------------------------------------------
const tty = process.stdout.isTTY
const paint = (code) => (s) => (tty ? `\x1b[${code}m${s}\x1b[0m` : s)
const bold = paint(1), dim = paint(2), green = paint(32), yellow = paint(33), red = paint(31)
const results = []
let stepNo = 0
const TOTAL = 4
const heading = (title) => console.log(`\n${bold(`[${++stepNo}/${TOTAL}] ${title}`)}`)
const ok = (msg) => console.log(`  ${green('✓')} ${msg}`)
const warn = (msg) => console.log(`  ${yellow('!')} ${msg}`)
const fail = (msg) => console.log(`  ${red('✗')} ${msg}`)
const note = (msg) => console.log(`    ${dim(msg)}`)

// ---- helpers --------------------------------------------------------------------------------
const sh = (cmd, a = [], opts = {}) =>
  spawnSync(cmd, a, { cwd: root, shell: OS === 'win32', encoding: 'utf8', ...opts })
const has = (cmd, a = ['--version']) => sh(cmd, a, { stdio: 'ignore' }).status === 0
const out = (cmd, a) => {
  const r = sh(cmd, a)
  return r.status === 0 ? `${r.stdout}${r.stderr}`.trim() : ''
}
const live = (cmd, a, opts = {}) => sh(cmd, a, { stdio: 'inherit', ...opts }).status === 0

async function confirm(question) {
  if (YES) return true
  if (!process.stdin.isTTY) return false
  const rl = createInterface({ input: process.stdin, output: process.stdout })
  const answer = (await rl.question(`  ${question} ${dim('[Y/n]')} `)).trim().toLowerCase()
  rl.close()
  return answer === '' || answer === 'y' || answer === 'yes'
}

const major = (v) => Number((/(\d+)\./.exec(v) ?? [])[1] ?? 0)

// Find a Python 3.11 to build the venv from. Returns [cmd, ...args] or null.
function findPython311() {
  const candidates = OS === 'win32' ? [['py', '-3.11'], ['python']] : [['python3.11'], ['python3']]
  for (const [cmd, ...pre] of candidates) {
    if (/Python 3\.11\./.test(out(cmd, [...pre, '--version']))) return [cmd, ...pre]
  }
  return null
}

// What is missing from the machine, and the command that installs it.
function missingTools() {
  const missing = []
  if (!findPython311()) missing.push({ name: 'Python 3.11', brew: 'python@3.11', other: 'https://www.python.org/downloads/' })
  if (major(out('node', ['--version']).replace(/^v/, '')) < 22)
    missing.push({ name: 'Node 22+', brew: 'node', other: 'https://nodejs.org' })
  if (!has('ffmpeg', ['-version']))
    missing.push({ name: 'FFmpeg', brew: 'ffmpeg', other: OS === 'win32' ? 'winget install Gyan.FFmpeg' : 'sudo apt install ffmpeg' })
  if (!has('ollama'))
    missing.push({ name: 'Ollama', brew: 'ollama', other: 'https://ollama.com/download' })
  return missing
}

// ---- 0. platform sanity ---------------------------------------------------------------------
if (OS === 'darwin' && process.arch !== 'arm64') {
  console.error(red('\nIntel Macs are not supported (PyTorch no longer publishes Intel Mac wheels).'))
  process.exit(1)
}
console.log(bold('Video Factory setup'))
note(`${OS} ${process.arch}, Node ${process.versions.node}${CHECK ? ' (check only, nothing will be changed)' : ''}`)

// ---- 1. system tools ------------------------------------------------------------------------
heading('System tools')
let missing = missingTools()
if (!missing.length) {
  ok('Python 3.11, Node 22+, FFmpeg and Ollama are all installed')
} else {
  for (const m of missing) fail(`${m.name} not found`)
  if (!CHECK && OS === 'darwin') {
    if (!has('brew')) {
      warn('Homebrew is not installed. It installs the tools above.')
      note('Install it from https://brew.sh (it asks for your password), then run this again.')
    } else if (await confirm(`Install with Homebrew (${missing.map((m) => m.brew).join(' ')})?`)) {
      live('brew', ['install', ...missing.map((m) => m.brew)])
      // Brew's node can leave pnpm unavailable until corepack is enabled; handled in step 4.
      missing = missingTools()
    }
  } else if (!CHECK) {
    note('Install these, then run this again:')
    for (const m of missing) note(`  ${m.name}: ${m.other}`)
  }
  if (missing.length) {
    for (const m of missing) note(`still missing: ${m.name}`)
    results.push('system tools')
    if (!CHECK) {
      console.error(red('\nSetup cannot continue until the tools above are installed.'))
      process.exit(1)
    }
  } else ok('Installed')
}

// ---- 2. Python environment ------------------------------------------------------------------
heading('Python environment')
const reqHash = createHash('sha256').update(readFileSync(join(root, 'requirements.txt'))).digest('hex')
const venvCurrent = existsSync(venvPython) && existsSync(venvStamp) && readFileSync(venvStamp, 'utf8').trim() === reqHash
if (venvCurrent && !FORCE) {
  ok('.venv is up to date')
} else if (CHECK) {
  fail(existsSync(venvPython) ? '.venv is out of date (requirements.txt changed)' : '.venv has not been created')
  results.push('python environment')
} else {
  const py = findPython311()
  if (!py) {
    fail('Python 3.11 is needed to create the environment')
    process.exit(1)
  }
  const pip = (a) => live(venvPython, ['-m', 'pip', ...a])
  if (!existsSync(venvPython)) {
    console.log('  Creating .venv ...')
    if (!live(py[0], [...py.slice(1), '-m', 'venv', '.venv'])) {
      fail('Could not create .venv')
      process.exit(1)
    }
  }
  // PyTorch first, from the right index: NVIDIA on Windows, plain PyPI on macOS (Metal) and Linux.
  const nvidia = OS !== 'darwin' && has('nvidia-smi', ['-L'])
  const torch = ['install', 'torch', 'torchvision']
  if (OS === 'win32') torch.push('--index-url', `https://download.pytorch.org/whl/${nvidia ? 'cu130' : 'cpu'}`)
  else if (OS === 'linux' && !nvidia) torch.push('--index-url', 'https://download.pytorch.org/whl/cpu')
  console.log(`  Installing PyTorch${nvidia ? ' (CUDA)' : OS === 'darwin' ? ' (Metal)' : ' (CPU)'}. This is the big download.`)
  if (!pip(torch)) { fail('PyTorch install failed'); process.exit(1) }
  console.log('  Installing the rest of the requirements ...')
  if (!pip(['install', '-r', 'requirements.txt'])) { fail('requirements.txt install failed'); process.exit(1) }
  writeFileSync(venvStamp, reqHash)
  ok('Python environment ready')
}

// ---- 3. language model ----------------------------------------------------------------------
heading('Language model')
if (NO_MODEL) {
  warn('Skipped (--no-model). Pick or pull a model later from the Models page.')
} else if (!has('ollama')) {
  warn('Ollama is not installed, so the local model cannot be checked')
} else {
  const ollamaUp = () => has('ollama', ['list'])
  const haveModel = () => out('ollama', ['list']).split(/\r?\n/).some((l) => l.startsWith(`${MODEL} `))
  if (!ollamaUp() && !CHECK) {
    note('Starting Ollama ...')
    // `ollama serve` stays up after we exit; the desktop app also starts it if it is not running.
    spawnSync(OS === 'darwin' ? 'open' : 'sh', OS === 'darwin' ? ['-a', 'Ollama'] : ['-c', 'nohup ollama serve >/dev/null 2>&1 &'], { stdio: 'ignore', shell: OS === 'win32' })
    for (let i = 0; i < 15 && !ollamaUp(); i++) spawnSync(process.execPath, ['-e', 'setTimeout(()=>{},1000)'])
  }
  if (!ollamaUp()) {
    warn('Ollama is not running. Start it, then run this again to download the model.')
    results.push('ollama not running')
  } else if (haveModel()) {
    ok(`${MODEL} is already downloaded`)
  } else if (CHECK) {
    fail(`${MODEL} is not downloaded`)
    results.push('model')
  } else if (await confirm(`Download ${MODEL} (about 5 GB)?`)) {
    if (live('ollama', ['pull', MODEL])) ok(`${MODEL} downloaded`)
    else { fail('Download failed. Run `ollama pull ' + MODEL + '` later.'); results.push('model') }
  } else {
    warn(`Skipped. Run \`ollama pull ${MODEL}\` when you want it.`)
  }
}

// ---- 4. UI dependencies ---------------------------------------------------------------------
heading('UI dependencies')
// Use pnpm if present, otherwise the copy bundled with Node via corepack.
const pnpm = has('pnpm') ? ['pnpm'] : ['corepack', 'pnpm']
const runUi = (a) => live(pnpm[0], [...pnpm.slice(1), ...a], { cwd: ui })
const stamp = join(ui, 'node_modules', '.modules.yaml')
const stale = !existsSync(stamp) || statSync(join(ui, 'pnpm-lock.yaml')).mtimeMs > statSync(stamp).mtimeMs
if (!stale) ok('Up to date')
else if (CHECK) { fail('Not installed (run without --check to install)'); results.push('ui dependencies') }
else if (!runUi(['install'])) { fail('pnpm install failed'); process.exit(1) }
else ok('Installed')

// ---- launch ---------------------------------------------------------------------------------
if (CHECK) {
  console.log(results.length ? `\n${yellow('Not ready:')} ${results.join(', ')}` : `\n${green('Everything is in place.')}`)
  process.exit(results.length ? 1 : 0)
}
console.log(`\n${bold('Starting Video Factory ...')}`)
process.exit(runUi(['run', 'dev']) ? 0 : 1)
