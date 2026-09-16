<script setup>
import { onMounted, reactive, ref } from 'vue'

const props = defineProps({
  initialConfig: { type: Object, default: () => ({}) },
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: 'StrmScraper' },
  sourcePluginId: { type: String, default: '' },
})

const emit = defineEmits(['save', 'close', 'switch'])

const DEFAULTS = {
  enabled: false,
  onlyonce: false,
  overwrite: false,
  skip_scraped: true,
  mode: 'compatibility',
  monitor_dirs: '',
  exclude_keywords: '',
  cron_enabled: false,
  cron_expression: '0 4 * * *',
  sidebar_enabled: true,
}

const config = reactive({ ...DEFAULTS })
const scanning = ref(false)
const error = ref('')
const notice = ref('')

const MODES = [
  { title: '兼容模式（轮询，网络盘用）', value: 'compatibility' },
  { title: '性能模式（inotify，仅本地盘）', value: 'fast' },
]

onMounted(() => {
  const source = props.initialConfig || {}
  Object.keys(DEFAULTS).forEach(key => {
    if (key in source && source[key] !== undefined && source[key] !== null) config[key] = source[key]
  })
})

const apiCall = (method, path, payload) => {
  if (typeof props.api?.[method] === 'function') return props.api[method](`plugin/${props.pluginId}${path}`, payload)
  return Promise.reject(new Error('MoviePilot API 客户端未注入'))
}

function submit() {
  emit('save', { ...config })
}

function close() {
  emit('close')
}

async function scanNow(force) {
  scanning.value = true
  error.value = ''
  notice.value = ''
  try {
    await apiCall('get', force ? '/scan?force=true' : '/scan')
    notice.value = force ? '已在后台启动强制全量扫描' : '已在后台启动全量扫描（跳过已刮削）'
  } catch (scanError) {
    error.value = scanError?.message || '触发全量扫描失败'
  } finally {
    scanning.value = false
  }
}
</script>

<template>
  <div class="cfg">
    <div class="cfg-head">
      <h2>STRM 刮削配置</h2>
      <button class="cfg-close" @click="close">×</button>
    </div>

    <div class="cfg-body">
      <div v-if="error" class="cfg-alert is-error">{{ error }}</div>
      <div v-else-if="notice" class="cfg-alert is-ok">{{ notice }}</div>

      <div class="cfg-section">运行状态</div>
      <div class="cfg-grid">
        <label class="cfg-switch">
          <input v-model="config.enabled" type="checkbox">
          <span><b>启用插件</b><em>开启目录实时监控</em></span>
        </label>
        <label class="cfg-switch">
          <input v-model="config.onlyonce" type="checkbox">
          <span><b>立即全量扫描一次</b><em>保存后 3 秒执行一次</em></span>
        </label>
        <label class="cfg-switch">
          <input v-model="config.overwrite" type="checkbox">
          <span><b>覆盖已有元数据</b><em>重刮时覆盖已存在的 NFO 与图片</em></span>
        </label>
        <label class="cfg-switch">
          <input v-model="config.skip_scraped" type="checkbox">
          <span><b>全量扫描跳过已刮削</b><em>NFO 齐全的目录整目录跳过，图片不会被反复重下</em></span>
        </label>
        <label class="cfg-switch">
          <input v-model="config.sidebar_enabled" type="checkbox">
          <span><b>显示侧栏入口</b><em>在主界面左侧导航栏显示「STRM刮削」，关闭后仍可从插件中心进入</em></span>
        </label>
      </div>

      <div class="cfg-section">监控</div>
      <div class="cfg-field">
        <span class="cfg-label">监控模式</span>
        <select v-model="config.mode" class="cfg-select">
          <option v-for="item in MODES" :key="item.value" :value="item.value">{{ item.title }}</option>
        </select>
      </div>
      <div class="cfg-field">
        <span class="cfg-label">监控目录</span>
        <textarea v-model="config.monitor_dirs" class="cfg-textarea" rows="4" placeholder="每行一个目录"></textarea>
      </div>
      <div class="cfg-field">
        <span class="cfg-label">排除关键词</span>
        <textarea v-model="config.exclude_keywords" class="cfg-textarea" rows="2" placeholder="每行一个关键词（正则）"></textarea>
      </div>
      <p class="cfg-tip">网络挂载目录（CloudDrive2 / rclone / SMB 等）请选择兼容模式。</p>

      <div class="cfg-section">定时全量刷新</div>
      <div class="cfg-grid">
        <label class="cfg-switch">
          <input v-model="config.cron_enabled" type="checkbox">
          <span><b>启用定时全量刷新</b><em>由主程序调度器按 Cron 触发</em></span>
        </label>
      </div>
      <div class="cfg-field">
        <span class="cfg-label">Cron 表达式（分 时 日 月 周）</span>
        <input v-model="config.cron_expression" class="cfg-input" placeholder="如 0 4 * * * 表示每天 04:00">
      </div>
      <p class="cfg-tip">关闭或改表达式会自动重新注册，无需重启。</p>
    </div>

    <div class="cfg-foot">
      <button class="cfg-btn ghost" :disabled="scanning" @click="scanNow(false)">全量扫描</button>
      <button class="cfg-btn ghost" :disabled="scanning" @click="scanNow(true)">强制全量</button>
      <div class="cfg-foot-right">
        <button class="cfg-btn ghost" @click="close">取消</button>
        <button class="cfg-btn" @click="submit">保存</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cfg { display: flex; flex-direction: column; max-height: 78vh; font-family: inherit; }
