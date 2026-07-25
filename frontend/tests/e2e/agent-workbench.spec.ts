import { expect, test, type Page } from '@playwright/test'

const DEMO_PASSWORD = 'CampusPilot-Demo-2026!'

test.describe.configure({ timeout: 180_000 })

async function login(page: Page, username = 'student01') {
  await page.goto('/login')
  await page.locator('#login-username').fill(username)
  await page.locator('#login-password').fill(DEMO_PASSWORD)
  await page.getByRole('button', { name: '登 录' }).click()
  await expect(page).toHaveURL(/\/$/)
}

async function createRun(page: Page, mode: string, input: string) {
  await page.goto('/agent/runs/new')
  const modeOption = page.locator('label.create__mode').filter({ hasText: mode })
  await modeOption.click()
  await expect(modeOption.locator('input[type="radio"]')).toBeChecked()
  await page.locator('#run-input').fill(input)
  await page.getByRole('button', { name: '创建运行' }).click()
  await expect(page).toHaveURL(/\/agent\/runs\/[0-9a-f-]+$/i, { timeout: 15_000 })
}

test.describe('M5 agent workbench e2e', () => {
  test('knowledge run reaches succeeded with a live timeline', async ({ page }) => {
    await login(page)
    await createRun(page, '知识问答', '四川大学有几个校区？望江校区地址是什么？')
    await expect(page.locator('.status-badge').first()).toBeVisible()
    await expect(page.getByText('路由决策')).toBeVisible({ timeout: 60_000 })
    await expect(page.getByText('终态：succeeded')).toBeVisible({ timeout: 120_000 })
    await expect(page.getByText('最终回答')).toBeVisible()
    expect(await page.evaluate(() => window.localStorage.length)).toBe(0)
  })

  test('R2 electricity topup waits for approval and completes after approval', async ({ page }) => {
    await login(page)
    await createRun(page, '校园服务', '给房间 21000000-0000-4000-8000-000000000001 充 20 元电费')
    await expect(page.getByText('等待审批').first()).toBeVisible({ timeout: 120_000 })
    await expect(page.getByRole('button', { name: '批准' })).toBeVisible()
    await page.getByRole('button', { name: '批准' }).click()
    await expect(page.getByText('已批准，运行将继续。')).toBeVisible()
    await expect(page.locator('.status-badge', { hasText: '成功' }).first()).toBeVisible({
      timeout: 120_000,
    })
  })

  test('cancelling a run waiting for approval lands on cancelled', async ({ page }) => {
    await login(page)
    await createRun(page, '校园服务', '给房间 21000000-0000-4000-8000-000000000001 充 30 元电费')
    await expect(page.getByText('待审批').first()).toBeVisible({ timeout: 120_000 })
    await page.getByRole('button', { name: '取消运行' }).click()
    await expect(page.getByText('已请求取消。')).toBeVisible()
    await expect(page.locator('.status-badge', { hasText: '已取消' }).first()).toBeVisible({
      timeout: 30_000,
    })
    await page.reload()
    await expect(page.locator('.status-badge', { hasText: '已取消' }).first()).toBeVisible()
  })
})
