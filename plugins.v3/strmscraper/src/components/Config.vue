<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { unwrap } from '../lib/strm.js'

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

const MODES = [
  { title: '兼容模式（轮询，网络盘用）', value: 'compatibility' },
  { title: '性能模式（inotify，仅本地盘）', value: 'fast' },
]

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
  if (!fields.every(field => CRON_FIELD.test(field))) return '只允许数字、* , - / 以及英文缩写'
  return ''
})

/** 失焦时把多余空白收成一个空格，避免「0  3 * * *」这类看不出问题的写法。 */
function normalizeCron() {
  const raw = String(config.cron_expression || '').trim()
  config.cron_expression = raw ? raw.split(/\s+/).join(' ') : ''
}

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
          <input v-model="config.record_enabled" type="checkbox">
          <span><b>记录刮削历史</b><em>把每次刮削的时间、目标、分类与结果落到插件数据目录</em></span>
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
      <div class="cfg-field">
        <span class="cfg-label">定时扫描（cron 表达式）</span>
        <input
          v-model="config.cron_expression"
          class="cfg-input"
          :class="{ 'is-invalid': cronError }"
          type="text"
          placeholder="留空关闭，例如 0 3 * * * 表示每天 03:00 全量补漏扫描一次"
          @blur="normalizeCron"
        >
        <p v-if="cronError" class="cfg-error">{{ cronError }}</p>
      </div>
      <p class="cfg-tip">网络挂载目录（CloudDrive2 / rclone / SMB 等）请选择兼容模式。</p>

      <div class="cfg-section">界面</div>
      <p class="cfg-tip">
        海报墙、分类筛选、单集/版本刮削与刮削记录都在插件详情页内，不再注册主界面侧栏入口。
      </p>
    </div>

    <div class="cfg-foot">
      <button
        class="cfg-btn ghost"
        :disabled="scanning"
        :title="config.overwrite ? '重下全部 NFO 与图片' : '已有元数据不动，只补未刮的'"
        @click="scanNow"
      >全量扫描{{ config.overwrite ? '（覆盖重刮）' : '（仅补缺失）' }}</button>
      <div class="cfg-foot-right">
        <button class="cfg-btn ghost" @click="close">取消</button>
        <button
          class="cfg-btn"
          :disabled="!!cronError"
          :title="cronError || '保存配置'"
          @click="submit"
        >保存</button>
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
.cfg-input.is-invalid { border-color: #E5484D; }
.cfg-error { font-size: 11.5px; color: #E5484D; margin: 5px 0 0; }
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
