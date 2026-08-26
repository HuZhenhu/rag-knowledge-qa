import { describe, it, expect } from 'vitest'
import {
  ROLE_LEVEL,
  roleLevel,
  hasMinRole,
  canEdit,
  isAdmin,
  canAccessRoute,
  canShowByMinRole,
} from '../utils/permission'
import { MENU_ITEMS, filterMenusByRole } from '../utils/menus'

describe('permission: ROLE_LEVEL', () => {
  it('角色等级 viewer < editor(writer) < admin', () => {
    expect(ROLE_LEVEL.viewer).toBe(0)
    expect(ROLE_LEVEL.editor).toBe(1)
    expect(ROLE_LEVEL.writer).toBe(1)
    expect(ROLE_LEVEL.admin).toBe(2)
  })

  it('未知角色/空角色按 viewer(0) 兜底', () => {
    expect(roleLevel(undefined)).toBe(0)
    expect(roleLevel(null)).toBe(0)
    expect(roleLevel('')).toBe(0)
    expect(roleLevel('superuser')).toBe(0)
  })
})

describe('permission: hasMinRole', () => {
  it('viewer 不满足 editor/admin，满足 viewer', () => {
    expect(hasMinRole('viewer', 'viewer')).toBe(true)
    expect(hasMinRole('viewer', 'editor')).toBe(false)
    expect(hasMinRole('viewer', 'admin')).toBe(false)
  })

  it('editor 满足 viewer/editor，不满足 admin', () => {
    expect(hasMinRole('editor', 'viewer')).toBe(true)
    expect(hasMinRole('editor', 'editor')).toBe(true)
    expect(hasMinRole('editor', 'admin')).toBe(false)
  })

  it('admin 满足所有等级', () => {
    expect(hasMinRole('admin', 'viewer')).toBe(true)
    expect(hasMinRole('admin', 'editor')).toBe(true)
    expect(hasMinRole('admin', 'admin')).toBe(true)
  })
})

describe('permission: canEdit / isAdmin', () => {
  it('canEdit = editor+（上传/删除文档）', () => {
    expect(canEdit('viewer')).toBe(false)
    expect(canEdit(undefined)).toBe(false)
    expect(canEdit('editor')).toBe(true)
    expect(canEdit('writer')).toBe(true)
    expect(canEdit('admin')).toBe(true)
  })

  it('isAdmin 仅 admin', () => {
    expect(isAdmin('admin')).toBe(true)
    expect(isAdmin('editor')).toBe(false)
    expect(isAdmin('viewer')).toBe(false)
  })
})

describe('permission: canAccessRoute（路由级）', () => {
  it('meta.roles 未配置 = 仅需登录，任何角色放行', () => {
    expect(canAccessRoute('viewer', undefined)).toBe(true)
    expect(canAccessRoute('admin', [])).toBe(true)
  })

  it('按允许角色集合判断', () => {
    expect(canAccessRoute('admin', ['admin'])).toBe(true)
    expect(canAccessRoute('editor', ['admin'])).toBe(false)
    expect(canAccessRoute(null, ['admin'])).toBe(false)
  })

  it('/dashboard、/chat 不设 roles，全角色可访问；/users 仅 admin', () => {
    expect(canAccessRoute('viewer', undefined)).toBe(true)
    expect(canAccessRoute('viewer', ['viewer', 'editor', 'admin'])).toBe(true)
    expect(canAccessRoute('viewer', ['admin'])).toBe(false)
    expect(canAccessRoute('admin', ['admin'])).toBe(true)
  })
})

describe('permission: canShowByMinRole（菜单/按钮级）', () => {
  it('minRole 未声明 = 对所有登录用户可见', () => {
    expect(canShowByMinRole('viewer', undefined)).toBe(true)
  })

  it('按最小角色等级过滤', () => {
    expect(canShowByMinRole('viewer', 'viewer')).toBe(true)
    expect(canShowByMinRole('viewer', 'editor')).toBe(false)
    expect(canShowByMinRole('editor', 'editor')).toBe(true)
    expect(canShowByMinRole('editor', 'admin')).toBe(false)
    expect(canShowByMinRole('admin', 'admin')).toBe(true)
  })
})

describe('permission: 菜单过滤（A2 验收）', () => {
  const paths = (roles: string | null) => filterMenusByRole(roles).map((m) => m.path)

  it('viewer 仅见 仪表盘/文档管理/聊天', () => {
    expect(paths('viewer')).toEqual(['/dashboard', '/documents', '/chat'])
  })

  it('editor 可见 索引监控（只读），不见 日志/评测/用户管理', () => {
    expect(paths('editor')).toEqual(['/dashboard', '/documents', '/index', '/chat'])
  })

  it('admin 全部 7 项可见', () => {
    expect(paths('admin')).toEqual([
      '/dashboard',
      '/documents',
      '/index',
      '/logs',
      '/evaluations',
      '/users',
      '/chat',
    ])
  })

  it('未登录（null）按 viewer 对待', () => {
    expect(paths(null)).toEqual(['/dashboard', '/documents', '/chat'])
  })

  it('MENU_ITEMS 与任务书路径/图标一致（索引监控 = Connection）', () => {
    const indexItem = MENU_ITEMS.find((m) => m.path === '/index')
    expect(indexItem?.icon).toBe('Connection')
    expect(indexItem?.minRole).toBe('editor')
    expect(MENU_ITEMS.find((m) => m.path === '/logs')?.minRole).toBe('admin')
    expect(MENU_ITEMS.find((m) => m.path === '/evaluations')?.minRole).toBe('admin')
    expect(MENU_ITEMS.find((m) => m.path === '/users')?.minRole).toBe('admin')
    expect(MENU_ITEMS.find((m) => m.path === '/documents')?.minRole).toBe('viewer')
  })
})
