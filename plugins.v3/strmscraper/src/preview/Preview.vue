<script setup>
import { nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import Page from '../components/Page.vue'
import Config from '../components/Config.vue'
import StrmIcon from '../components/StrmIcon.vue'
import { createPreviewApi } from './api.js'
const dark = ref(false)
const api = ref(createPreviewApi())
const revision = ref(0)
const settings = ref(false)
const closed = ref(false)
const dialogEl = ref(null)
let settingsReturnFocus
watch(settings, async (open) => {
  if (open) {
    settingsReturnFocus = document.activeElement
    await nextTick()
    dialogEl.value?.querySelector('button')?.focus()
  } else if (settingsReturnFocus?.isConnected) {
    await nextTick()
    settingsReturnFocus.focus()
  }
})
function trapSettingsFocus(event) {
  if (event.key !== 'Tab') return
  const controls = [
    ...dialogEl.value.querySelectorAll(
      'button:not(:disabled), input:not(:disabled), textarea:not(:disabled)',
    ),
  ]
  if (event.shiftKey && document.activeElement === controls[0]) {
    event.preventDefault()
    controls.at(-1)?.focus()
  } else if (!event.shiftKey && document.activeElement === controls.at(-1)) {
    event.preventDefault()
    controls[0]?.focus()
  }
}
const toast = ref('')
let toastTimer
const config = reactive({
  enabled: true,
  record_enabled: true,
  onlyonce: false,
  overwrite: false,
  mode: 'compatibility',
  monitor_dirs: '/media/电影\n/media/电视剧',
  exclude_keywords: '预告片\n花絮',
  cron_expression: '0 3 * * *',
})
function reset() {
  api.value = createPreviewApi()
  revision.value++
  closed.value = false
  settings.value = false
  notify('示例数据已重置')
}
function notify(message) {
  toast.value = message
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => {
    toast.value = ''
  }, 3200)
}
function save(value) {
  Object.assign(config, value)
  settings.value = false
  notify('演示设置已保存，仅在本次预览中生效')
}
onBeforeUnmount(() => clearTimeout(toastTimer))
</script>
<template>
  <div :class="['preview', dark && 'is-dark']">
    <div class="preview-bar">
      <div class="preview-title">
        <span class="preview-mark"></span>
        <b>STRM Scraper</b>
        <span class="preview-slash">/</span>
        <span>界面预览</span>
        <span class="preview-badge">示例数据</span>
      </div>
      <div class="preview-tools">
        <button @click="reset">
          <StrmIcon name="refresh" :size="13" />
          重置演示
        </button>
        <button :aria-label="dark ? '切换浅色主题' : '切换深色主题'" @click="dark = !dark">
          <StrmIcon :name="dark ? 'sun' : 'moon'" :size="15" />
          <span>{{ dark ? '浅色模式' : '深色模式' }}</span>
        </button>
      </div>
    </div>
    <div class="preview-frame" :inert="settings || undefined">
      <Page
        v-if="!closed"
        :key="revision"
        :api="api"
        plugin-id="StrmScraper"
        @switch="settings = true"
        @close="closed = true"
      />
      <div v-else class="preview-closed">
        <StrmIcon name="library" :size="40" />
        <h2>预览已收起</h2>
        <p>示例操作不会影响真实媒体文件。</p>
        <button @click="closed = false">重新打开媒体库</button>
      </div>
    </div>
    <div v-if="settings" class="preview-modal" @keydown.esc="settings = false">
      <button class="preview-backdrop" aria-label="关闭配置弹窗" @click="settings = false"></button>
      <div
        ref="dialogEl"
        class="preview-dialog"
        @keydown="trapSettingsFocus"
        role="dialog"
        aria-modal="true"
        aria-label="刮削设置"
      >
        <Config :initial-config="config" :api="api" @save="save" @close="settings = false" />
      </div>
    </div>
    <Transition name="toast">
      <div v-if="toast" class="preview-toast" role="status">
        <StrmIcon name="checkCircle" :size="17" />
        {{ toast }}
      </div>
    </Transition>
  </div>
