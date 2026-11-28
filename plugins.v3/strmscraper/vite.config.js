import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import federation from '@originjs/vite-plugin-federation'

export default defineConfig({
  plugins: [
    vue(),
    federation({
      name: 'StrmScraper',
      filename: 'remoteEntry.js',
      // 不再暴露 ./AppPage：侧栏全页入口已移除，全部能力收敛到 ./Page 详情组件
      exposes: {
        './Page': './src/components/Page.vue',
        './Config': './src/components/Config.vue',
      },
      shared: {
        vue: { requiredVersion: false, generate: false, singleton: true },
        vuetify: { requiredVersion: false, generate: false, singleton: true },
        'vuetify/styles': { requiredVersion: false, generate: false, singleton: true },
      },
      format: 'esm',
    }),
  ],
  build: {
    target: 'esnext',
    minify: false,
    cssCodeSplit: true,
    rollupOptions: {
      output: {
        // 公共图标的 JS/CSS 块也必须满足宿主静态资产白名单。
        chunkFileNames: (chunk) =>
          chunk.name.startsWith('__federation_')
            ? 'assets/[name]-[hash].js'
            : 'assets/_plugin-vue_export-helper-[name]-[hash].js',
        assetFileNames: (asset) =>
          asset.name?.startsWith('__federation_')
            ? 'assets/[name]-[hash][extname]'
            : 'assets/_plugin-vue_export-helper-[name]-[hash][extname]',
      },
    },
  },
  css: {
    postcss: {
      plugins: [
        {
          postcssPlugin: 'internal:charset-removal',
          AtRule: {
            charset: (atRule) => atRule.remove(),
          },
        },
        {
          postcssPlugin: 'vuetify-filter',
          Root(root) {
            const sourcePath = root.source?.input?.file?.replaceAll('\\', '/') || ''
            if (
              sourcePath.includes('/node_modules/vuetify/') ||
              sourcePath.includes('/node_modules/@mdi/')
            ) {
              root.nodes = []
              return
            }
            root.walkRules((rule) => {
              if (
                rule.selector &&
                (rule.selector.includes('.v-') || rule.selector.includes('.mdi-'))
              ) {
                rule.remove()
              }
            })
          },
        },
      ],
    },
  },
})
