/**
 * 角色权限统一判断（A5：路由 / 菜单 / 按钮三处权限同源）
 *
 * 角色等级（与后端 src/api/jwt_auth.py::require_role 对齐）：
 *   viewer(0) < editor(1) = writer(1) < admin(2)
 */

export type UserRole = 'viewer' | 'editor' | 'writer' | 'admin'

export const ROLE_LEVEL: Record<string, number> = {
  viewer: 0,
  editor: 1,
  writer: 1,
  admin: 2,
}

/** 角色等级数值；未知角色按 viewer(0) 兜底 */
export function roleLevel(role?: string | null): number {
  if (!role) return ROLE_LEVEL.viewer
  return ROLE_LEVEL[role] ?? ROLE_LEVEL.viewer
}

/** 是否达到某一最小角色等级（如 hasMinRole('admin', 'editor') = true） */
export function hasMinRole(role: string | undefined | null, minRole: string): boolean {
  return roleLevel(role) >= roleLevel(minRole)
}

/** 是否至少可编辑（editor+）：上传 / 删除文档等操作 */
export function canEdit(role?: string | null): boolean {
  return roleLevel(role) >= ROLE_LEVEL.editor
}

/** 是否管理员 */
export function isAdmin(role?: string | null): boolean {
  return role === 'admin'
}

/**
 * 路由访问判断：meta.roles 未配置 = 仅需登录（返回 true）；否则要求角色在允许集合内
 */
export function canAccessRoute(role: string | undefined | null, allowedRoles?: string[]): boolean {
  if (!allowedRoles || allowedRoles.length === 0) return true
  return allowedRoles.includes(role ?? '')
}

/** 菜单项过滤：minRole 未声明 = 对所有登录用户可见 */
export function canShowByMinRole(
  role: string | undefined | null,
  minRole?: string
): boolean {
  if (!minRole) return true
  return roleLevel(role) >= roleLevel(minRole)
}
