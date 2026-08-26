/**
 * 侧边栏菜单定义（A2：菜单权限与路由 meta.roles 同源）
 *
 * minRole 按任务书 A 部分表格：
 *   /dashboard、/chat         所有登录用户（viewer+）
 *   /documents                viewer+
 *   /index（索引监控）          editor+
 *   /logs、/evaluations、/users admin
 */
import { hasMinRole } from './permission'

export interface MenuItem {
  /** 路由路径（el-menu index） */
  path: string
  /** Element Plus 图标组件名（全局注册） */
  icon: string
  /** i18n 菜单文案 key */
  labelKey: string
  /** 最小角色（permission.ts ROLE_LEVEL） */
  minRole: string
}

export const MENU_ITEMS: MenuItem[] = [
  { path: '/dashboard', icon: 'Grid', labelKey: 'menu.dashboard', minRole: 'viewer' },
  { path: '/documents', icon: 'Document', labelKey: 'menu.documents', minRole: 'viewer' },
  { path: '/index', icon: 'Connection', labelKey: 'menu.indexMonitor', minRole: 'editor' },
  { path: '/logs', icon: 'TrendCharts', labelKey: 'menu.queryLogs', minRole: 'admin' },
  { path: '/evaluations', icon: 'DataAnalysis', labelKey: 'menu.evaluations', minRole: 'admin' },
  { path: '/users', icon: 'User', labelKey: 'menu.users', minRole: 'admin' },
  { path: '/chat', icon: 'ChatDotRound', labelKey: 'menu.chat', minRole: 'viewer' },
]

/** 按角色过滤菜单项（viewer/editor/admin 均可测试） */
export function filterMenusByRole(
  role: string | undefined | null,
  items: MenuItem[] = MENU_ITEMS
): MenuItem[] {
  return items.filter((item) => hasMinRole(role, item.minRole))
}
