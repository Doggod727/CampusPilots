// 静态门禁：业务代码禁止硬编码 API 路径、内部端点、原始 fetch 与浏览器持久化。
// 豁免：生成 SDK、api 封装层（client/stream）与无持久化守卫自身。
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const srcRoot = fileURLToPath(new URL('../src', import.meta.url))

const ALLOWED = [
  /src[/\\]api[/\\]generated[/\\]/,
  /src[/\\]api[/\\]client[/\\]/,
  /src[/\\]api[/\\]stream[/\\]/,
  /src[/\\]app[/\\]bootstrap[/\\]noPersistence\.ts$/,
]

const RULES = [
  { pattern: /\/api\/v1\//, label: 'hardcoded /api/v1 path' },
  { pattern: /\/internal\/v1\//, label: 'internal tool endpoint reference' },
  { pattern: /\bfetch\s*\(/, label: 'raw fetch call' },
  { pattern: /\blocalStorage\b/, label: 'localStorage usage' },
  { pattern: /\bsessionStorage\b/, label: 'sessionStorage usage' },
  { pattern: /\bindexedDB\b/, label: 'indexedDB usage' },
  { pattern: /\bcaches\b/, label: 'CacheStorage usage' },
]

function* walk(dir) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      yield* walk(full)
    } else if (/\.(ts|vue)$/.test(entry)) {
      yield full
    }
  }
}

let violations = 0
for (const file of walk(srcRoot)) {
  if (ALLOWED.some((re) => re.test(file))) {
    continue
  }
  const content = readFileSync(file, 'utf8')
  const rel = relative(srcRoot, file)
  for (const { pattern, label } of RULES) {
    if (pattern.test(content)) {
      console.error(`GUARD VIOLATION ${rel}: ${label}`)
      violations += 1
    }
  }
}

if (violations > 0) {
  console.error(`${violations} guard violation(s) found`)
  process.exit(1)
}
console.log('guard checks passed')
