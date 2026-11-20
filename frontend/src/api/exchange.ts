/** 维度字典交换的前端共享类型与小工具，维度管理与数据集市两个页面都用。 */
export type ExchangePackage = {
  id: string
  version: string
  source: string
  field: string
  effective_from: string
  effective_to: string
  member_count: number
  edge_count: number
  valid_edge_count: number
  invalid_edge_count: number
  conflict_count: number
  duplicate_submissions: number
  seeded: boolean
}

export type ExchangeMember = {
  code: string
  name: string
  parent_code: string
  aliases: string[]
  effective_from: string
  effective_to: string
  source_file: string
  source_line: number
  hierarchy_status: string
  hierarchy_reason: string
  group_name: string
  path_names: string[]
  name_collision: boolean
  name_collision_codes?: string[]
}

export type ExchangeDiagnostic = {
  file: string | null
  line: number | null
  level: string
  code: string
  message: string
}

export type ComparisonVersion = {
  package_id: string
  version: string
  source: string
  name: string
  parent_code: string
  aliases: string[]
  effective_from: string
  effective_to: string
  hierarchy_status: string
  hierarchy_reason: string
  group_name: string
  path_names: string[]
  source_file: string
  source_line: number
  effective_conflict: boolean
  alias_conflict: boolean
}

export type ComparisonRow = {
  code: string
  name: string
  versions: ComparisonVersion[]
  parent_changed: boolean
  name_changed: boolean
  alias_conflict: boolean
  effective_conflict: boolean
  source_refs: string[]
}

export type ConflictDiff = {
  code: string
  old_parent_code: string | null
  new_parent_code: string | null
  old_group_name: string | null
  new_group_name: string | null
  old_name?: string
  new_name?: string
  kind: string
}

export type OverlapConflict = {
  old_package_id: string
  new_package_id: string
  old_version: string
  new_version: string
  overlap_from: string
  overlap_to: string
  diffs: ConflictDiff[]
}

export type AliasConflict = {
  alias: string
  codes: string[]
  kind: string
  owners: { package_id: string; code: string; name: string; source_file: string; source_line: number }[]
}

export type ReconcileOrder = {
  order_no: string
  source: string
  business_date: string
  region_code: string
  amount: number
  status: string
  reason: string
  version: string | null
  matched_by: string | null
  group_code: string | null
  group_name: string | null
}

export type ReconcileRun = {
  run_id: string
  policy: string
  policy_label: string
  order_total: number
  mapped_count: number
  pending_count: number
  mapped_amount: number
  pending_amount: number
  same_order_counted_once: boolean
  groups: { group_code: string; group_name: string; order_count: number; amount: number; orders: string[] }[]
  pending_orders: ReconcileOrder[]
  orders: ReconcileOrder[]
  overlaps: { old_version: string; new_version: string; overlap_from: string; overlap_to: string }[]
}

export const POLICY_OPTIONS = [
  { key: 'hold_overlap', label: '暂缓重叠期归并', desc: '重叠期内的订单留在待映射，等版本能唯一确认再重算' },
  { key: 'by_source', label: '按原来源生效域', desc: '按订单来源选择覆盖业务日的那个版本，重叠期也照来源各归各的' },
]

export function download(path: string): void {
  window.open(path, '_blank')
}