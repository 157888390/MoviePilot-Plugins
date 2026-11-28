<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { unwrap } from '../lib/strm.js'
import StrmIcon from './StrmIcon.vue'

const props = defineProps({
  initialConfig: { type: Object, default: () => ({}) },
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: 'StrmScraper' },
  sourcePluginId: { type: String, default: '' },
})

const emit = defineEmits(['save', 'close', 'switch'])

// 字段必须与后端 init_plugin / __save_config 读取的键完全一致，
// 后端不认的键（例如历史遗留的 skip_scraped / cron_* / sidebar_enabled）不再保留，
// 否则界面会给出「看起来能配、其实无效」的死开关。
const DEFAULTS = {
  enabled: false,
  onlyonce: false,
  overwrite: false,
  mode: 'compatibility',
  monitor_dirs: '',
  exclude_keywords: '',
  record_enabled: true,
  cron_expression: '',
}

const config = reactive({ ...DEFAULTS })
const scanning = ref(false)
const error = ref('')
const notice = ref('')

// cron 校验。宿主是把这段字符串**直接当 APScheduler 的触发器**用的，只有 CronTrigger
// 实例或 "cron"/"interval"/"date" 别名才认，所以手填的表达式必须是标准五段式；
// 少写几段（例如只想填「0 4」）不会定时跑，而是让宿主直接报服务注册失败。
// 这里只挡字段数与字符集这类手误，语义正确性仍以后端 CronTrigger.from_crontab 为准。
const CRON_FIELD = /^[0-9*,\-/A-Za-z]+$/
const cronError = computed(() => {
  const raw = String(config.cron_expression || '').trim()
  if (!raw) return ''
  const fields = raw.split(/\s+/)
  if (fields.length !== 5) return `必须是 5 段（分 时 日 月 周），当前只有 ${fields.length} 段`
  if (!fields.every((field) => CRON_FIELD.test(field))) return '只允许数字、* , - / 以及英文缩写'
  return ''
})

/** 失焦时把多余空白收成一个空格，避免「0  3 * * *」这类看不出问题的写法。 */
function normalizeCron() {
  const raw = String(config.cron_expression || '').trim()
  config.cron_expression = raw ? raw.split(/\s+/).join(' ') : ''
}

onMounted(() => {
  const source = props.initialConfig || {}
  Object.keys(DEFAULTS).forEach((key) => {
    if (key in source && source[key] !== undefined && source[key] !== null)
      config[key] = source[key]
  })
})

const apiCall = (method, path, payload) => {
  if (typeof props.api?.[method] === 'function')
    return props.api[method](`plugin/${props.pluginId}${path}`, payload)
  return Promise.reject(new Error('MoviePilot API 客户端未注入'))
}

function submit() {
  if (cronError.value) return
  normalizeCron()
  emit('save', { ...config })
}

function close() {
  emit('close')
}

/**
 * 手动触发一次全量扫描并入队。
 *
 * 是否覆盖直接复用本页「覆盖已有元数据」开关的当前取值，因此不再需要「全量 / 强制全量」
 * 两个按钮 —— 它们本来就只差这个布尔值，并排摆放极易点错并触发全库重刮。
 */
async function scanNow() {
  scanning.value = true
  error.value = ''
  notice.value = ''
  try {
    unwrap(await apiCall('get', `/scan?overwrite=${Boolean(config.overwrite)}`))
    notice.value = config.overwrite
      ? '已加入队列：全量覆盖重刮'
      : '已加入队列：全量补缺失（跳过已有元数据）'
  } catch (scanError) {
    error.value = scanError?.message || '触发全量扫描失败'
  } finally {
    scanning.value = false
  }
}
</script>

