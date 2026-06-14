import { ref, onUnmounted } from 'vue'

/**
 * 数据监控 WebSocket — 接收文件变化和索引进度通知
 */
export function useDataMonitor(baseUrl: string) {
  const isConnected = ref(false)
  const lastEvent = ref<string>('')
  const indexProgress = ref<{ current: number; total: number; filename: string } | null>(null)
  const indexStats = ref<{ added: number; updated: number; deleted: number; errors: number } | null>(null)
  const error = ref<string>('')

  let ws: WebSocket | null = null
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null

  // 回调
  let onFileChange: ((files: string[], action: string) => void) | null = null
  let onIndexStart: ((files: string[], count: number) => void) | null = null
  let onIndexProgress: ((current: number, total: number, filename: string) => void) | null = null
  let onIndexComplete: ((stats: { added: number; updated: number; deleted: number; errors: number }) => void) | null = null
  let onIndexError: ((filename: string, errorMsg: string) => void) | null = null

  function connect() {
    ws = new WebSocket(baseUrl)

    ws.onopen = () => {
      isConnected.value = true
      error.value = ''
      // 心跳
      heartbeatTimer = setInterval(() => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'ping' }))
        }
      }, 30000)
    }

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)
      lastEvent.value = data.type

      switch (data.type) {
        case 'file_change':
          onFileChange?.call(null, data.files, data.action)
          break
        case 'index_start':
          indexProgress.value = { current: 0, total: data.count, filename: '' }
          onIndexStart?.call(null, data.files, data.count)
          break
        case 'index_progress':
          indexProgress.value = { current: data.current, total: data.total, filename: data.filename }
          onIndexProgress?.call(null, data.current, data.total, data.filename)
          break
        case 'index_complete':
          indexStats.value = data.stats
          indexProgress.value = null
          onIndexComplete?.call(null, data.stats)
          break
        case 'index_error':
          error.value = `索引失败: ${data.filename} — ${data.error}`
          onIndexError?.call(null, data.filename, data.error)
          break
        case 'pong':
          break
      }
    }

    ws.onclose = () => {
      isConnected.value = false
      if (heartbeatTimer) {
        clearInterval(heartbeatTimer)
        heartbeatTimer = null
      }
      // 3秒后自动重连
      setTimeout(connect, 3000)
    }

    ws.onerror = () => {
      isConnected.value = false
    }
  }

  function disconnect() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer)
      heartbeatTimer = null
    }
    if (ws) {
      ws.close()
      ws = null
    }
  }

  onUnmounted(disconnect)

  return {
    isConnected,
    lastEvent,
    indexProgress,
    indexStats,
    error,
    connect,
    disconnect,
    get onFileChange() { return onFileChange },
    set onFileChange(fn) { onFileChange = fn },
    get onIndexStart() { return onIndexStart },
    set onIndexStart(fn) { onIndexStart = fn },
    get onIndexProgress() { return onIndexProgress },
    set onIndexProgress(fn) { onIndexProgress = fn },
    get onIndexComplete() { return onIndexComplete },
    set onIndexComplete(fn) { onIndexComplete = fn },
    get onIndexError() { return onIndexError },
    set onIndexError(fn) { onIndexError = fn },
  }
}
