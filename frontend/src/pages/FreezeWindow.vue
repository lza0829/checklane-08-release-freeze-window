<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  deleteJSON,
  getJSON,
  localInputToUTC,
  postJSON,
  putJSON,
  utcToLocalInput,
} from '../api'

const status = ref(null)
const startInput = ref('')
const endInput = ref('')
const note = ref('')
const err = ref('')
const info = ref('')
const busy = ref(false)
const signingId = ref(null)

const window = computed(() => status.value?.window ?? null)
const frozen = computed(() => status.value?.frozen === true)
const heldBatches = computed(() => status.value?.held_batches ?? [])

async function load() {
  status.value = await getJSON('/api/freeze-window')
}

function preset(hours) {
  const start = new Date(Date.now() - 60 * 1000)
  const end = new Date(Date.now() + hours * 3600 * 1000)
  const toInput = (d) => {
    const pad = (n) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
  }
  startInput.value = toInput(start)
  endInput.value = toInput(end)
  note.value = `演示冻结窗（${hours} 小时）`
}

async function save() {
  busy.value = true
  err.value = ''
  info.value = ''
  try {
    if (!startInput.value || !endInput.value) throw new Error('请填写冻结窗起止时间')
    status.value = await putJSON('/api/freeze-window', {
      start_at: localInputToUTC(startInput.value),
      end_at: localInputToUTC(endInput.value),
      note: note.value || null,
    })
    info.value = '冻结窗配置已保存并落库'
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

async function clearWindow() {
  busy.value = true
  err.value = ''
  info.value = ''
  try {
    status.value = await deleteJSON('/api/freeze-window')
    startInput.value = ''
    endInput.value = ''
    note.value = ''
    info.value = '冻结窗已解除，可对挂起批次补签'
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

async function signBatch(id) {
  signingId.value = id
  err.value = ''
  info.value = ''
  try {
    const detail = await postJSON(`/api/batches/${id}/sign`, {})
    info.value = `批次 #${id} 补签成功：签发 ${detail.released_count} 行到精选区（未重跑规则）`
    await load()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    signingId.value = null
  }
}

onMounted(async () => {
  try {
    await load()
    if (window.value) {
      startInput.value = utcToLocalInput(window.value.start_at)
      endInput.value = utcToLocalInput(window.value.end_at)
      note.value = window.value.note ?? ''
    }
  } catch (e) {
    err.value = String(e.message || e)
  }
})
</script>

<template>
  <div class="page">
    <header>
      <h1>放行冻结窗</h1>
      <p class="muted">
        窗内入检照常跑规则与隔离，但通过行不得写入精选区，批次挂起为「检过未签发」；
        窗内一切签发入口拒绝，解冻后才能补签。边界：<code>start_at &lt;= now &lt; end_at</code>（含起点，不含终点）
      </p>
    </header>

    <p v-if="err" class="tag danger">{{ err }}</p>
    <p v-if="info" class="tag ok">{{ info }}</p>

    <div class="panel">
      <h3>
        当前状态
        <span class="tag" :class="frozen ? 'danger' : 'ok'">
          {{ frozen ? '冻结中：禁止任何签发' : '未冻结：可正常签发' }}
        </span>
      </h3>
      <p v-if="window" class="muted">
        窗口（UTC）：{{ window.start_at }} ~ {{ window.end_at }}
        <template v-if="window.note">｜{{ window.note }}</template>
      </p>
      <p v-else class="muted">尚未配置冻结窗</p>
      <p class="muted">口径：{{ status?.boundary_rule }}</p>
    </div>

    <div class="panel">
      <h3>配置冻结窗</h3>
      <p class="muted">时间按本机时区填写，提交时转 UTC 存储；刷新后配置仍在。</p>
      <div class="row">
        <label>起 <input type="datetime-local" v-model="startInput" /></label>
        <label>止 <input type="datetime-local" v-model="endInput" /></label>
        <label>备注 <input type="text" v-model="note" placeholder="可选" /></label>
      </div>
      <div class="row" style="margin-top:10px">
        <button :disabled="busy" @click="save">保存冻结窗</button>
        <button :disabled="busy || !window" class="btn-secondary" @click="clearWindow">解除冻结窗</button>
        <span class="muted">快捷：</span>
        <button class="btn-secondary" :disabled="busy" @click="preset(1)">未来 1 小时</button>
        <button class="btn-secondary" :disabled="busy" @click="preset(24)">未来 24 小时</button>
      </div>
    </div>

    <div class="panel">
      <h3>挂起批次（检过未签发 / frozen_held）<span class="tag">{{ heldBatches.length }}</span></h3>
      <p v-if="!heldBatches.length" class="muted">当前没有挂起批次。</p>
      <table v-else>
        <thead>
          <tr>
            <th>批次</th><th>数据集</th><th>已检通过</th><th>因冻结未签发</th>
            <th>隔离</th><th>最终签发</th><th>挂起时间</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="b in heldBatches" :key="b.id">
            <td>#{{ b.id }}</td>
            <td>{{ b.dataset_id }}</td>
            <td>{{ b.passed_count }}</td>
            <td><span class="tag danger">{{ b.held_count }}</span></td>
            <td>{{ b.quarantined_count }}</td>
            <td>{{ b.released_count }}</td>
            <td>{{ b.held_at }}</td>
            <td>
              <button
                :disabled="frozen || signingId === b.id"
                @click="signBatch(b.id)"
              >
                {{ frozen ? '窗内禁止补签' : (signingId === b.id ? '补签中…' : '解冻后补签') }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="frozen" class="muted">
        冻结窗内补签按钮禁用；即使绕过页面直接调用 <code>POST /api/batches/{id}/sign</code> 也会被 409 拒绝。
      </p>
    </div>
  </div>
</template>