.cfg-head {
  display: flex; align-items: center; padding: 16px 20px 12px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
}
.cfg-head h2 { font-size: 16px; font-weight: 700; margin: 0; }
.cfg-close {
  margin-left: auto; width: 30px; height: 30px; border-radius: 9px; cursor: pointer; font-size: 18px; line-height: 1;
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .14);
  background: transparent; color: inherit;
}
.cfg-body { flex: 1; overflow-y: auto; padding: 4px 20px 16px; }
.cfg-alert { border-radius: 10px; padding: 9px 13px; font-size: 13px; margin: 12px 0; }
.cfg-alert.is-error { background: rgba(229, 72, 77, .12); color: #E5484D; }
.cfg-alert.is-ok { background: rgba(22, 163, 74, .12); color: #16A34A; }
.cfg-section {
  font-size: 12px; font-weight: 700; letter-spacing: .04em; margin: 18px 0 10px;
  color: rgb(var(--v-theme-primary, 124, 92, 252));
}
.cfg-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px; }
.cfg-switch {
  display: flex; align-items: flex-start; gap: 10px; padding: 10px 12px; border-radius: 11px; cursor: pointer;
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
}
.cfg-switch input { width: 16px; height: 16px; margin-top: 2px; flex-shrink: 0; accent-color: rgb(var(--v-theme-primary, 124, 92, 252)); cursor: pointer; }
.cfg-switch b { display: block; font-size: 13px; font-weight: 600; }
.cfg-switch em {
  display: block; font-style: normal; font-size: 11.5px; margin-top: 2px;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .6));
}
.cfg-field { margin-bottom: 12px; }
.cfg-label {
  display: block; font-size: 12px; margin-bottom: 5px;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .7));
}
.cfg-input, .cfg-select, .cfg-textarea {
  width: 100%; padding: 9px 12px; font-size: 13px; font-family: inherit; border-radius: 10px; outline: none;
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .16);
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  color: rgb(var(--v-theme-on-surface, 27, 29, 41));
}
.cfg-textarea { resize: vertical; }
.cfg-input:focus, .cfg-select:focus, .cfg-textarea:focus {
  border-color: rgb(var(--v-theme-primary, 124, 92, 252));
}
.cfg-tip {
  font-size: 11.5px; margin: -4px 0 0;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .55));
}
.cfg-foot {
  display: flex; align-items: center; gap: 9px; padding: 13px 20px;
  border-top: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
}
.cfg-foot-right { margin-left: auto; display: flex; gap: 9px; }
.cfg-btn {
  padding: 8px 16px; border-radius: 10px; font-size: 13px; font-weight: 600; font-family: inherit; cursor: pointer;
  border: none; background: rgb(var(--v-theme-primary, 124, 92, 252));
  color: rgb(var(--v-theme-on-primary, 255, 255, 255));
}
.cfg-btn:disabled { opacity: .45; cursor: not-allowed; }
.cfg-btn.ghost {
  background: transparent;
  color: rgb(var(--v-theme-primary, 124, 92, 252));
  border: 1px solid rgba(var(--v-theme-primary, 124, 92, 252), .35);
}
</style>