</template>
<style>
html,
body,
#app {
  margin: 0;
  min-width: 340px;
  min-height: 100%;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft YaHei', sans-serif;
}
body {
  background: #edf0ed;
}
.preview {
  --v-theme-primary: 45, 113, 86;
  --v-theme-surface: 255, 255, 255;
  --v-theme-on-surface: 39, 48, 45;
  --preview-bg: #eef1ee;
  --preview-muted: #89928c;
  background: var(--preview-bg);
  min-height: 100vh;
  padding: 0 18px 18px;
  color: rgb(var(--v-theme-on-surface));
  box-sizing: border-box;
}
.preview.is-dark {
  --v-theme-primary: 131, 181, 154;
  --v-theme-on-primary: 22, 42, 31;
  --v-theme-surface: 29, 36, 33;
  --v-theme-on-surface: 222, 228, 224;
  --preview-bg: #131a16;
  --preview-muted: #87958c;
  color-scheme: dark;
}
.preview button {
  font-family: inherit;
  cursor: pointer;
}
.preview-bar {
  height: 47px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  font-size: 10px;
  padding: 0 6px;
  color: var(--preview-muted);
}
.preview-title {
  display: flex;
  align-items: center;
  gap: 11px;
}
.preview-title b {
  font-weight: 600;
  color: rgb(var(--v-theme-on-surface));
  letter-spacing: -0.3px;
}
.preview-mark {
  width: 5px;
  height: 5px;
  background: #649b7c;
  border-radius: 50%;
}
.preview-slash {
  opacity: 0.45;
}
.preview-badge {
  border: 1px solid #8b9b8d33;
  border-radius: 4px;
  padding: 1px 5px;
  font-size: 8px;
}
.preview-tools {
  display: flex;
  align-items: center;
  gap: 13px;
}
.preview-tools button {
  display: flex;
  gap: 5px;
  align-items: center;
  background: transparent;
  border: 0;
  padding: 5px 0;
  font-size: 9px;
  color: var(--preview-muted);
}
.preview-tools button:hover {
  color: rgb(var(--v-theme-primary));
}
.preview-frame {
  background: rgb(var(--v-theme-surface));
  border: 1px solid #72907a20;
  box-shadow: 0 8px 40px #2b4e3620;
  border-radius: 12px;
  overflow: hidden;
}
.preview-closed {
  height: 80vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 15px;
  color: var(--preview-muted);
}
.preview-closed h2 {
  margin: 0;
  font-size: 23px;
  font-weight: 500;
}
.preview-closed p {
  font-size: 12px;
  margin: 0 0 10px;
}
.preview-closed button {
  background: rgb(var(--v-theme-primary));
  color: white;
  border: 0;
  border-radius: 6px;
  padding: 10px 20px;
}
.preview-modal {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.preview-backdrop {
  position: absolute;
  inset: 0;
  background: #11231955;
  backdrop-filter: blur(5px);
  border: 0;
}
.preview-dialog {
  width: min(680px, 100%);
  position: relative;
  border-radius: 13px;
  overflow: hidden;
  border: 1px solid #b6c3ba30;
  box-shadow: 0 25px 100px #00000038;
}
.preview-toast {
  position: fixed;
  bottom: 29px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 18px;
  border-radius: 8px;
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-primary));
  box-shadow: 0 5px 25px #00000022;
  border: 1px solid #72907a30;
  font-size: 11px;
  z-index: 110;
  white-space: nowrap;
}
.toast-enter-active,
.toast-leave-active {
  transition: opacity 0.2s;
}
.toast-enter-from,
.toast-leave-to {
  opacity: 0;
}
@media (max-width: 640px) {
  .preview {
    padding: 0 7px 7px;
  }
  .preview-bar {
    padding: 0 5px;
    height: 41px;
    font-size: 9px;
    gap: 6px;
  }
  .preview-title {
    gap: 6px;
  }
  .preview-title > span:nth-last-child(2) {
    display: none;
  }
  .preview-tools {
    gap: 8px;
  }
  .preview-tools button {
    font-size: 8px;
  }
  .preview-tools button:last-child span {
    display: none;
  }
  .preview-modal {
    padding: 10px;
  }
  .preview-toast {
    max-width: 90%;
    white-space: normal;
    font-size: 10px;
  }
}
</style>
