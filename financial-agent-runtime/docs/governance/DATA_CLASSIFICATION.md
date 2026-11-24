# 数据分类与最小化采集（最小实现）

日期：2026-11-23
范围：治理项 2（数据治理）的可执行分类表。

---

## 1. 分类表

| 类别 | 示例 | 采集最小化 | 存储 | 脱敏 | 留存 | 访问 |
|---|---|---|---|---|---|---|
| P0 禁止采集 | 密码、完整卡号/PAN、CVV、私钥、完整身份证号 | 不采集；如误入立即拒绝并告警 | 不落库 | — | — | — |
| P1 高敏 | 交易明细、账户余额、客户姓名、手机号 | 仅业务必需字段；ID 化引用优先 | checkpoint AES-256-GCM 加密；DB 加密 | 显示/遥测一律 `***REDACTED***` 或掩码 | 按合规最短（当前 24h namespace retention 仅 Temporal；业务数据由 HUMAN 定） | `audit:read` + tenant RLS + Resource ACL |
| P2 内部 | 租户 ID、用户 ID、角色、权限、Run 元数据、prompt | 仅任务所需 | 普通 DB（RLS） | 日志中不打印完整 prompt | 跟随 Run 生命周期 | tenant + ACL |
| P3 公开 | agent_id、model_name、harness_version、指标 | 无限制 | 普通 | 无 | 按需 | 认证用户 |

---

## 2. 代码级护栏（已实现）

- `context/pack.py`：显示/遥测视图敏感值返回 `***REDACTED***`。
- `db.py`：`account_number_masked` 字段（不存完整账号）。
- `checkpoint/builder.py`：secret scrub（access_token/api_key 等）。
- `checkpoint/crypto.py`：AES-256-GCM + SHA-256 digest + canonical JSON。
- `migrations/0003_enable_rls.py`：租户表 RLS。

---

## 3. NOT ENFORCEABLE / HUMAN

- 真实 KMS 密钥管理与轮换（当前 `SNAPSHOT_ENCRYPTION_KEYS` 环境变量占位）。
- 删除请求执行（GDPR/个保法流程）、跨境数据传输审批。
- 访问审计长期归档与不可篡改存储。

---

## 4. 红线

不得将 P1 数据写入日志、prompt 遥测或未加密的 checkpoint；发现即视为上线阻断。
