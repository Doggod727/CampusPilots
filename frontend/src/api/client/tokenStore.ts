/** Access Token 仅驻留模块内存：刷新页面时通过 Refresh Cookie 重建，不落任何持久层。 */
let accessToken: string | null = null

export const tokenStore = {
  get(): string | null {
    return accessToken
  },
  set(token: string | null): void {
    accessToken = token
  },
  clear(): void {
    accessToken = null
  },
}
