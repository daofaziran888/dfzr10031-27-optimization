<template>
  <section class="panel" data-module="region-reconcile">
    <header class="panel-head">
      <div>
        <h3>地区归并与待映射</h3>
        <p class="page-desc">
          消费已接收的维度交换包重算地区归并与未映射单量。两种确认口径下，每笔订单都只产出一条结果、只入账一次。
        </p>
      </div>
      <div class="panel-actions">
        <button class="btn" type="button" @click="download('/api/data_mart/region/reconcile.csv?policy=' + policy)">
          下载逐单对照
        </button>
        <button class="btn" type="button" @click="download('/api/data_mart/region/conflicts.csv')">
          下载冲突与受影响订单
        </button>
      </div>
    </header>

    <div class="policy-row">
      <label v-for="option in POLICY_OPTIONS" :key="option.key" class="policy-card" :class="{ active: policy === option.key }">
        <input v-model="policy" type="radio" :value="option.key" @change="reconcile" />
        <strong>{{ option.label }}</strong>
        <small>{{ option.desc }}</small>
      </label>
      <button class="btn primary" type="button" @click="reconcile">按此口径重算</button>
    </div>

    <div v-if="message" class="notice" :class="messageLevel">{{ message }}</div>

    <div v-if="run" class="stat-row">
      <article class="stat-card"><span class="stat-label">订单总数</span><strong class="stat-value">{{ run.order_total }}</strong></article>
      <article class="stat-card"><span class="stat-label">已归并</span><strong class="stat-value">{{ run.mapped_count }}</strong></article>
      <article class="stat-card"><span class="stat-label">待映射</span><strong class="stat-value">{{ run.pending_count }}</strong></article>
      <article class="stat-card"><span class="stat-label">已归并金额</span><strong class="stat-value">{{ run.mapped_amount }}</strong></article>
      <article class="stat-card"><span class="stat-label">待映射金额</span><strong class="stat-value">{{ run.pending_amount }}</strong></article>
    </div>

    <template v-if="run">
      <p class="page-desc">
        口径「{{ run.policy_label }}」· 批次 {{ run.run_id }} ·
        <span :class="run.same_order_counted_once ? 'tag' : 'tag danger'">
          {{ run.same_order_counted_once ? '同笔订单只计一次' : '存在重复订单号' }}
        </span>
      </p>

      <h4 class="sub-title">按地区分组</h4>
      <table class="data-table">
        <thead>
          <tr><th>分组</th><th>代码</th><th>入账笔数</th><th>金额</th><th>订单</th></tr>
        </thead>
        <tbody>
          <tr v-for="group in run.groups" :key="group.group_code">
            <td>{{ group.group_name }}</td>
            <td>{{ group.group_code }}</td>
            <td>{{ group.order_count }}</td>
            <td>{{ group.amount }}</td>
            <td class="mono">{{ group.orders.join('、') }}</td>
          </tr>
          <tr v-if="!run.groups.length">
            <td colspan="5" class="empty-state">该口径下没有可归并订单</td>
          </tr>
        </tbody>
      </table>

      <h4 class="sub-title">待映射订单（{{ run.pending_count }}）</h4>
      <table class="data-table">
        <thead>
          <tr><th>订单号</th><th>来源</th><th>业务日</th><th>地区代码</th><th>金额</th><th>原因</th></tr>
        </thead>
        <tbody>
          <tr v-for="order in run.pending_orders" :key="order.order_no">
            <td>{{ order.order_no }}</td>
            <td>{{ order.source }}</td>
            <td>{{ order.business_date }}</td>
            <td>{{ order.region_code }}</td>
            <td>{{ order.amount }}</td>
            <td>{{ order.reason }}</td>
          </tr>
          <tr v-if="!run.pending_orders.length">
            <td colspan="6" class="empty-state">没有待映射订单</td>
          </tr>
        </tbody>
      </table>

      <h4 class="sub-title">逐单归并结果</h4>
      <table class="data-table">
        <thead>
          <tr>
            <th>订单号</th><th>来源</th><th>业务日</th><th>地区代码</th><th>归并结果</th>
            <th>地区分组</th><th>命中版本</th><th>命中方式</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="order in run.orders" :key="order.order_no">
            <td>{{ order.order_no }}</td>
            <td>{{ order.source }}</td>
            <td>{{ order.business_date }}</td>
            <td>{{ order.region_code }}</td>
            <td>
              <span :class="order.status === '已归并' ? 'tag' : 'tag warn'">{{ order.status }}</span>
            </td>
            <td>{{ order.group_name || '—' }}</td>
            <td>{{ order.version || '—' }}</td>
            <td>{{ order.matched_by || '—' }}</td>
          </tr>
        </tbody>
      </table>

      <h4 class="sub-title">生效期重叠</h4>
      <table class="data-table">
        <thead>
          <tr><th>版本</th><th>重叠期</th><th>此口径下的处理</th></tr>
        </thead>
        <tbody>
          <tr v-for="overlap in run.overlaps" :key="overlap.old_version + overlap.new_version">
            <td>{{ overlap.old_version }} → {{ overlap.new_version }}</td>
            <td>{{ overlap.overlap_from }} ~ {{ overlap.overlap_to }}</td>
            <td>{{ policy === 'by_source' ? '按订单来源各自落入其生效域' : '重叠期内订单暂缓归并，留在待映射' }}</td>
          </tr>
          <tr v-if="!run.overlaps.length">
            <td colspan="3" class="empty-state">当前交换包之间没有生效期重叠</td>
          </tr>
        </tbody>
      </table>
    </template>
    <p v-else class="empty-state">还没有重算结果，选择口径后点「按此口径重算」</p>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchJson, request } from '@/api/client'
import type { ReconcileRun } from '@/api/exchange'
import { download, POLICY_OPTIONS } from '@/api/exchange'

const policy = ref('hold_overlap')
const run = ref<ReconcileRun | null>(null)
const message = ref('')
const messageLevel = ref('ok')

function flash(text: string, level = 'ok') {
  message.value = text
  messageLevel.value = level
}

async function reconcile() {
  try {
    const response = await request('/api/data_mart/region/reconcile', {
      method: 'POST',
      body: JSON.stringify({ policy: policy.value }),
    })
    const result = await response.json()
    if (!result.ok) {
      flash(result.message, 'error')
      return
    }
    run.value = result.entry
    flash(result.message, 'ok')
  } catch (error) {
    flash(error instanceof Error ? error.message : '重算失败', 'error')
  }
}

onMounted(async () => {
  try {
    run.value = await fetchJson<ReconcileRun>(`/api/data_mart/region/reconcile?policy=${policy.value}`)
  } catch {
    run.value = null
  }
})
</script>
