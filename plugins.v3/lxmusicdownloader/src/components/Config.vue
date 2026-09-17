<script setup>
import { onMounted, reactive, ref } from 'vue'
import { SOURCES, QUALITIES, makeApiCall, unwrap } from '../lib/lx.js'

const props = defineProps({
  initialConfig: { type: Object, default: () => ({}) },
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: 'LxMusicDownloader' },
  sourcePluginId: { type: String, default: '' },
})

const emit = defineEmits(['save', 'close', 'switch'])

const DEFAULTS = {
  enabled: false,
  host: 'http://127.0.0.1:23332',
  username: '',
  password: '',
  token: '',
  source: 'kw',
  quality: '320k',
  download_path: '',
  name_template: '{name} - {singer}',
  max_results: 10,
  subdir_by_artist: true,
  save_cover: false,
  embed_tag: true,
  embed_lyric: true,
  use_server_cache: false,
  sidebar_enabled: true,
}

const config = reactive({ ...DEFAULTS })
const testing = ref(false)
const error = ref('')
const notice = ref('')

const apiCall = makeApiCall(props.api, props.pluginId)

onMounted(() => {
  const source = props.initialConfig || {}
  Object.keys(DEFAULTS).forEach(key => {
    if (key in source && source[key] !== undefined && source[key] !== null) config[key] = source[key]
  })
})

function submit() {
  emit('save', { ...config })
}

function close() {
  emit('close')
}

async function testConnection() {
  testing.value = true
  error.value = ''
  notice.value = ''
  try {
    const data = unwrap(await apiCall('get', '/verify'))
    if (data?.valid) notice.value = `连接正常，鉴权用户：${data.username}`
    else if (config.token) error.value = 'Token 无效或未生效（请确认用户名与 token 匹配）'
    else error.value = '未配置凭据，将以公开用户访问，无法解析直链'
  } catch (testError) {
    error.value = `连接失败：${testError?.message || testError}`
  } finally {
    testing.value = false
  }
}
</script>

<template>
  <div class="cfg">
    <div class="cfg-head">
      <h2>LX 音源下载配置</h2>
      <button class="cfg-close" @click="close">×</button>
    </div>

    <div class="cfg-body">
      <div v-if="error" class="cfg-alert is-error">{{ error }}</div>
      <div v-else-if="notice" class="cfg-alert is-ok">{{ notice }}</div>

      <div class="cfg-section">运行状态</div>
      <div class="cfg-grid">
        <label class="cfg-switch">
          <input v-model="config.enabled" type="checkbox">
          <span><b>启用插件</b><em>关闭后远程命令与接口不再响应</em></span>
        </label>
        <label class="cfg-switch">
          <input v-model="config.sidebar_enabled" type="checkbox">
          <span><b>显示侧栏入口</b><em>在主界面左侧导航栏显示「LX 音源下载」，关闭后仍可从插件中心进入</em></span>
        </label>
      </div>

      <div class="cfg-section">服务端</div>
      <div class="cfg-field">
        <span class="cfg-label">LX 服务端地址</span>
        <input v-model="config.host" class="cfg-input" placeholder="https://music.example.com 或 http://127.0.0.1:23332">
      </div>
      <div class="cfg-grid">
        <div class="cfg-field">
          <span class="cfg-label">用户名</span>
          <input v-model="config.username" class="cfg-input" placeholder="服务端登录用户名">
        </div>
        <div class="cfg-field">
          <span class="cfg-label">密码</span>
          <input v-model="config.password" class="cfg-input" type="password" placeholder="与用户名配对，留空则用 token">
        </div>
      </div>
      <div class="cfg-field">
        <span class="cfg-label">持久化 Token</span>
        <input v-model="config.token" class="cfg-input" placeholder="填了可免密码；用户名仍需填写（服务端靠用户名定位音源）">
      </div>
      <p class="cfg-tip">
        下载直链解析依赖「用户名 + token」定位自定义音源：只填 token 不填用户名会退回公开用户并报「未找到支持 xx 平台的自定义源」。
        只填账号密码时会自动登录换取 token。
      </p>

      <div class="cfg-section">下载</div>
      <div class="cfg-grid">
        <div class="cfg-field">
          <span class="cfg-label">音源平台</span>
          <select v-model="config.source" class="cfg-select">
            <option v-for="item in SOURCES" :key="item.value" :value="item.value">{{ item.label }}</option>
          </select>
        </div>
        <div class="cfg-field">
          <span class="cfg-label">下载音质</span>
          <select v-model="config.quality" class="cfg-select">
            <option v-for="item in QUALITIES" :key="item.value" :value="item.value">{{ item.label }}</option>
          </select>
        </div>
        <div class="cfg-field">
          <span class="cfg-label">搜索结果数</span>
          <input v-model.number="config.max_results" class="cfg-input" type="number" min="1" max="50">
        </div>
      </div>
      <p class="cfg-tip">音质不可用时按 flac24bit &gt; hires &gt; flac &gt; 320k &gt; 192k &gt; 128k 自动降级；真实容器以响应文件头为准。</p>

      <div class="cfg-field">
        <span class="cfg-label">下载位置</span>
        <input v-model="config.download_path" class="cfg-input" placeholder="/media/music，留空使用插件数据目录下的 music">
      </div>
      <div class="cfg-field">
        <span class="cfg-label">文件名模板</span>
        <input v-model="config.name_template" class="cfg-input" placeholder="支持 {name} {singer} {album} {source} {songmid}">
      </div>

      <div class="cfg-grid">
        <label class="cfg-switch">
          <input v-model="config.subdir_by_artist" type="checkbox">
          <span><b>按歌手分目录</b><em>在下载目录下按歌手建子目录</em></span>
        </label>
        <label class="cfg-switch">
          <input v-model="config.save_cover" type="checkbox">
          <span><b>保存封面</b><em>额外保存同名封面图</em></span>
        </label>
        <label class="cfg-switch">
          <input v-model="config.embed_tag" type="checkbox">
          <span><b>注入 ID3 标签</b><em>由服务端写入标题/艺术家/专辑/封面，不改变音频本体</em></span>
        </label>
        <label class="cfg-switch">
          <input v-model="config.embed_lyric" type="checkbox">
          <span><b>嵌入歌词</b><em>写入 USLT 帧，需同时开启 ID3 标签；失败自动降级</em></span>
        </label>
        <label class="cfg-switch">
          <input v-model="config.use_server_cache" type="checkbox">
          <span><b>下到服务端缓存</b><em>不落本地，改为提交服务端缓存任务</em></span>
        </label>
      </div>
    </div>

    <div class="cfg-foot">
      <button class="cfg-btn ghost" :disabled="testing" @click="testConnection">
        {{ testing ? '测试中…' : '测试连接' }}
      </button>
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
.cfg-input, .cfg-select {
  width: 100%; padding: 9px 12px; font-size: 13px; font-family: inherit; border-radius: 10px; outline: none;
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .16);
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  color: rgb(var(--v-theme-on-surface, 27, 29, 41));
}
.cfg-input:focus, .cfg-select:focus { border-color: rgb(var(--v-theme-primary, 124, 92, 252)); }
.cfg-tip {
  font-size: 11.5px; margin: -4px 0 12px; line-height: 1.5;
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
