# RAG 系统权限管理设计方案

**版本**: v1.0
**日期**: 2026-06-14
**状态**: 设计阶段

---

## 1. 背景与目标

### 1.1 背景

当前系统为个人使用场景，权限模型简单（admin/viewer），存在以下问题：

- 无部门/租户隔离，所有用户共享同一知识库
- 无文档级权限控制
- 无敏感信息脱敏
- 不满足企业级安全要求

### 1.2 设计目标

1. **权限由后端控制**：所有权限判断、数据过滤在后端完成，不依赖大模型
2. **多级权限体系**：支持系统级 → 部门级 → 知识库级 → 文档级 → 片段级
3. **数据隔离**：不同部门/租户的数据完全隔离
4. **结果脱敏**：敏感信息在返回给大模型前进行脱敏处理
5. **完整审计**：记录权限判断、检索条件、脱敏处理等全流程

---

## 2. 权限模型设计

### 2.1 角色体系

```
super_admin (超管)
    └── tenant_admin (租户管理员)
        └── department_admin (部门管理员)
            ├── editor (编辑者)
            └── viewer (查看者)
```

#### 角色权限矩阵

| 操作 | super_admin | tenant_admin | department_admin | editor | viewer |
|------|-------------|--------------|------------------|--------|--------|
| 管理租户 | ✅ | ❌ | ❌ | ❌ | ❌ |
| 管理用户 | ✅ | ✅(本租户) | ✅(本部门) | ❌ | ❌ |
| 创建知识库 | ✅ | ✅ | ✅ | ❌ | ❌ |
| 删除知识库 | ✅ | ✅(自己创建) | ✅(自己创建) | ❌ | ❌ |
| 上传文档 | ✅ | ✅ | ✅ | ✅ | ❌ |
| 删除文档 | ✅ | ✅ | ✅ | ✅(自己上传) | ❌ |
| 编辑文档权限 | ✅ | ✅ | ✅ | ❌ | ❌ |
| 查询问答 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 查看日志 | ✅ | ✅(本租户) | ✅(本部门) | ❌ | ❌ |
| 管理 API Key | ✅ | ✅ | ❌ | ❌ | ❌ |
| 查看审计日志 | ✅ | ✅(本租户) | ❌ | ❌ | ❌ |

### 2.2 数据模型

#### 租户表 tenants

```sql
CREATE TABLE tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    plan TEXT DEFAULT 'free',          -- free/basic/enterprise
    max_users INTEGER DEFAULT 10,
    max_knowledge_bases INTEGER DEFAULT 5,
    max_documents INTEGER DEFAULT 100,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);
```

#### 部门表 departments

```sql
CREATE TABLE departments (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    parent_id TEXT,                     -- 支持多级部门
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    FOREIGN KEY (parent_id) REFERENCES departments(id)
);
```

#### 用户表 users（扩展）

```sql
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer',  -- super_admin/tenant_admin/department_admin/editor/viewer
    tenant_id TEXT,
    department_id TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login_at TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    FOREIGN KEY (department_id) REFERENCES departments(id)
);
```

#### 知识库表 knowledge_bases（扩展）

```sql
CREATE TABLE knowledge_bases (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    owner_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    department_id TEXT,                 -- NULL 表示租户内全员可见
    visibility TEXT DEFAULT 'department', -- public/tenant/department/private
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (owner_id) REFERENCES users(id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    FOREIGN KEY (department_id) REFERENCES departments(id)
);
```

#### 文档表 documents（扩展）

```sql
CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    kb_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    file_type TEXT,
    file_size INTEGER,
    chunk_count INTEGER DEFAULT 0,
    owner_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    department_id TEXT,
    visibility TEXT DEFAULT 'department', -- public/tenant/department/private
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (kb_id) REFERENCES knowledge_bases(id),
    FOREIGN KEY (owner_id) REFERENCES users(id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    FOREIGN KEY (department_id) REFERENCES departments(id)
);
```

#### 文档权限表 document_permissions

