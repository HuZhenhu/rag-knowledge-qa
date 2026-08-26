<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const submitRef = ref(false)

const mode = ref<'login' | 'register'>('login')

const form = ref({
  username: '',
  password: '',
  confirmPassword: '',
})

const loading = ref(false)
const errorMessage = ref('')

const formRef = ref()

const isLoginMode = computed(() => mode.value === 'login')

function switchMode() {
  mode.value = mode.value === 'login' ? 'register' : 'login'
  errorMessage.value = ''
}

function validateForm() {
  if (!form.value.username) {
    errorMessage.value = '请输入用户名'
    return false
  }
  if (!form.value.password) {
    errorMessage.value = '请输入密码'
    return false
  }
  if (mode.value === 'register' && form.value.password !== form.value.confirmPassword) {
    errorMessage.value = '两次输入的密码不一致'
    return false
  }
  return true
}

async function handleLogin() {
  if (!validateForm()) return
  loading.value = true
  errorMessage.value = ''
  try {
    if (mode.value === 'register') {
      await authStore.register(form.value.username, form.value.password)
    } else {
      await authStore.login(form.value.username, form.value.password)
    }
    const redirect = (route.query.redirect as string) || '/dashboard'
    router.push(redirect)
  } catch (error: any) {
    errorMessage.value = error?.message || '登录失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

function submitForm() {
  submitRef.value = true
  formRef.value?.validate?.((valid: boolean) => {
    if (valid) handleLogin()
  })
}

if (localStorage.getItem('token')) {
  router.replace('/')
}
</script>

<template>
  <div class="login-page">
    <!-- 左侧品牌区（知识工坊氛围） -->
    <div class="brand-panel bg-grid">
      <div class="brand-inner">
        <div class="brand-logo mono">R</div>
        <div class="brand-title">
          <span class="brand-name">Knowledge Works</span>
          <span class="brand-sub mono">rag · knowledge · qa</span>
        </div>
        <p class="brand-desc">
          企业级知识库问答平台<br />
          检索 · 索引 · 评测 · 运维
        </p>
        <ul class="brand-feature">
          <li class="mono"><span class="idx">01</span> 文档向量化检索与引用溯源</li>
          <li class="mono"><span class="idx">02</span> Agent 多路推理实时时间线</li>
          <li class="mono"><span class="idx">03</span> 权限分级 · 操作审计可溯</li>
        </ul>
        <div class="brand-footer mono">EST. 2026 — RAG-NG</div>
      </div>
    </div>

    <!-- 右侧表单区 -->
    <div class="form-panel">
      <div class="form-card">
        <div class="form-head">
          <div class="form-kicker mono">{{ mode === 'login' ? 'SIGN IN' : 'CREATE ACCOUNT' }}</div>
          <h1>{{ mode === 'login' ? '登录' : '注册' }}</h1>
          <p class="form-sub">
            {{ mode === 'login' ? '欢迎回来，进入你的知识库' : '创建账号，即刻开始检索与问答' }}
          </p>
        </div>

        <el-form ref="formRef" class="login-form" @submit.prevent="submitForm">
          <el-form-item>
            <el-input
              v-model="form.username"
              :placeholder="isLoginMode ? '用户名' : '设置用户名'"
              prefix-icon="User"
              size="large"
            />
          </el-form-item>
          <el-form-item>
            <el-input
              v-model="form.password"
              type="password"
              :placeholder="isLoginMode ? '密码' : '设置密码'"
              prefix-icon="Lock"
              size="large"
              show-password
            />
          </el-form-item>
          <el-form-item v-if="mode === 'register'">
            <el-input
              v-model="form.confirmPassword"
              type="password"
              placeholder="确认密码"
              prefix-icon="Lock"
              size="large"
              show-password
            />
          </el-form-item>

          <div v-if="errorMessage" class="error-msg">{{ errorMessage }}</div>

          <el-button
            type="primary"
            size="large"
            :loading="loading"
            class="submit-btn"
            native-type="submit"
          >
            {{ mode === 'login' ? '登 录' : '注 册' }}
          </el-button>

          <div class="login-switch">
            <span v-if="isLoginMode">
              没有账号？<a class="switch-link" @click.prevent="switchMode">立即注册</a>
            </span>
            <span v-else>
              已有账号？<a class="switch-link" @click.prevent="switchMode">去登录</a>
            </span>
          </div>
        </el-form>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  display: grid;
  grid-template-columns: 1.05fr 1fr;
  min-height: 100vh;
  background: var(--bg);
}

/* ---------- 品牌区 ---------- */
.brand-panel {
  position: relative;
  display: flex;
  align-items: center;
  padding: 9vh 8vw;
  background:
    radial-gradient(900px 500px at 18% 12%, var(--accent-soft), transparent 60%),
    radial-gradient(700px 480px at 90% 90%, var(--accent-2-soft), transparent 55%),
    var(--bg-surface);
  border-right: 1px solid var(--border);
  overflow: hidden;
}

.brand-inner {
  max-width: 520px;
}

.brand-logo {
  width: 58px;
  height: 58px;
  border-radius: var(--radius-lg);
  background: var(--accent);
  box-shadow: var(--shadow-md);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 28px;
  font-weight: 700;
  color: var(--bg-surface);
  margin-bottom: 30px;
  letter-spacing: 0.02em;
}

.brand-title {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 24px;
}

.brand-name {
  font-size: 34px;
  font-weight: 800;
  letter-spacing: 0.02em;
}

.brand-sub {
  font-size: 12.5px;
  color: var(--text-3);
  letter-spacing: 0.22em;
  text-transform: uppercase;
}

.brand-desc {
  font-size: 15px;
  line-height: 1.8;
  color: var(--text-2);
  margin-bottom: 40px;
}

.brand-feature {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin-bottom: 60px;
}

.brand-feature li {
  font-size: 13.5px;
  color: var(--text-2);
  display: flex;
  gap: 12px;
  align-items: baseline;
}

.brand-feature .idx {
  color: var(--accent);
  font-weight: 600;
}

.brand-footer {
  font-size: 11px;
  letter-spacing: 0.18em;
  color: var(--text-3);
  opacity: 0.8;
}

/* ---------- 表单区 ---------- */
.form-panel {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px;
}

.form-card {
  width: 100%;
  max-width: 400px;
}

.form-head {
  margin-bottom: 30px;
}

.form-kicker {
  font-size: 11px;
  letter-spacing: 0.28em;
  color: var(--accent);
  font-weight: 600;
  margin-bottom: 12px;
  text-transform: uppercase;
}

.form-head h1 {
  font-size: 28px;
  font-weight: 800;
  margin-bottom: 10px;
}

.form-sub {
  font-size: 13.5px;
  color: var(--text-3);
}

.login-form .el-form-item {
  margin-bottom: 18px;
}

.submit-btn {
  width: 100%;
  height: 44px;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.2em;
  margin-top: 6px;
  border-radius: var(--radius);
}

.error-msg {
  margin-bottom: 14px;
  padding: 10px 14px;
  background: var(--error-bg);
  color: var(--error);
  font-size: 13px;
  border-radius: var(--radius-sm);
}

.login-switch {
  margin-top: 20px;
  text-align: center;
  font-size: 13.5px;
  color: var(--text-3);
}

.switch-link {
  color: var(--accent);
  cursor: pointer;
  font-weight: 600;
  margin-left: 4px;
  text-decoration: underline;
  text-underline-offset: 3px;
}

.switch-link:hover {
  color: var(--accent-hover);
}

/* ---------- 响应式 ---------- */
@media (max-width: 900px) {
  .login-page {
    grid-template-columns: 1fr;
  }
  .brand-panel {
    display: none;
  }
  .form-panel {
    padding: 24px;
  }
}
</style>
