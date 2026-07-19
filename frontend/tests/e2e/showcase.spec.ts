import { expect, test } from '@playwright/test'

const WIDTHS = [390, 768, 1280, 1440]
const DEMO_PASSWORD = 'CampusPilot-Demo-2026!'

async function login(page: import('@playwright/test').Page) {
  await page.goto('/login')
  await page.locator('#login-username').fill('student01')
  await page.locator('#login-password').fill(DEMO_PASSWORD)
  await page.getByRole('button', { name: '登 录' }).click()
  await expect(page).toHaveURL(/\/$/)
}

test.describe('components showcase', () => {
  for (const width of WIDTHS) {
    test(`no horizontal overflow at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      await login(page)
      await page.goto('/dev/components')
      await expect(page.getByRole('heading', { name: '基础组件' })).toBeVisible()
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      )
      expect(overflow).toBeLessThanOrEqual(0)
    })
  }
})