```sql
CREATE TABLE document_permissions (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    user_id TEXT,                        -- 指定用户
    department_id TEXT,                  -- 指定部门
    permission TEXT NOT NULL,            -- read/write/admin
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (department_id) REFERENCES departments(id),
    UNIQUE(document_id, user_id),
    UNIQUE(document_id, department_id)
);
```

#### Chunk 权限标签表 chunk_permissions（可选，高级功能）

```sql
CREATE TABLE chunk_permissions (
    id TEXT PRIMARY KEY,
    chunk_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    department_id TEXT,
    required_role TEXT,                  -- 需要的角色
    sensitivity_level INTEGER DEFAULT 0, -- 敏感等级 0-5
    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    FOREIGN KEY (department_id) REFERENCES departments(id)
);
```

#### 审计日志表 audit_logs（扩展）

```sql
CREATE TABLE audit_logs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    action TEXT NOT NULL,                -- login/query/upload/download/admin
    resource_type TEXT,                  -- document/knowledge_base/user
    resource_id TEXT,
    details TEXT,                        -- JSON 详细信息
    ip_address TEXT,
    user_agent TEXT,
    permission_context TEXT,             -- 权限判断上下文
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
```

---

## 3. 权限控制流程

### 3.1 查询权限控制（核心）

```
用户提问
    ↓
[1] 身份认证
    - 验证 JWT Token
    - 获取 user_id, tenant_id, department_id, role
    ↓
[2] 权限范围计算
    - 获取用户有权限的 department_ids（自己部门 + 下属部门）
    - 获取用户有权限的 knowledge_base_ids
    - 获取用户有权限的 document_ids
    ↓
[3] 带权限条件检索
    - 向量检索：WHERE tenant_id = ? AND department_id IN (?)
    - BM25 检索：同上过滤
    - 合并结果
    ↓
[4] 结果过滤
    - 再次验证每条结果的权限
    - 过滤掉无权限的内容
    ↓
[5] 敏感信息脱敏
    - 识别手机号、身份证、薪资等
    - 脱敏处理（如 138****1234）
    ↓
[6] 交给大模型
    - 已脱敏的上下文
    - 带上权限标识
    ↓
[7] 日志记录
    - 记录查询内容、权限条件、返回结果数
```

### 3.2 权限判断伪代码

```python
def get_user_permission_context(user: User) -> dict:
    """获取用户的权限上下文"""
    return {
        "tenant_id": user.tenant_id,
        "department_ids": get_department_tree(user.department_id),
        "role": user.role,
        "allowed_kb_ids": get_allowed_knowledge_bases(user),
        "allowed_doc_ids": get_allowed_documents(user),
    }

def query_with_permission(user: User, query: str, top_k: int = 5):
    """带权限的查询"""
    # 1. 获取权限上下文
    perm_ctx = get_user_permission_context(user)
    
    # 2. 带权限条件检索
    results = vector_store.search(
        query=query,
        top_k=top_k * 2,  # 多检索一些，过滤后可能不够
        where={
            "tenant_id": perm_ctx["tenant_id"],
            "department_id": {"$in": perm_ctx["department_ids"]}
        }
    )
    
    # 3. 过滤无权限结果
    filtered_results = [
        r for r in results 
        if r["metadata"]["document_id"] in perm_ctx["allowed_doc_ids"]
    ][:top_k]
    
    # 4. 脱敏处理
    sanitized_results = [sanitize_content(r) for r in filtered_results]
    
    # 5. 记录审计日志
    log_audit(user, "query", query, perm_ctx, len(sanitized_results))
    
    return sanitized_results
```

### 3.3 文档级权限检查

```python
def check_document_permission(user: User, doc_id: str, required_perm: str = "read") -> bool:
    """检查用户对文档的权限"""
    doc = get_document(doc_id)
    
    # 1. 租户检查
    if doc.tenant_id != user.tenant_id:
        return False
    
    # 2. 部门检查
    if doc.department_id and doc.department_id != user.department_id:
        # 检查是否是下属部门
        if not is_sub_department(user.department_id, doc.department_id):
            return False
    
    # 3. 可见性检查
    if doc.visibility == "private" and doc.owner_id != user.id:
        return False
    
    # 4. 显式权限检查
    perm = get_document_permission(doc_id, user.id, user.department_id)
    if perm:
        return has_permission(perm, required_perm)
    
    # 5. 角色默认权限
    return has_role_permission(user.role, required_perm)
```