<template>
  <form class="cfg" @submit.prevent="submit">
    <header class="cfg-head">
      <span class="cfg-mark"><StrmIcon name="settings" :size="24" /></span>
      <div>
        <span class="cfg-eyebrow">PLUGIN SETTINGS</span>
        <h2>刮削设置</h2>
        <p>按你的存储方式，让媒体资料自动保持完整。</p>
      </div>
      <button type="button" class="cfg-close" aria-label="关闭设置" @click="close">
        <StrmIcon name="close" :size="20" />
      </button>
    </header>
    <div class="cfg-body">
      <div v-if="error" class="cfg-alert is-error" role="alert">{{ error }}</div>
      <div v-else-if="notice" class="cfg-alert is-ok" role="status">{{ notice }}</div>
      <section class="cfg-section">
        <div class="cfg-section-head">
          <span>01</span>
          <div>
            <h3>运行方式</h3>
            <p>设置自动监控与刮削行为</p>
          </div>
        </div>
        <div class="cfg-grid">
          <label
            v-for="option in [
              { key: 'enabled', title: '启用目录监控', hint: '发现新增文件后自动刮削' },
              { key: 'record_enabled', title: '记录刮削历史', hint: '保留每次刮削的时间与结果' },
              { key: 'onlyonce', title: '保存后扫描一次', hint: '保存配置后执行一次全量扫描' },
              { key: 'overwrite', title: '覆盖已有元数据', hint: '全量扫描时重新下载已有资料' },
            ]"
            :key="option.key"
            class="cfg-switch"
          >
            <span>
              <b>{{ option.title }}</b>
              <small>{{ option.hint }}</small>
            </span>
            <input v-model="config[option.key]" type="checkbox" role="switch" />
            <i class="cfg-toggle" aria-hidden="true"></i>
          </label>
        </div>
      </section>
      <section class="cfg-section">
        <div class="cfg-section-head">
          <span>02</span>
          <div>
            <h3>监控目录</h3>
            <p>选择适合你存储环境的监控方式</p>
          </div>
        </div>
        <div class="cfg-modes">
          <label :class="['cfg-mode', config.mode === 'compatibility' && 'active']">
            <input v-model="config.mode" type="radio" value="compatibility" name="monitor-mode" />
            <StrmIcon name="folder" :size="21" />
            <span>
              <b>
                兼容模式
                <em>推荐</em>
              </b>
              <small>适用于 SMB、rclone、CloudDrive2 等网络挂载</small>
            </span>
            <i></i>
          </label>
          <label :class="['cfg-mode', config.mode === 'fast' && 'active']">
            <input v-model="config.mode" type="radio" value="fast" name="monitor-mode" />
            <StrmIcon name="activity" :size="21" />
            <span>
              <b>性能模式</b>
              <small>使用文件事件监控，适用于本地磁盘</small>
            </span>
            <i></i>
          </label>
        </div>
        <label class="cfg-field">
          <span class="cfg-label">
            监控路径
            <small>每行一个绝对路径</small>
          </span>
          <textarea
            v-model="config.monitor_dirs"
            class="cfg-textarea cfg-code"
            rows="3"
            placeholder="/media/电影&#10;/media/电视剧"
            spellcheck="false"
          ></textarea>
        </label>
        <label class="cfg-field">
          <span class="cfg-label">
            排除关键词
            <small>可选，支持正则表达式</small>
          </span>
          <textarea
            v-model="config.exclude_keywords"
            class="cfg-textarea"
            rows="2"
            placeholder="每行一个关键词，例如：预告片、花絮"
          ></textarea>
        </label>
      </section>
      <section class="cfg-section">
        <div class="cfg-section-head">
          <span>03</span>
          <div>
            <h3>定时扫描</h3>
            <p>定期检查目录，补齐遗漏的媒体资料</p>
          </div>
        </div>
        <label class="cfg-field">
          <span class="cfg-label">
            Cron 表达式
            <small>留空则关闭定时扫描</small>
          </span>
          <div class="cfg-cron-wrap">
            <StrmIcon name="clock" :size="17" />
            <input
              v-model="config.cron_expression"
              class="cfg-input cfg-code"
              :class="{ 'is-invalid': cronError }"
              type="text"
              placeholder="0 3 * * *"
              :aria-invalid="!!cronError"
              aria-describedby="cfg-cron-help"
              @blur="normalizeCron"
            />
          </div>
          <p v-if="cronError" id="cfg-cron-help" class="cfg-error">{{ cronError }}</p>
          <p v-else id="cfg-cron-help" class="cfg-field-tip">
            标准五段格式：分 · 时 · 日 · 月 · 周
          </p>
        </label>
        <div class="cfg-presets">
          <span>快捷设置</span>
          <button type="button" @click="config.cron_expression = '0 3 * * *'">每天 03:00</button>
          <button type="button" @click="config.cron_expression = '0 */6 * * *'">每 6 小时</button>
          <button type="button" @click="config.cron_expression = ''">关闭定时</button>
        </div>
      </section>
    </div>
    <footer class="cfg-foot">
      <button type="button" class="cfg-scan" :disabled="scanning" @click="scanNow">
        <StrmIcon name="refresh" :size="15" />
        {{ scanning ? '正在入队…' : config.overwrite ? '立即覆盖扫描' : '立即补全缺失' }}
      </button>
      <div class="cfg-foot-right">
        <button type="button" class="cfg-btn ghost" @click="close">取消</button>
        <button type="submit" class="cfg-btn" :disabled="!!cronError">
          <StrmIcon name="check" :size="15" />
          保存设置
        </button>
      </div>
    </footer>
  </form>
