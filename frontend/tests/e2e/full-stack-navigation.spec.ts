import { expect, test, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

const DEMO_PASSWORD = 'CampusPilot-Demo-2026!'

async function login(page: Page, username: string) {
  await page.goto('/login')
  await page.locator('#login-username').fill(username)
  await page.locator('#login-password').fill(DEMO_PASSWORD)
  await page.getByRole('button', { name: '登 录' }).click()
  await expect(page).toHaveURL(/\/$/)
}

async function expectModulePage(page: Page, path: string) {
  const serverErrors: number[] = []
  const onResponse = (response: { status(): number; url(): string }) => {
    if (response.status() >= 500 && response.url().includes('/api/')) {
      serverErrors.push(response.status())
    }
  }
  page.on('response', onResponse)
  await page.goto(path)
  await expect(page.locator('main h1').first()).toBeVisible()
  await expect(page.locator('.error-state')).toHaveCount(0)
  expect(page.url()).not.toContain('/403')
  expect(serverErrors).toEqual([])
  page.off('response', onResponse)
}

test.describe('full-stack module navigation (real backend + PostgreSQL)', () => {
  test.describe.configure({ timeout: 180_000 })

  test('student modules load from the real API without browser persistence', async ({ page }) => {
    await login(page, 'student01')
    for (const path of [
      '/services',
      '/services/work-orders',
      '/services/electricity',
      '/chat',
      '/knowledge/bases',
      '/community/topics',
      '/community/posts',
      '/community/events',
      '/community/lost-found',
      '/community/lost-found/claims',
      '/agent/runs',
      '/agent/catalog',
      '/agent/tools',
    ]) {
      await test.step(path, () => expectModulePage(page, path))
    }
    const storage = await page.evaluate(() => {
      let indexedDbBlocked = false
      try {
        void window.indexedDB
      } catch {
        indexedDbBlocked = true
      }
      return { local: localStorage.length, session: sessionStorage.length, indexedDbBlocked }
    })
    expect(storage).toEqual({ local: 0, session: 0, indexedDbBlocked: true })
  })

  test('ModelOps and governance pages load with administrator permissions', async ({ page }) => {
    await login(page, 'admin01')
    for (const path of [
      '/modelops/datasets',
      '/modelops/training',
      '/modelops/models',
      '/admin/users',
      '/admin/roles',
      '/admin/words',
      '/admin/moderation',
      '/admin/audit',
      '/admin/config',
    ]) {
      await test.step(path, () => expectModulePage(page, path))
    }
  })

  for (const width of [390, 768, 1280, 1440]) {
    test(`dashboard has no horizontal overflow at ${width}px`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 900 })
      await login(page, 'admin01')
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      )
      expect(overflow).toBeLessThanOrEqual(1)
      const results = await new AxeBuilder({ page }).analyze()
      const blocking = results.violations.filter(
        (violation) => violation.impact === 'critical' || violation.impact === 'serious',
      )
      expect(blocking).toEqual([])
      await page.screenshot({ path: testInfo.outputPath(`dashboard-${width}.png`), fullPage: true })
    })
  }
})