---

## 4. 敏感信息脱敏

### 4.1 脱敏规则

| 类型 | 正则 | 脱敏方式 |
|------|------|----------|
| 手机号 | `1[3-9]\d{9}` | `138****1234` |
| 身份证 | `\d{17}[\dXx]` | `110***********1234` |
| 邮箱 | `[\w.]+@[\w.]+` | `a***@example.com` |
| 银行卡 | `\d{16,19}` | `6222********1234` |
| 薪资 | `￥?\d{4,}` | `[薪资已脱敏]` |
| IP地址 | `\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}` | `192.168.***.***` |

### 4.2 脱敏实现

```python
import re

SENSITIVE_PATTERNS = {
    "phone": (r"1[3-9]\d{9}", lambda m: m.group()[:3] + "****" + m.group()[-4:]),
    "id_card": (r"\d{17}[\dXx]", lambda m: m.group()[:3] + "***********" + m.group()[-4:]),
    "email": (r"[\w.]+@[\w.]+", lambda m: m.group()[0] + "***@" + m.group().split("@")[1]),
    "bank_card": (r"\d{16,19}", lambda m: m.group()[:4] + "********" + m.group()[-4:]),
    "salary": (r"￥?\d{4,}", lambda m: "[薪资已脱敏]"),
    "ip": (r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", lambda m: ".".join(m.group().split(".")[:2]) + ".***.***"),
}

def sanitize_content(text: str) -> str:
    """对文本进行敏感信息脱敏"""
    for name, (pattern, replacer) in SENSITIVE_PATTERNS.items():
        text = re.sub(pattern, replacer, text)
    return text
```

---

## 5. 审计日志

### 5.1 需要记录的操作

| 操作 | 记录内容 |
|------|----------|
| 登录/登出 | user_id, ip, timestamp, success/fail |
| 查询 | query, permission_context, result_count, latency |
| 上传文档 | filename, kb_id, department_id, file_size |
| 下载文档 | doc_id, user_id, timestamp |
| 删除文档 | doc_id, user_id, reason |
| 修改权限 | doc_id, target_user, old_perm, new_perm |
| 创建知识库 | kb_name, department_id, visibility |
| 删除知识库 | kb_id, user_id, reason |

### 5.2 审计日志查询

```python
def query_audit_logs(
    tenant_id: str,
    user_id: str = None,
    action: str = None,
    start_time: datetime = None,
    end_time: datetime = None,
    page: int = 1,
    page_size: int = 20
) -> dict:
    """查询审计日志"""
    # 只能查本租户的日志
    # tenant_admin 可以查所有用户
    # department_admin 只能查本部门
    pass
```

---

## 6. API 设计

### 6.1 用户管理

```
POST   /api/v1/users                    # 创建用户
GET    /api/v1/users                    # 用户列表
GET    /api/v1/users/{id}               # 用户详情
PUT    /api/v1/users/{id}               # 更新用户
DELETE /api/v1/users/{id}               # 删除用户
POST   /api/v1/users/{id}/reset-password # 重置密码
```

### 6.2 部门管理

```
POST   /api/v1/departments              # 创建部门
GET    /api/v1/departments              # 部门列表
GET    /api/v1/departments/{id}         # 部门详情
PUT    /api/v1/departments/{id}         # 更新部门
DELETE /api/v1/departments/{id}         # 删除部门
GET    /api/v1/departments/{id}/tree    # 部门树
```

### 6.3 文档权限

```
GET    /api/v1/documents/{id}/permissions  # 查看权限
POST   /api/v1/documents/{id}/permissions  # 设置权限
DELETE /api/v1/documents/{id}/permissions/{perm_id}  # 删除权限
```

### 6.4 审计日志

