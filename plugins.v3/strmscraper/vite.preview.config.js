import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { PREVIEW_MEDIA } from './src/preview/data.js'

/** 独立预览入口，示例接口和海报缓存不会进入正式联邦构建。 */
export default defineConfig({
  plugins: [
    vue(),
    {
      name: 'strmscraper-local-preview',
      configureServer(server) {
        server.middlewares.use('/__preview/api/plugin/StrmScraper/cover', async (req, res) => {
          const url = new URL(req.url, 'http://localhost')
          const item = PREVIEW_MEDIA.find((entry) => entry.path === url.searchParams.get('path'))
          if (!item) {
            res.statusCode = 404
            res.end()
            return
          }
          try {
            const image = await readFile(
              fileURLToPath(new URL('./.preview/posters/' + item.id + '.jpg', import.meta.url)),
            )
            res.setHeader('Content-Type', 'image/jpeg')
            res.setHeader('Cache-Control', 'public, max-age=3600')
            res.end(image)
          } catch {
            res.statusCode = 302
            res.setHeader('Location', 'https://image.tmdb.org/t/p/w342' + item.poster)
            res.end()
          }
        })
      },
    },
  ],
  server: { host: '127.0.0.1', port: 5178, strictPort: true },
})
