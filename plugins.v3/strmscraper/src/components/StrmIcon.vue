<script setup>
/** 纯 SVG 图标，避免依赖宿主的图标字体与组件注册。 */
defineProps({
  name: { type: String, default: 'grid' },
  size: { type: [Number, String], default: 20 },
})
const paths = {
  library: 'M4 4h6v7H4z M14 4h6v7h-6z M4 15h6v5H4z M14 15h6v5h-6z',
  grid: 'M4 4h6v6H4z M14 4h6v6h-6z M4 14h6v6H4z M14 14h6v6h-6z',
  list: 'M8 6h12 M8 12h12 M8 18h12 M4 6h.01 M4 12h.01 M4 18h.01',
  film: 'M4 4h16v16H4z M4 8h16 M4 16h16 M8 4v4 M16 4v4 M8 16v4 M16 16v4',
  tv: 'M4 7h16v13H4z M8 3l4 4 4-4',
  music:
    'M9 18V5l11-2v13 M9 7l11-2 M9 18a3 3 0 1 1-3-3c1.7 0 3 1.3 3 3 M20 16a3 3 0 1 1-3-3c1.7 0 3 1.3 3 3',
  folder: 'M3 7V5h6l2 2h10v13H3z',
  check: 'M5 12l4 4L19 6',
  checkCircle: 'M21 11v1a9 9 0 1 1-5.3-8.2 M21 4L11 14l-3-3',
  clock: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0 M12 7v5l3 2',
  alert:
    'M12 8v5 M12 17h.01 M10.3 3.9L2.2 18a2 2 0 0 0 1.7 3h16.2a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0',
  search: 'M20 20l-5-5 M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0',
  refresh: 'M20 7v5h-5 M4 17v-5h5 M6.1 6a8 8 0 0 1 13 2l.9 4 M4 12l.9 4a8 8 0 0 0 13 2',
  sparkles: 'M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z M20 2v4 M18 4h4',
  chevron: 'M8 10l4 4 4-4',
  arrow: 'M5 12h14 M13 6l6 6-6 6',
  back: 'M19 12H5 M11 6l-6 6 6 6',
  settings:
    'M9 4l1-2h4l1 2 3 2 2 1v4l-2 1-1 3v2l-3 2h-4l-1-2-3-2-2-1v-4l2-1 1-3V4z M15 10a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
  close: 'M6 6l12 12 M6 18L18 6',
  history: 'M3 11a9 9 0 1 1 2.6 7.4 M3 4v7h7 M12 7v5l3 2',
  queue: 'M8 5h12 M8 12h12 M8 19h8 M3 5h.01 M3 12h.01 M3 19h.01',
  activity: 'M2 12h4l3-8 6 16 3-8h4',
  sort: 'M8 4v16 M4 16l4 4 4-4 M15 5h6 M15 10h4 M15 15h2',
  moon: 'M20.8 13.2A9 9 0 0 1 10.8 3.2a9 9 0 1 0 10 10',
  sun: 'M12 8a4 4 0 1 1 0 8 4 4 0 0 1 0-8 M12 2v2 M12 20v2 M2 12h2 M20 12h2 M5 5l1.5 1.5 M17.5 17.5L19 19 M5 19l1.5-1.5 M17.5 6.5L19 5',
  monitor: 'M3 4h18v13H3z M8 21h8 M12 17v4',
}
</script>
<template>
  <svg
    class="strm-icon"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.65"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="paths[name] || paths.grid" />
  </svg>
</template>
<style scoped>
.strm-icon {
  display: inline-block;
  flex: 0 0 auto;
  vertical-align: middle;
}
</style>