```
GET    /api/v1/audit-logs              # 查询审计日志
GET    /api/v1/audit-logs/export       # 导出审计日志
```

---

## 7. 实施计划

### Phase 1：部门隔离（1-2 周）

- [ ] 扩展 users 表，添加 tenant_id, department_id
- [ ] 扩展 knowledge_bases 表，添加 department_id
- [ ] 扩展 documents 表，添加 department_id
- [ ] 修改检索逻辑，自动过滤同部门数据
- [ ] 添加部门管理 API
- [ ] 更新前端用户管理页面

### Phase 2：文档级权限（2-3 周）

- [ ] 创建 document_permissions 表
- [ ] 实现文档权限检查函数
- [ ] 修改检索逻辑，只返回有权限的文档
- [ ] 添加文档权限管理 API
- [ ] 更新前端文档管理页面

### Phase 3：结果脱敏（1 周）

- [ ] 实现脱敏规则引擎
- [ ] 在检索结果返回前进行脱敏
- [ ] 添加脱敏规则配置 API
- [ ] 记录脱敏日志

### Phase 4：审计日志（1 周）

- [ ] 扩展审计日志表
- [ ] 记录所有权限相关操作
- [ ] 添加审计日志查询 API
- [ ] 添加审计日志导出功能

### Phase 5：高级功能（可选）

- [ ] Chunk 级权限标签
- [ ] 动态权限策略
- [ ] 权限继承
- [ ] 临时权限授予

---

## 8. 测试用例

### 8.1 权限隔离测试

```python
def test_department_isolation():
    """测试部门数据隔离"""
    # 用户 A 属于部门 1
    # 用户 B 属于部门 2
    # 用户 A 上传文档到部门 1
    # 用户 B 查询时不应该看到部门 1 的文档
    pass

def test_document_permission():
    """测试文档级权限"""
    # 文档 D 设置为仅 HR 部门可读
    # 普通员工查询时不应该返回文档 D 的内容
    pass

def test_role_hierarchy():
    """测试角色层级"""
    # admin 可以查看所有内容
    # viewer 只能查看授权内容
    pass
```

### 8.2 脱敏测试

```python
def test_phone_masking():
    """测试手机号脱敏"""
    text = "联系方式：13812345678"
    result = sanitize_content(text)
    assert "138****5678" in result
    assert "13812345678" not in result

def test_id_card_masking():
    """测试身份证脱敏"""
    text = "身份证：110101199001011234"
    result = sanitize_content(text)
    assert "110***********1234" in result
```

---

## 9. 安全注意事项

1. **权限判断必须在后端**：前端权限控制只是 UI 展示，不能作为安全依据
2. **最小权限原则**：默认不给权限，需要显式授予
3. **权限缓存**：权限变化后需要清除缓存
4. **日志不可删除**：审计日志一旦写入不能修改或删除
5. **密码安全**：密码必须 bcrypt 加密，不能明文存储
6. **API Key 安全**：API Key 需要定期轮换

---

## 10. 与现有系统的兼容

### 10.1 迁移策略

1. 现有用户自动成为 `tenant_admin`，所属租户为默认租户
2. 现有知识库和文档的 `department_id` 设为 NULL（全员可见）
3. 保持现有 API 接口不变，新增权限相关接口

### 10.2 配置开关

```python
# config.py
ENABLE_DEPARTMENT_ISOLATION = True    # 启用部门隔离
ENABLE_DOCUMENT_PERMISSION = True    # 启用文档权限
ENABLE_CONTENT_SANITIZATION = True   # 启用内容脱敏
ENABLE_AUDIT_LOG = True              # 启用审计日志
```

---

## 11. 总结

本方案实现了企业级 RAG 系统所需的权限控制能力：

1. **多级角色体系**：支持从超管到普通用户的 5 级角色
2. **数据隔离**：通过租户和部门实现数据隔离
3. **文档级权限**：支持 read/write/admin 三级权限
4. **结果脱敏**：自动识别和脱敏敏感信息
5. **完整审计**：记录所有权限相关操作

通过分阶段实施，可以在不影响现有功能的情况下逐步完善权限系统。
