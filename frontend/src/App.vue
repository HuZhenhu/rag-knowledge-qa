<script setup lang="ts">
</script>

<template>
  <router-view />
</template>

<style>
/* 全局样式 */
:root {
  --radius: 1rem;
  --radius-sm: 0.5rem;
  --radius-full: 9999px;
}

[data-theme="dark"] {
  --bg: #050505;
  --bg-surface: #0A0A0A;
  --bg-card: rgba(255,255,255,0.03);
  --bg-hover: rgba(255,255,255,0.06);
  --border: rgba(255,255,255,0.08);
  --text-1: #fff;
  --text-2: rgba(255,255,255,0.55);
  --text-3: rgba(255,255,255,0.3);
  --accent: #8B5CF6;
  --accent-soft: rgba(139,92,246,0.08);
  --success: #10B981;
  --success-bg: rgba(16,185,129,0.15);
  --error: #EF4444;
  --error-bg: rgba(239,68,68,0.15);
  --warning: #F59E0B;
  --warning-bg: rgba(245,158,11,0.15);
}

[data-theme="light"] {
  --bg: #F8F9FC;
  --bg-surface: #FFFFFF;
  --bg-card: rgba(0,0,0,0.02);
  --bg-hover: rgba(0,0,0,0.04);
  --border: rgba(0,0,0,0.08);
  --text-1: #0F172A;
  --text-2: #64748B;
  --text-3: #94A3B8;
  --accent: #7C3AED;
  --accent-soft: rgba(124,58,237,0.06);
  --success: #059669;
  --success-bg: rgba(5,150,105,0.1);
  --error: #DC2626;
  --error-bg: rgba(220,38,38,0.1);
  --warning: #D97706;
  --warning-bg: rgba(217,119,6,0.1);
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: var(--bg);
  color: var(--text-1);
  transition: background 0.3s, color 0.3s;
  -webkit-font-smoothing: antialiased;
}
</style>
