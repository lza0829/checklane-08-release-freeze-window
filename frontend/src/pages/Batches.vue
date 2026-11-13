<script setup>
import { onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'
const rows = ref([])
const detail = ref(null)
const err = ref('')
const info = ref('')
const signingId = ref(null)
async function load() {
  rows.value = await getJSON('/api/batches')
}
async function show(id) {
  detail.value = await getJSON(`/api/batches/${id}`)
}
async function signBatch(id) {
  signingId.value = id
  err.value = ''
  info.value = ''
  try {
    const d = await postJSON(`/api/batches/${id}/sign`, {})
    info.value = `批次 #${id} 补签成功：最终签发 ${d.released_count} 行`
    await load()
    detail.value = await getJSON(`/api/batches/${id}`)
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    signingId.value = null
  }
}
function statusClass(s) {
  if (s === 'released') return 'ok'
  if (s === 'frozen_held') return 'danger'
  return ''
}
function statusLabel(s) {
  return {
    released: '已签发',
    frozen_held: '检过未签发·冻结挂起',
    inspected: '已检待签',
  }[s] || s
}
onMounted(async () => {
  try { await load() }
  catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <header>
      <h1>批次</h1>
      <p class="muted">历史入检批次与报告；冻结挂起批次解冻后可在此或「放行冻结窗」页补签</p>
    </header>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <p v-if="info" class="tag ok">{{ info }}</p>
    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>ID</th><th>数据集</th><th>状态</th><th>扫描</th><th>已检通过</th>
            <th>隔离</th><th>冻结未签</th><th>最终签发</th><th>时间</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id" style="cursor:pointer" @click="show(r.id)">
            <td>{{ r.id }}</td>
            <td>{{ r.dataset_id }}</td>
            <td><span class="tag" :class="statusClass(r.status)">{{ statusLabel(r.status) }}</span></td>
            <td>{{ r.scanned_count }}</td>
            <td>{{ r.passed_count }}</td>
            <td>{{ r.quarantined_count }}</td>
            <td>{{ r.held_count }}</td>
            <td>{{ r.released_count }}</td>
            <td>{{ r.created_at }}</td>
            <td @click.stop>
              <button
                v-if="r.status === 'frozen_held'"
                :disabled="signingId === r.id"
                @click="signBatch(r.id)"
              >{{ signingId === r.id ? '补签中…' : '补签' }}</button>
              <span v-else class="muted">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="panel" v-if="detail">
      <h3>批次 #{{ detail.id }} 明细
        <span class="tag" :class="statusClass(detail.status)">{{ statusLabel(detail.status) }}</span>
      </h3>
      <pre class="muted" style="white-space:pre-wrap">{{ JSON.stringify(detail.report, null, 2) }}</pre>
    </div>
  </div>
</template>
