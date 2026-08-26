<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  loading?: boolean
}>()

const emit = defineEmits<{
  (e: 'send', message: string): void
}>()

const inputText = ref('')

function handleSend(): void {
  if (inputText.value.trim() && !props.loading) {
    emit('send', inputText.value.trim())
    inputText.value = ''
  }
}

function handleKeyup(e: KeyboardEvent): void {
  if (e.key === 'Enter' && !e.shiftKey) {
    handleSend()
  }
}
</script>

<template>
  <div class="chat-input">
    <input
      v-model="inputText"
      type="text"
      placeholder="输入问题..."
      :disabled="loading"
      @keyup="handleKeyup"
    />
    <button :disabled="loading || !inputText.trim()" @click="handleSend">
      {{ loading ? '发送中...' : '发送' }}
    </button>
  </div>
</template>

<style scoped>
.chat-input {
  display: flex;
  gap: 10px;
  align-items: center;
  margin: 0 20px 20px;
  padding: 8px 10px 8px 18px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-md);
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.chat-input:focus-within {
  border-color: var(--accent);
  box-shadow: var(--shadow-md), 0 0 0 3px var(--accent-soft);
}

input {
  flex: 1;
  min-width: 0;
  padding: 10px 0;
  background: transparent;
  border: none;
  outline: none;
  font-size: 15px;
  color: var(--text-1);
  font-family: var(--font-sans);
}

input::placeholder {
  color: var(--text-3);
  font-style: italic;
}

input:disabled {
  cursor: not-allowed;
}

button {
  padding: 9px 20px;
  background: var(--accent);
  color: var(--bg-surface);
  border: none;
  border-radius: var(--radius-full);
  cursor: pointer;
  font-size: 14px;
  font-weight: 600;
  font-family: var(--font-sans);
  transition: background 0.2s ease, transform 0.15s ease;
}

button:hover:not(:disabled) {
  background: var(--accent-hover);
  transform: translateY(-1px);
}

button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
</style>
