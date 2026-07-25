import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

import { load } from 'js-yaml'
import { describe, expect, it } from 'vitest'

const SDK_PATH = join(__dirname, '../../src/api/generated/sdk.gen.ts')
const SPEC_PATH = join(__dirname, '../../../docx/deliverables/openapi.yaml')
const SRC_DIR = join(__dirname, '../../src')

function contractOperationIds(): string[] {
  const spec = load(readFileSync(SPEC_PATH, 'utf-8')) as {
    paths: Record<string, Record<string, { operationId?: string }>>
  }
  const ids: string[] = []
  for (const item of Object.values(spec.paths)) {
    for (const [method, operation] of Object.entries(item)) {
      if (['get', 'post', 'put', 'patch', 'delete'].includes(method) && operation.operationId) {
        ids.push(operation.operationId)
      }
    }
  }
  return ids.sort()
}

function* walk(dir: string): Generator<string> {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) {
      yield* walk(full)
    } else if (/\.(ts|vue)$/.test(entry.name)) {
      yield full
    }
  }
}

describe('generated OpenAPI SDK', () => {
  it('exports every contract operationId as an sdk function', () => {
    const sdk = readFileSync(SDK_PATH, 'utf-8')
    const exported = [...sdk.matchAll(/export const (\w+)/g)].map((match) => match[1]!).sort()
    expect(exported).toEqual(contractOperationIds())
  })

  it('keeps invokeInternalTool out of business code', () => {
    for (const file of walk(SRC_DIR)) {
      const normalized = file.replaceAll('\\', '/')
      if (normalized.includes('/api/generated/')) continue
      expect(readFileSync(file, 'utf-8')).not.toContain('invokeInternalTool')
    }
  })

  it('forbids raw fetch outside the api layer', () => {
    for (const file of walk(SRC_DIR)) {
      if (file.startsWith(join(SRC_DIR, 'api'))) continue
      const source = readFileSync(file, 'utf-8')
      expect(source).not.toMatch(/\bfetch\s*\(/)
    }
  })
})
