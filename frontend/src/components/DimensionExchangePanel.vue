<template>
  <section class="panel" data-module="dimension-exchange">
    <header class="panel-head">
      <div>
        <h3>维度字典交换</h3>
        <p class="page-desc">
          导出可重建层级的交换包，或导入含维度字段、层级代码、父级代码、生效日期与别名的分卷包。
          同一交换包重复提交只登记一次成员变更。
        </p>
      </div>
      <div class="panel-actions">
        <button class="btn" type="button" @click="download('/api/dimension/exchange/dictionary/export?format=csv')">
          导出本地字典 CSV
        </button>
        <button class="btn" type="button" @click="openImport">导入交换包</button>
        <button class="btn ghost" type="button" @click="reloadPackages">刷新</button>
      </div>
    </header>

    <div v-if="message" class="notice" :class="messageLevel">{{ message }}</div>

    <div class="tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab"
        :class="{ active: activeTab === tab.key }"
        type="button"
        @click="activeTab = tab.key"
      >
        {{ tab.label }}
      </button>
    </div>

    <template v-if="activeTab === 'packages'">
      <table class="data-table">
        <thead>
          <tr>
            <th>交换包</th><th>版本</th><th>来源</th><th>生效期</th><th>成员</th>
            <th>有效层级边</th><th>冲突</th><th>重复提交</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in packages" :key="item.id">
            <td>{{ item.id }}</td>
            <td>{{ item.version }}</td>
            <td>{{ item.source }}</td>
            <td>{{ item.effective_from }} ~ {{ item.effective_to }}</td>
            <td>{{ item.member_count }}</td>
            <td>{{ item.valid_edge_count }}/{{ item.edge_count }}</td>
            <td>{{ item.conflict_count }}</td>
            <td>{{ item.duplicate_submissions }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="openDetail(item.id)">成员明细</button>
              <button class="link" type="button" @click="download(`/api/dimension/exchange/packages/${item.id}/export?format=csv`)">
                导出
              </button>
            </td>
          </tr>
          <tr v-if="!packages.length">
            <td colspan="9" class="empty-state">还没有交换包，先导入一个分卷包</td>
          </tr>
        </tbody>
      </table>
    </template>

    <template v-if="activeTab === 'comparison'">
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">交换包</span><strong class="stat-value">{{ comparison?.totals.packages ?? 0 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">地区代码</span><strong class="stat-value">{{ comparison?.totals.codes ?? 0 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">上级发生更正</span
          ><strong class="stat-value">{{ comparison?.totals.codes_with_parent_change ?? 0 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">生效期冲突</span
          ><strong class="stat-value">{{ comparison?.totals.effective_conflicts ?? 0 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">冲突别名</span
          ><strong class="stat-value">{{ comparison?.alias_report.conflict_count ?? 0 }}</strong>
        </article>
      </div>
      <div class="panel-actions" style="margin-bottom: 8px">
        <button class="btn" type="button" @click="download('/api/dimension/exchange/comparison.csv')">下载版本对照</button>
        <button class="btn" type="button" @click="download('/api/dimension/exchange/alias-conflicts.csv')">下载冲突别名</button>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>层级代码</th><th>名称</th><th>各版上级</th><th>各版地区分组</th>
            <th>生效期冲突</th><th>别名冲突</th><th>源文件:行号</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in comparison?.rows ?? []" :key="row.code">
            <td>{{ row.code }}</td>
            <td>{{ row.name }}</td>
            <td>
              <span v-for="version in row.versions" :key="version.package_id" class="tag-line">
                {{ version.version }}：{{ version.parent_code || '（根）' }}
                <span v-if="version.hierarchy_status !== '有效'" class="tag warn" :title="version.hierarchy_reason">层级无效</span>
              </span>
            </td>
            <td>
              <span v-for="version in row.versions" :key="version.package_id" class="tag-line">
                {{ version.version }}：{{ version.group_name }}
              </span>
            </td>
            <td>
              <span :class="row.effective_conflict ? 'tag danger' : 'tag'">{{ row.effective_conflict ? '有' : '无' }}</span>
            </td>
            <td>
              <span :class="row.alias_conflict ? 'tag danger' : 'tag'">{{ row.alias_conflict ? '有' : '无' }}</span>
            </td>
            <td class="mono">{{ row.source_refs.join(' / ') }}</td>
          </tr>
          <tr v-if="!(comparison?.rows ?? []).length">
            <td colspan="7" class="empty-state">暂无对照数据，先导入至少一个交换包</td>
          </tr>
        </tbody>
      </table>

      <h4 class="sub-title">生效期冲突</h4>
      <table class="data-table">
        <thead>
          <tr><th>版本</th><th>重叠期</th><th>存在差异的代码</th></tr>
        </thead>
        <tbody>
          <tr v-for="conflict in comparison?.conflicts ?? []" :key="conflict.old_package_id + conflict.new_package_id">
            <td>{{ conflict.old_version }} → {{ conflict.new_version }}</td>
            <td>{{ conflict.overlap_from }} ~ {{ conflict.overlap_to }}</td>
            <td>
              <span v-for="diff in conflict.diffs" :key="diff.code" class="tag-line">
                {{ diff.code }}：{{ diff.old_group_name || '未收录' }} → {{ diff.new_group_name || '未收录' }}
              </span>
            </td>
          </tr>
          <tr v-if="!(comparison?.conflicts ?? []).length">
            <td colspan="3" class="empty-state">没有生效期重叠的版本</td>
          </tr>
        </tbody>
      </table>

      <h4 class="sub-title">冲突别名</h4>
      <table class="data-table">
        <thead>
          <tr><th>别名</th><th>冲突类型</th><th>命中代码</th><th>源位置</th></tr>
        </thead>
        <tbody>
          <tr v-for="conflict in comparison?.alias_report.conflicts ?? []" :key="conflict.alias">
            <td>{{ conflict.alias }}</td>
            <td>{{ conflict.kind }}</td>
            <td>{{ conflict.codes.join('、') }}</td>
            <td class="mono">
              <span v-for="owner in conflict.owners" :key="owner.package_id + owner.code" class="tag-line">
                {{ owner.code }}@{{ owner.source_file }}:{{ owner.source_line }}
              </span>
            </td>
          </tr>
          <tr v-if="!(comparison?.alias_report.conflicts ?? []).length">
            <td colspan="4" class="empty-state">没有冲突别名</td>
          </tr>
        </tbody>
      </table>
    </template>

    <template v-if="activeTab === 'detail'">
      <div v-if="detail" class="detail-block">
        <p class="page-desc">
          交换包 {{ detail.id }}（{{ detail.version }} · {{ detail.source }}），
          成员 {{ detail.member_count }} 个，有效层级边 {{ detail.valid_edge_count }}/{{ detail.edge_count }}。
          <span v-if="detail.duplicate_submissions">重复提交 {{ detail.duplicate_submissions }} 次未新增成员。</span>
        </p>
        <table class="data-table">
          <thead>
            <tr>
              <th>层级代码</th><th>名称</th><th>父级</th><th>层级路径</th><th>别名</th>
              <th>层级状态</th><th>源文件:行号</th><th>追加别名</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="member in detail.members" :key="member.code">
              <td>{{ member.code }}</td>
              <td>{{ member.name }}</td>
              <td>{{ member.parent_code || '（根）' }}</td>
              <td>{{ member.path_names.join(' / ') }}</td>
              <td>{{ member.aliases.join('、') || '—' }}</td>
              <td>
                <span :class="member.hierarchy_status === '有效' ? 'tag' : 'tag danger'">
                  {{ member.hierarchy_status }}
                </span>
                <small v-if="member.hierarchy_reason">{{ member.hierarchy_reason }}</small>
                <span v-if="member.name_collision" class="tag warn" title="同名地区">同名</span>
              </td>
              <td class="mono">{{ member.source_file }}:{{ member.source_line }}</td>
              <td>
                <input v-model="aliasInputs[member.code]" class="inline-input" placeholder="别名" />
                <button class="link" type="button" @click="appendAlias(member.code)">追加</button>
              </td>
            </tr>
          </tbody>
        </table>

        <h4 class="sub-title">成员变更</h4>
        <table class="data-table">
          <thead>
            <tr><th>代码</th><th>变更类型</th><th>变更字段</th><th>上级</th><th>别名</th><th>源文件:行号</th></tr>
          </thead>
          <tbody>
            <tr v-for="change in detail.changes" :key="change.id">
              <td>{{ change.code }}</td>
              <td>{{ change.change_type }}</td>
              <td>{{ change.changed_fields.join('、') || '—' }}</td>
              <td>{{ change.old_parent_code || '—' }} → {{ change.new_parent_code || '—' }}</td>
              <td>{{ (change.old_aliases || []).join('、') || '—' }} → {{ (change.new_aliases || []).join('、') || '—' }}</td>
              <td class="mono">{{ change.source_file }}:{{ change.source_line }}</td>
            </tr>
            <tr v-if="!detail.changes.length">
              <td colspan="6" class="empty-state">与上一版相比没有成员变更</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="empty-state">请选择一个交换包查看成员明细</p>
    </template>

    <Teleport to="body">
      <div v-if="showImport" class="modal-overlay" @click.self="showImport = false">
        <div class="modal-card wide">
          <header class="modal-head">
            <h3>导入维度交换包</h3>
            <button class="btn ghost" type="button" @click="showImport = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitImport">
            <div class="form-grid">
              <label class="modal-field"><span>版本</span><input v-model="form.version" placeholder="如 2026.2" required /></label>
              <label class="modal-field"><span>来源</span><input v-model="form.source" placeholder="如 新来源" required /></label>
              <label class="modal-field"><span>维度字段</span><input v-model="form.field" placeholder="地区" /></label>
              <label class="modal-field"><span>生效期起</span><input v-model="form.effective_from" type="date" required /></label>
              <label class="modal-field"><span>生效期止</span><input v-model="form.effective_to" type="date" /></label>
            </div>
            <label class="modal-field">
              <span>分卷文件（CSV，可多选，也可粘贴 JSON 包）</span>
              <input type="file" accept=".csv,text/csv,.json" multiple @change="loadFiles" />
            </label>
            <label class="modal-field">
              <span>交换包内容（CSV 文本或 JSON，直接回传导出结果即可）</span>
              <textarea v-model="form.raw" rows="8" placeholder="维度字段,层级代码,父级代码,生效日期,别名,名称&#10;地区,RG-A,,2026-01-01,甲区,甲"></textarea>
            </label>
            <p v-if="importError" class="error-text">{{ importError }}</p>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="showImport = false">取消</button>
              <button class="btn primary" type="submit">提交导入</button>
            </footer>
          </form>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { fetchJson, request } from '@/api/client'
import type {
  AliasConflict,
  ComparisonRow,
  ExchangeDiagnostic,
  ExchangeMember,
  ExchangePackage,
  OverlapConflict,
} from '@/api/exchange'
import { download } from '@/api/exchange'

type PackageDetail = ExchangePackage & {
  members: ExchangeMember[]
  changes: {
    id: number
    code: string
    change_type: string
    changed_fields: string[]
    old_parent_code: string | null
    new_parent_code: string | null
    old_aliases: string[]
    new_aliases: string[]
    source_file: string
    source_line: number
  }[]
}

type Comparison = {
  packages: ExchangePackage[]
  conflicts: OverlapConflict[]
  alias_report: { conflict_count: number; conflicts: AliasConflict[] }
  rows: ComparisonRow[]
  totals: {
    packages: number
    codes: number
    codes_with_parent_change: number
    codes_with_alias_conflict: number
    effective_conflicts: number
  }
}

const tabs = [
  { key: 'packages', label: '交换包' },
  { key: 'comparison', label: '版本与别名对照' },
  { key: 'detail', label: '成员明细' },
]

const activeTab = ref('packages')
const packages = ref<ExchangePackage[]>([])
const comparison = ref<Comparison | null>(null)
const detail = ref<PackageDetail | null>(null)
const message = ref('')
const messageLevel = ref('')
const showImport = ref(false)
const importError = ref('')
const aliasInputs = ref<Record<string, string>>({})
const form = reactive({
  version: '',
  source: '',
  field: '地区',
  effective_from: '',
  effective_to: '',
  raw: '',
})
const volumes = ref<{ file: string; content: string }[]>([])

function flash(text: string, level = 'ok') {
  message.value = text
  messageLevel.value = level
}

async function reloadPackages() {
  try {
    const payload = await fetchJson<{ items: ExchangePackage[] }>('/api/dimension/exchange/packages')
    packages.value = payload.items
  } catch (error) {
    flash(error instanceof Error ? error.message : '交换包读取失败', 'error')
  }
}

async function reloadComparison() {
  try {
    comparison.value = await fetchJson<Comparison>('/api/dimension/exchange/comparison')
  } catch (error) {
    flash(error instanceof Error ? error.message : '对照读取失败', 'error')
  }
}

async function openDetail(packageId: string) {
  try {
    detail.value = await fetchJson<PackageDetail>(`/api/dimension/exchange/packages/${packageId}`)
    aliasInputs.value = {}
    activeTab.value = 'detail'
  } catch (error) {
    flash(error instanceof Error ? error.message : '交换包明细读取失败', 'error')
  }
}

function openImport() {
  importError.value = ''
  showImport.value = true
}

async function loadFiles(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  const loaded: { file: string; content: string }[] = []
  for (const file of files) {
    loaded.push({ file: file.name, content: await file.text() })
  }
  volumes.value = loaded
  if (loaded.length === 1) {
    form.raw = loaded[0].content
    volumes.value = []
  }
}

function parseRaw(): { volumes: { file?: string; content?: string; rows?: Record<string, string>[] }[] } | null {
  const raw = form.raw.trim()
  if (!raw) return null
  if (raw.startsWith('{')) {
    try {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed.volumes)) return parsed
    } catch {
      // 交给后端按 CSV 处理
    }
  }
  return { volumes: [{ file: `${form.version || 'package'}.csv`, content: form.raw }] }
}

async function submitImport() {
  importError.value = ''
  try {
    let payload: Record<string, unknown> = {
      version: form.version,
      source: form.source,
      field: form.field || '地区',
      effective_from: form.effective_from,
      effective_to: form.effective_to || null,
      volumes: volumes.value,
    }
    const parsed = parseRaw()
    if (parsed) {
      payload = {
        ...payload,
        ...parsed,
        version: form.version || (parsed as { version?: string }).version,
        source: form.source || (parsed as { source?: string }).source,
        effective_from: form.effective_from || (parsed as { effective_from?: string }).effective_from,
        effective_to: form.effective_to || (parsed as { effective_to?: string }).effective_to,
        field: form.field || (parsed as { field?: string }).field,
      }
    }
    const response = await request('/api/dimension/exchange/packages', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    const result = await response.json()
    const diags: ExchangeDiagnostic[] = result.entry?.diagnostics ?? []
    const problems = diags.filter((item) => item.level === 'error')
    if (!result.ok) {
      importError.value = `${result.message}${problems.length ? '：' + problems.map((d) => d.message).join('；') : ''}`
      return
    }
    showImport.value = false
    form.raw = ''
    volumes.value = []
    const prefix = problems.length ? `（有 ${problems.length} 行被拒绝，详见成员明细）` : ''
    flash(`${result.message}${prefix}`, result.entry?.duplicate ? 'warn' : 'ok')
    await Promise.all([reloadPackages(), reloadComparison()])
    if (result.entry?.package?.id) await openDetail(result.entry.package.id)
    activeTab.value = 'packages'
  } catch (error) {
    importError.value = error instanceof Error ? error.message : '导入失败'
  }
}

async function appendAlias(code: string) {
  if (!detail.value) return
  const alias = (aliasInputs.value[code] ?? '').trim()
  if (!alias) return
  try {
    const response = await request(`/api/dimension/exchange/packages/${detail.value.id}/aliases`, {
      method: 'POST',
      body: JSON.stringify({ code, alias }),
    })
    const result = await response.json()
    flash(result.message, result.ok ? 'ok' : 'error')
    if (result.ok) {
      aliasInputs.value[code] = ''
      detail.value = await fetchJson<PackageDetail>(`/api/dimension/exchange/packages/${detail.value.id}`)
      await reloadComparison()
    }
  } catch (error) {
    flash(error instanceof Error ? error.message : '别名追加失败', 'error')
  }
}

onMounted(async () => {
  await Promise.all([reloadPackages(), reloadComparison()])
})
</script>
