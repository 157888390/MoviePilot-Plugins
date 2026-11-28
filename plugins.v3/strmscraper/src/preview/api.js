import { PREVIEW_MEDIA } from './data.js'

/** 仅内存演示：覆盖页面用到的接口，所有操作只修改示例数据。 */
export function createPreviewApi() {
  const media = structuredClone(PREVIEW_MEDIA)
  let records = media
    .filter((item) => item.status !== 'pending')
    .map((item, index) => ({
      title: item.title,
      target: item.path,
      category: item.category,
      type: 'dir',
      success: item.status === 'scraped',
      error_code: item.error_code,
      message: item.status === 'scraped' ? 'NFO 与海报已更新，媒体资料已补全' : item.error_message,
      exists: true,
      time: new Date(Date.now() - index * 420000).toLocaleString('sv-SE').slice(0, 16),
    }))
  const waiting = []
  let running = null
  let nextAt = 0
  const stats = { done: 8, failed: 1, canceled: 0 }
  const recent = records.slice(0, 4).map((item) => ({ ...item, kind: 'dir' }))
  const clone = (value) => structuredClone(value)
  const envelope = (value) => ({ data: { success: true, data: clone(value) } })
  const delay = () => new Promise((resolve) => setTimeout(resolve, 140))
  let serial = 0
  function advance() {
    if (running && Date.now() >= nextAt) {
      const item = media.find(
        (entry) => running.target === entry.path || running.target.startsWith(entry.path + '/'),
      )
      if (item) {
        item.status = 'scraped'
        item.dir_scraped = true
        item.error_code = ''
        item.error_message = ''
        item.last_scrape = Math.floor(Date.now() / 1000)
      }
      const record = {
        title: item?.title || running.target.split('/').pop(),
        target: running.target,
        category: item?.category || '',
        type: running.kind,
        success: true,
        message: '示例刮削完成，媒体资料已补全',
        exists: true,
        time: new Date().toLocaleString('sv-SE').slice(0, 16),
      }
      records.unshift(record)
      recent.unshift({ ...record, kind: running.kind })
      recent.splice(8)
      stats.done++
      running = null
    }
    if (!running && waiting.length) {
      running = waiting.shift()
      nextAt = Date.now() + 2400
    }
  }
  function enqueue(paths, kind = 'dir') {
    let queued = 0
    for (const target of paths) {
      if (running?.target === target || waiting.some((item) => item.target === target)) continue
      waiting.push({ key: 'preview-' + ++serial, target, kind })
      queued++
    }
    advance()
    return { queued, deduped: paths.length - queued }
  }
  function categories() {
    return ['movie', 'tv'].map((type) => {
      const entries = media.filter((item) => item.type === type)
      const children = [...new Set(entries.map((item) => item.category))].map((name) => {
        const members = entries.filter((item) => item.category === name)
        return {
          name,
          path: entries[0].category_dir + '/' + name,
          [type]: members.length,
          total: members.length,
          unscraped: members.filter((item) => item.status !== 'scraped').length,
        }
      })
      return {
        name: type === 'movie' ? '电影' : '电视剧',
        path: entries[0].category_dir,
        [type]: entries.length,
        total: entries.length,
        children,
      }
    })
  }
  function files(item) {
    return Array.from({ length: item.total_files }, (_, index) => {
      const season = Math.floor(index / 10) + 1
      const episode = (index % 10) + 1
      const name =
        item.type === 'tv'
          ? item.title.split(' (')[0] +
            '.S0' +
            season +
            'E' +
            String(episode).padStart(2, '0') +
            '.1080p.WEB-DL.strm'
          : item.title + (index ? '.1080p.BluRay.strm' : '.2160p.WEB-DL.strm')
      const status = item.status === 'scraped' || index < 3 ? 'scraped' : item.status
      return {
        path: item.path + '/' + name,
        name,
        episode,
        season_no: season,
        season_name: '第 ' + season + ' 季',
        size: 235,
        status,
        scraped: status === 'scraped',
        error_code: status === 'failed' ? 'timeout' : '',
        error_message: status === 'failed' ? '连接元数据服务超时' : '',
      }
    })
  }
  return {
    defaults: { baseURL: '/__preview/api' },
    async get(raw) {
      await delay()
      advance()
      const url = new URL(raw, 'http://preview.local')
      const endpoint = url.pathname.split('StrmScraper')[1] || ''
      const q = url.searchParams
      if (endpoint === '/items') return envelope(media)
      if (endpoint === '/categories') return envelope(categories())
      if (endpoint === '/overview')
        return envelope({
          total: media.length,
          movie: media.filter((item) => item.type === 'movie').length,
          tv: media.filter((item) => item.type === 'tv').length,
          music: 0,
          monitoring: true,
          categories: categories(),
          status: Object.fromEntries(
            ['scraped', 'pending', 'failed', 'skipped'].map((status) => [
              status,
              media.filter((item) => item.status === status).length,
            ]),
          ),
        })
      if (endpoint === '/queue')
        return envelope({
          running,
          queued: waiting,
          queued_total: waiting.length,
          stats,
          recent,
          busy: Boolean(running || waiting.length),
        })
      if (endpoint === '/files') {
        const item = media.find((item) => item.path === q.get('path'))
        if (!item) throw new Error('示例媒体不存在')
        return envelope({ type: item.type, files: files(item) })
      }
      if (endpoint === '/scan') {
        const scope = q.get('scope')
        const prefixes = (q.get('paths') || '').split(',').filter(Boolean)
        const candidates = media.filter(
          (item) =>
            (!prefixes.length || prefixes.some((prefix) => item.path.startsWith(prefix + '/'))) &&
            (q.get('overwrite') === 'true' || item.status !== 'scraped') &&
            (scope !== 'incremental' || item.status === 'pending'),
        )
        return envelope(enqueue(candidates.map((item) => item.path)))
      }
      if (endpoint === '/records/clear') {
        records = []
        return envelope({ cleared: true })
      }
      if (endpoint === '/records')
        return envelope(
          records.filter(
            (item) =>
              (!q.has('success') || item.success === (q.get('success') === 'true')) &&
              (!q.get('type') || item.type === q.get('type')) &&
              (!q.get('category') || item.category === q.get('category')) &&
              (!q.get('since') || item.time.slice(0, 10) >= q.get('since')) &&
              (!q.get('until') || item.time.slice(0, 10) <= q.get('until')),
          ),
        )
      throw new Error('未实现的预览接口：' + endpoint)
    },
    async post(raw, payload = {}) {
      await delay()
      advance()
      const endpoint = raw.split('StrmScraper')[1]
      if (endpoint === '/scrape')
        return envelope(enqueue(payload.paths || [], payload.target === 'file' ? 'file' : 'dir'))
      if (endpoint === '/retry_failed')
        return envelope(
          enqueue(media.filter((item) => item.status === 'failed').map((item) => item.path)),
        )
      if (endpoint === '/queue/cancel') {
        const before = waiting.length
        if (payload.mode === 'all') waiting.splice(0)
        else
          for (let i = waiting.length - 1; i >= 0; i--)
            if (
              waiting[i].target === payload.target ||
              waiting[i].target.startsWith(payload.target + '/')
            )
              waiting.splice(i, 1)
        const canceled = before - waiting.length
        stats.canceled += canceled
        return envelope({ canceled })
      }
      throw new Error('未实现的预览接口：' + endpoint)
    },
  }
}
