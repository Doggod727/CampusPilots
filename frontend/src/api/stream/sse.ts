/** 通用 SSE 帧解析：event/data/id 行、跨块重组、跳过 keep-alive 注释。 */
export interface SseFrame {
  event: string
  data: string
  id: string | null
}

function parseFrame(raw: string): SseFrame | null {
  let event = 'message'
  let id: string | null = null
  const data: string[] = []
  for (const line of raw.split('\n')) {
    if (line.startsWith(':')) {
      continue
    }
    if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trim()
    } else if (line.startsWith('data:')) {
      data.push(line.slice('data:'.length).replace(/^ /, ''))
    } else if (line.startsWith('id:')) {
      id = line.slice('id:'.length).trim()
    }
  }
  if (data.length === 0 && event === 'message') {
    return null
  }
  return { event, data: data.join('\n'), id }
}

export async function* parseSseStream(body: ReadableStream<Uint8Array>): AsyncGenerator<SseFrame> {
  const reader = body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) {
        break
      }
      buffer += decoder.decode(value, { stream: true })
      let boundary = buffer.indexOf('\n\n')
      while (boundary >= 0) {
        const raw = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const frame = parseFrame(raw)
        if (frame) {
          yield frame
        }
        boundary = buffer.indexOf('\n\n')
      }
    }
  } finally {
    reader.releaseLock()
  }
}