</template>
<style scoped>
.cfg {
  --cfg-primary: rgb(var(--v-theme-primary, 45, 113, 86));
  --cfg-surface: rgb(var(--v-theme-surface, 255, 255, 255));
  --cfg-text: rgb(var(--v-theme-on-surface, 39, 48, 45));
  --cfg-muted: rgba(var(--v-theme-on-surface, 39, 48, 45), 0.47);
  --cfg-line: rgba(var(--v-theme-on-surface, 39, 48, 45), 0.09);
  --cfg-wash: color-mix(in srgb, var(--cfg-surface) 97%, var(--cfg-text));
  display: flex;
  flex-direction: column;
  max-height: min(86vh, 850px);
  color: var(--cfg-text);
  background: var(--cfg-surface);
  font-size: 12px;
  line-height: 1.6;
  font-family: inherit;
}
.cfg * {
  box-sizing: border-box;
}
.cfg button,
.cfg input,
.cfg textarea {
  font: inherit;
}
.cfg button {
  cursor: pointer;
}
.cfg button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}
.cfg button:focus-visible,
.cfg input:focus-visible,
.cfg textarea:focus-visible {
  outline: 2px solid var(--cfg-primary);
  outline-offset: 3px;
}
.cfg-head {
  display: flex;
  align-items: center;
  gap: 15px;
  padding: 26px 30px 22px;
  border-bottom: 1px solid var(--cfg-line);
}
.cfg-mark {
  display: grid;
  place-items: center;
  width: 47px;
  height: 47px;
  border: 1px solid color-mix(in srgb, var(--cfg-primary) 16%, transparent);
  background: color-mix(in srgb, var(--cfg-primary) 6%, var(--cfg-surface));
  color: var(--cfg-primary);
  border-radius: 12px;
}
.cfg-eyebrow {
  font-size: 9px;
  letter-spacing: 1.6px;
  color: var(--cfg-muted);
}
.cfg-head h2 {
  font-size: 21px;
  font-weight: 600;
  margin: 1px 0 3px;
}
.cfg-head p {
  font-size: 11px;
  color: var(--cfg-muted);
  margin: 0;
}
.cfg-close {
  margin-left: auto;
  display: grid;
  place-items: center;
  border: 0;
  background: transparent;
  color: var(--cfg-muted);
  width: 28px;
  height: 28px;
  border-radius: 6px;
}
.cfg-close:hover {
  background: var(--cfg-wash);
}
.cfg-body {
  min-height: 0;
  overflow-y: auto;
  padding: 0 30px 25px;
  scrollbar-width: thin;
}
.cfg-section {
  padding-top: 25px;
}
.cfg-section + .cfg-section {
  margin-top: 24px;
  border-top: 1px solid var(--cfg-line);
}
.cfg-section-head {
  display: flex;
  align-items: center;
  gap: 11px;
  margin-bottom: 17px;
}
.cfg-section-head > span {
  font-size: 12px;
  color: var(--cfg-muted);
  border: 1px solid var(--cfg-line);
  border-radius: 6px;
  width: 28px;
  height: 30px;
  display: grid;
  place-items: center;
  font-variant-numeric: tabular-nums;
}
.cfg-section-head h3 {
  font-size: 13px;
  font-weight: 550;
  margin: 0;
}
.cfg-section-head p {
  font-size: 10px;
  color: var(--cfg-muted);
  margin: 3px 0 0;
}
.cfg-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.cfg-switch {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 13px;
  border: 1px solid var(--cfg-line);
  border-radius: 7px;
  cursor: pointer;
  position: relative;
}
.cfg-switch b {
  display: block;
  font-size: 12px;
  font-weight: 500;
}
.cfg-switch small {
  display: block;
  font-size: 10px;
  color: var(--cfg-muted);
  margin-top: 3px;
}
.cfg-switch input {
  position: absolute;
  right: 13px;
  width: 29px;
  height: 17px;
  opacity: 0;
}
.cfg-toggle {
  width: 29px;
  height: 17px;
  flex-shrink: 0;
  background: color-mix(in srgb, var(--cfg-text) 17%, var(--cfg-surface));
  border-radius: 20px;
  position: relative;
  transition: background 0.2s;
  pointer-events: none;
}
.cfg-toggle::after {
  content: '';
  position: absolute;
  width: 13px;
  height: 13px;
  border-radius: 50%;
  top: 2px;
  left: 2px;
  background: white;
  box-shadow: 0 1px 3px #00000020;
  transition: transform 0.2s;
}
.cfg-switch input:checked + .cfg-toggle {
  background: var(--cfg-primary);
}
.cfg-switch input:checked + .cfg-toggle::after {
  transform: translateX(12px);
}
.cfg-switch input:focus-visible + .cfg-toggle {
  outline: 2px solid var(--cfg-primary);
  outline-offset: 3px;
}
.cfg-modes {
  display: grid;
  gap: 9px;
  margin-bottom: 18px;
}
.cfg-mode {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 13px 15px;
  border: 1px solid var(--cfg-line);
  border-radius: 7px;
  cursor: pointer;
  position: relative;
}
.cfg-mode > svg {
  color: var(--cfg-muted);
}
.cfg-mode input {
  position: absolute;
  opacity: 0;
  width: 100%;
  height: 100%;
  inset: 0;
  pointer-events: none;
}
.cfg-mode.active {
  border-color: color-mix(in srgb, var(--cfg-primary) 40%, var(--cfg-line));
  background: color-mix(in srgb, var(--cfg-primary) 3%, var(--cfg-surface));
}
.cfg-mode.active > svg {
  color: var(--cfg-primary);
}
.cfg-mode b {
  font-weight: 500;
  display: block;
  font-size: 12px;
}
.cfg-mode small {
  display: block;
  color: var(--cfg-muted);
  font-size: 10px;
  margin-top: 3px;
}
.cfg-mode em {
  margin-left: 5px;
  font-style: normal;
  font-size: 9px;
  color: var(--cfg-primary);
  background: color-mix(in srgb, var(--cfg-primary) 8%, var(--cfg-surface));
  padding: 1px 4px;
  border-radius: 3px;
}
.cfg-mode > i {
  margin-left: auto;
  width: 13px;
  height: 13px;
  border-radius: 50%;
  border: 1px solid var(--cfg-line);
  flex-shrink: 0;
}
.cfg-mode.active > i {
  border: 4px solid var(--cfg-primary);
  background: var(--cfg-surface);
}
.cfg-mode:has(input:focus-visible) {
  outline: 2px solid var(--cfg-primary);
  outline-offset: 3px;
}
.cfg-field {
  display: block;
  margin-top: 15px;
}
.cfg-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  margin-bottom: 7px;
}
.cfg-label small {
  color: var(--cfg-muted);
  font-size: 10px;
}
.cfg-input,
.cfg-textarea {
  width: 100%;
  color: var(--cfg-text);
  background: var(--cfg-wash);
  border: 1px solid var(--cfg-line);
  border-radius: 6px;
  padding: 10px 12px;
  outline: none;
  font-size: 12px !important;
}
.cfg-code {
  font-family: 'Cascadia Code', Consolas, monospace !important;
  font-size: 12px !important;
}
.cfg-textarea {
  display: block;
  resize: vertical;
  line-height: 1.8;
  min-height: 70px;
}
.cfg-input:focus,
.cfg-textarea:focus {
  border-color: var(--cfg-primary);
}
.cfg-input::placeholder,
.cfg-textarea::placeholder {
  color: var(--cfg-muted);
  opacity: 0.65;
}
.cfg-cron-wrap {
  position: relative;
}
.cfg-cron-wrap > svg {
  position: absolute;
  left: 12px;
  top: 12px;
  color: var(--cfg-muted);
}
.cfg-cron-wrap input {
  padding-left: 38px;
}
.cfg-error {
  font-size: 11px;
  color: #b95f56;
  margin: 6px 0 0;
}
.cfg-field-tip {
  font-size: 10px;
  color: var(--cfg-muted);
  margin: 6px 0 0;
}
.cfg-input.is-invalid {
  border-color: #b95f56;
}
.cfg-presets {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 12px;
}
.cfg-presets span {
  font-size: 10px;
  color: var(--cfg-muted);
  margin-right: 3px;
}
.cfg-presets button {
  padding: 3px 7px;
  font-size: 10px;
  border: 1px solid var(--cfg-line);
  background: transparent;
  border-radius: 4px;
  color: var(--cfg-muted);
}
.cfg-presets button:hover {
  border-color: var(--cfg-primary);
  color: var(--cfg-primary);
}
.cfg-alert {
  padding: 10px 12px;
  border-radius: 6px;
  font-size: 12px;
  margin-top: 20px;
}
.cfg-alert.is-error {
  background: #b95f560d;
  color: #b95f56;
}
.cfg-alert.is-ok {
  background: color-mix(in srgb, var(--cfg-primary) 7%, var(--cfg-surface));
  color: var(--cfg-primary);
}
.cfg-foot {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 30px;
  border-top: 1px solid var(--cfg-line);
}
.cfg-foot-right {
  margin-left: auto;
  display: flex;
  gap: 8px;
}
.cfg-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 15px;
  min-height: 35px;
  border-radius: 6px;
  border: 1px solid transparent;
  background: var(--cfg-primary);
  color: rgb(var(--v-theme-on-primary, 255, 255, 255));
  font-size: 12px !important;
}
.cfg-btn.ghost {
  background: transparent;
  color: var(--cfg-text);
  border-color: var(--cfg-line);
}
.cfg-scan {
  display: flex;
  gap: 6px;
  align-items: center;
  background: transparent;
  border: 0;
  color: var(--cfg-muted);
  font-size: 11px !important;
  padding: 5px 0;
}
.cfg-scan:hover {
  color: var(--cfg-primary);
}
@media (max-width: 640px) {
  .cfg {
    max-height: 90vh;
  }
  .cfg-head {
    padding: 21px 19px;
    gap: 12px;
  }
  .cfg-mark {
    width: 39px;
    height: 42px;
  }
  .cfg-head h2 {
    font-size: 19px;
  }
  .cfg-head p {
    font-size: 9px;
  }
  .cfg-body {
    padding-inline: 19px;
  }
  .cfg-grid {
    grid-template-columns: 1fr;
    gap: 8px;
  }
  .cfg-section-head {
    margin-bottom: 14px;
  }
  .cfg-foot {
    padding: 15px 19px;
  }
  .cfg-scan {
    font-size: 9px !important;
  }
  .cfg-btn {
    padding-inline: 10px;
    font-size: 11px !important;
  }
  .cfg-label {
    gap: 5px;
  }
  .cfg-label small {
    font-size: 9px;
  }
}
</style>
