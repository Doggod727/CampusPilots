import { marked } from 'marked'

/** Render markdown string to safe HTML for v-html usage. */
export function renderMarkdown(text: string): string {
  if (!text) return ''
  const raw = marked.parse(text, { async: false }) as string
  // Strip <script> and event handlers for safety
  return raw
    .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '')
    .replace(/\bon\w+\s*=\s*"[^"]*"/gi, '')
    .replace(/\bon\w+\s*=\s*'[^']*'/gi, '')
}
