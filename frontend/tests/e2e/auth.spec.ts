import { expect, test, type Page } from '@playwright/test'

const DEMO_PASSWORD = 'CampusPilot-Demo-2026!'

async function login(page: Page, username: string) {
  await page.goto('/login')
  await page.locator('#login-username').fill(username)
  await page.locator('#login-password').fill(DEMO_PASSWORD)
  await page.getByRole('button', { name: '登 录' }).click()
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole('heading', { name: '概览' })).toBeVisible()
}

async function storageIsEmpty(page: Page) {
  return page.evaluate(() => {
    try {
      return window.localStorage.length === 0 && window.sessionStorage.length === 0
    } catch {
      return false
    }
  })
}

test.describe('auth e2e (real backend + e2e database)', () => {
  test('login reaches the dashboard and browser storage stays empty', async ({ page }) => {
    await login(page, 'student01')
    expect(await storageIsEmpty(page)).toBe(true)
    await expect(page.getByText('张同学').first()).toBeVisible()
  })

  test('reload restores identity from the refresh cookie', async ({ page }) => {
    await login(page, 'student01')
    await page.reload()
    await expect(page.getByRole('heading', { name: '概览' })).toBeVisible()
    await expect(page.getByText('张同学').first()).toBeVisible()
    expect(await storageIsEmpty(page)).toBe(true)
  })

  test('a fresh browser context without cookies lands on login', async ({ browser }) => {
    const context = await browser.newContext()
    const page = await context.newPage()
    await page.goto('/')
    await expect(page).toHaveURL(/\/login/)
    await context.close()
  })

  test('logout clears the session and returns to login', async ({ page }) => {
    await login(page, 'student01')
    await page.getByRole('button', { name: '退出' }).click()
    await expect(page).toHaveURL(/\/login/)
    await page.goto('/')
    await expect(page).toHaveURL(/\/login/)
    expect(await storageIsEmpty(page)).toBe(true)
  })

  test('wrong password shows the stable error', async ({ page }) => {
    await page.goto('/login')
    await page.locator('#login-username').fill('student01')
    await page.locator('#login-password').fill('wrong-password-1')
    await page.getByRole('button', { name: '登 录' }).click()
    await expect(page.getByRole('alert')).toContainText('用户名或密码不正确')
  })

  test('menus differ by role permissions', async ({ page }) => {
    await login(page, 'admin01')
    await expect(page.locator('.shell__link', { hasText: '概览' }).first()).toBeVisible()
    await page.getByRole('button', { name: '退出' }).click()
    await login(page, 'student01')
    await expect(page.locator('.shell__link', { hasText: '概览' })).toHaveCount(0)
  })
})
