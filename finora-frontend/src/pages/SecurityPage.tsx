import { useState } from 'react';
import { Alert, Button, Card, Descriptions, Drawer, Empty, Space, Table, Tabs, Tag, Typography } from 'antd';
import type { TableColumnsType } from 'antd';
import { ApiOutlined, AuditOutlined, LockOutlined, SafetyCertificateOutlined, TeamOutlined } from '@ant-design/icons';
import { api } from '../api';
import { useAsyncData } from '../hooks/useAsyncData';
import { useApp } from '../state/AppContext';
import { ApiUnavailable, DecisionTag, DemoBanner, formatTime, shortId } from '../components/Ui';
import type {
  AclEntry, AbacPolicy, ApprovalRecord, DelegatedIdentity, McpToolRecord, RoleRecord,
  SecurityEvent, TenantRecord, UserRecord,
} from '../types';

const { Text, Paragraph } = Typography;

export function SecurityPage() {
  const { identity, switchIdentity, switchTenant, refreshToken, requestRefresh, notify } = useApp();
  const [selectedUser, setSelectedUser] = useState<UserRecord | null>(null);
  const [selectedRole, setSelectedRole] = useState<RoleRecord | null>(null);
  const securityQuery = useAsyncData(() => api.getSecuritySnapshot(), [refreshToken]);
  const toolsQuery = useAsyncData(() => api.listMcpTools(), [refreshToken]);
  const snapshot = securityQuery.data;

  const changeIdentity = (role: 'analyst' | 'compliance' | 'auditor') => {
    switchIdentity(role);
    notify('info', '已切换演示身份（非真实认证）');
    requestRefresh();
  };

  const tenantColumns: TableColumnsType<TenantRecord> = [
    { title: '租户编号', dataIndex: 'tenant_id', render: (value: string) => <Text code>{value}</Text> },
    { title: '名称', dataIndex: 'name' },
    { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={value === 'ACTIVE' ? 'success' : 'error'}>{value}</Tag> },
    { title: '用户数', dataIndex: 'users' },
    { title: '创建时间', dataIndex: 'created_at', render: formatTime },
    { title: '', key: 'action', render: (_, record) => <Button type="link" onClick={() => { switchTenant(record.tenant_id); requestRefresh(); notify('info', `当前数据租户已切换为 ${record.tenant_id}（演示）`); }}>切换数据租户</Button> },
  ];

  const userColumns: TableColumnsType<UserRecord> = [
    { title: '用户编号', dataIndex: 'user_id', render: (value: string) => <Text code>{value}</Text> },
    { title: '姓名', dataIndex: 'display_name' },
    { title: '租户', dataIndex: 'tenant_id' },
    { title: '角色', dataIndex: 'roles', render: (value: string[]) => <Space size={3}>{value.map((role) => <Tag key={role} color="blue">{role}</Tag>)}</Space> },
    { title: '代理身份', dataIndex: 'delegated_identity', render: (value: string) => <Text code>{shortId(value)}</Text> },
    { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={value === 'ACTIVE' ? 'success' : 'error'}>{value}</Tag> },
    { title: '最近活跃', dataIndex: 'last_active_at', render: formatTime },
    { title: '', key: 'action', render: (_, record) => <Button type="link" onClick={() => setSelectedUser(record)}>详情</Button> },
  ];

  const roleColumns: TableColumnsType<RoleRecord> = [
    { title: '角色编号', dataIndex: 'role_id', render: (value: string) => <Text code>{value}</Text> },
    { title: '角色', dataIndex: 'name' },
    { title: '说明', dataIndex: 'description', ellipsis: true },
    { title: '角色权限', dataIndex: 'permissions', render: (value: string[]) => <Space size={3} wrap>{value.map((permission) => <Tag key={permission}>{permission}</Tag>)}</Space> },
    { title: '成员数', dataIndex: 'member_count', width: 90 },
    { title: '', key: 'action', render: (_, record) => <Button type="link" onClick={() => setSelectedRole(record)}>详情</Button> },
  ];

  const abacColumns: TableColumnsType<AbacPolicy> = [
    { title: '策略编号', dataIndex: 'policy_id', render: (value: string) => <Text code>{value}</Text> },
    { title: '名称', dataIndex: 'name' },
    { title: '效果', dataIndex: 'effect', render: (value: AbacPolicy['effect']) => <DecisionTag decision={value} /> },
    { title: '条件', dataIndex: 'conditions', render: (value: string) => <Text code>{value}</Text> },
    { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={value === 'ACTIVE' ? 'success' : 'default'}>{value}</Tag> },
  ];

  const aclColumns: TableColumnsType<AclEntry> = [
    { title: '资源', dataIndex: 'resource', render: (value: string) => <Text code>{value}</Text> },
    { title: '主体', dataIndex: 'subject' },
    { title: '权限', dataIndex: 'permission', render: (value: string) => <Tag color="blue">{value}</Tag> },
    { title: '效果', dataIndex: 'effect', render: (value: AclEntry['effect']) => <DecisionTag decision={value} /> },
    { title: '来源', dataIndex: 'source' },
  ];

  const toolColumns: TableColumnsType<McpToolRecord> = [
    { title: '工具', dataIndex: 'name', render: (value: string) => <Text code>{value}</Text> },
    { title: '服务', dataIndex: 'server_id' },
    { title: '所需权限', dataIndex: 'required_scopes', render: (value: string[]) => <Space size={3}>{value.map((scope) => <Tag key={scope}>{scope}</Tag>)}</Space> },
    { title: '风险', dataIndex: 'risk_level', render: (value: string) => <Tag color={value === 'high' || value === 'critical' ? 'volcano' : 'blue'}>{value}</Tag> },
  ];

  const delegatedColumns: TableColumnsType<DelegatedIdentity> = [
    { title: '身份编号', dataIndex: 'identity_id', render: (value: string) => <Text code>{value}</Text> },
    { title: '名称', dataIndex: 'name' },
    { title: '所有者', dataIndex: 'owner' },
    { title: '租户', dataIndex: 'tenant_id' },
    { title: '允许的工具', dataIndex: 'allowed_tools', render: (value: string[]) => value.join(', ') || '—' },
    { title: '权限范围', dataIndex: 'scopes', render: (value: string[]) => <Space size={3}>{value.map((scope) => <Tag key={scope}>{scope}</Tag>)}</Space> },
    { title: '过期时间', dataIndex: 'expires_at', render: formatTime },
  ];

  const approvalColumns: TableColumnsType<ApprovalRecord> = [
    { title: '审批编号', dataIndex: 'approval_id', render: (value: string) => <Text code>{shortId(value)}</Text> },
    { title: '运行编号', dataIndex: 'run_id', render: (value: string) => <Text code>{shortId(value)}</Text> },
    { title: '申请人', dataIndex: 'requester' },
    { title: '审批人', dataIndex: 'approver' },
    { title: '决定', dataIndex: 'decision', render: (value: string) => <Tag color={value === 'APPROVED' ? 'success' : value === 'REJECTED' ? 'error' : 'processing'}>{value}</Tag> },
    { title: '理由', dataIndex: 'reason', ellipsis: true },
    { title: '时间', dataIndex: 'time', render: formatTime },
  ];

  const eventColumns: TableColumnsType<SecurityEvent> = [
    { title: '事件编号', dataIndex: 'event_id', render: (value: string) => <Text code>{shortId(value)}</Text> },
    { title: '类型', dataIndex: 'type', render: (value: string) => <Tag color={value.includes('BLOCK') || value.includes('DENIED') ? 'volcano' : 'blue'}>{value}</Tag> },
    { title: '操作者', dataIndex: 'actor' },
    { title: '租户', dataIndex: 'tenant_id' },
    { title: '目标', dataIndex: 'target', render: (value: string) => <Text code>{value}</Text> },
    { title: '原因', dataIndex: 'reason', ellipsis: true },
    { title: '决定', dataIndex: 'decision', render: (value: SecurityEvent['decision']) => <DecisionTag decision={value} /> },
    { title: '时间', dataIndex: 'time', render: formatTime },
  ];

  return <>
    <DemoBanner note="租户、角色、属性访问控制策略、资源访问控制列表、代理身份、审批与拒绝记录均为演示数据。" />
    <Alert className="dev-alert" type="warning" showIcon message="演示身份切换仅用于开发演示"
      description="前端切换用户不会进行登录认证或权限校验；真实权限必须由后端基于已验证身份、租户上下文、权限范围、属性访问控制与资源访问控制列表执行。" />
    <Card className="panel-card" bordered={false}>
      <div className="identity-demo-bar">
        <div><Text strong>演示身份</Text><Text type="secondary"> 当前：{identity.tenantId} / {identity.userId}（{identity.displayName}）</Text></div>
        <Space wrap>
          <Button size="small" onClick={() => changeIdentity('analyst')}>分析人员</Button>
          <Button size="small" onClick={() => changeIdentity('compliance')}>合规复核员</Button>
          <Button size="small" onClick={() => changeIdentity('auditor')}>审计观察员</Button>
        </Space>
      </div>
    </Card>
    {securityQuery.error && <ApiUnavailable error={securityQuery.error} feature="企业权限与安全中心" />}
    <Card className="panel-card" bordered={false}>
      <Tabs items={[
        {
          key: 'tenants', label: <span><TeamOutlined /> 租户与用户</span>,
          children: <>
            <div className="section-label">租户列表</div>
            <Table rowKey="tenant_id" columns={tenantColumns} dataSource={snapshot?.tenants || []} pagination={false} size="small" locale={{ emptyText: <Empty description="无租户数据" /> }} />
            <div className="subtable-gap" />
            <div className="section-label">用户列表</div>
            <Table rowKey="user_id" columns={userColumns} dataSource={snapshot?.users || []} pagination={false} size="small" scroll={{ x: 1_100 }} locale={{ emptyText: <Empty description="无用户数据" /> }} />
          </>,
        },
        {
          key: 'roles', label: <span><SafetyCertificateOutlined /> 角色权限</span>,
          children: <>
            <Paragraph type="secondary">角色权限负责粗粒度权限，属性访问控制与资源访问控制列表负责细粒度条件判断。以下均为演示策略。</Paragraph>
            <Table rowKey="role_id" columns={roleColumns} dataSource={snapshot?.roles || []} pagination={false} scroll={{ x: 1_100 }} locale={{ emptyText: <Empty description="无角色数据" /> }} />
          </>,
        },
        {
          key: 'abac', label: <span><LockOutlined /> 属性与访问控制</span>,
          children: <>
            <div className="section-label">属性访问控制策略</div>
            <Table rowKey="policy_id" columns={abacColumns} dataSource={snapshot?.abacPolicies || []} pagination={false} size="small" locale={{ emptyText: <Empty description="无属性访问控制策略" /> }} />
            <div className="subtable-gap" />
            <div className="section-label">资源访问控制列表</div>
            <Table rowKey="resource" columns={aclColumns} dataSource={snapshot?.acl || []} pagination={false} size="small" locale={{ emptyText: <Empty description="无资源访问控制记录" /> }} />
          </>,
        },
        {
          key: 'mcp', label: <span><ApiOutlined /> 工具与代理身份</span>,
          children: toolsQuery.error
            ? <ApiUnavailable error={toolsQuery.error} feature="工具权限" />
            : <>
              <div className="section-label">工具所需权限</div>
              <Table rowKey="name" columns={toolColumns} dataSource={toolsQuery.data || []} pagination={false} size="small" locale={{ emptyText: <Empty description="无工具权限数据" /> }} />
              <div className="subtable-gap" />
              <div className="section-label">智能体代理身份</div>
              <Table rowKey="identity_id" columns={delegatedColumns} dataSource={snapshot?.delegatedIdentities || []} pagination={false} size="small" scroll={{ x: 1_100 }} locale={{ emptyText: <Empty description="无代理身份" /> }} />
            </>,
        },
        {
          key: 'events', label: <span><AuditOutlined /> 审批与拦截</span>,
          children: <>
            <div className="section-label">审批记录</div>
            <Table rowKey="approval_id" columns={approvalColumns} dataSource={snapshot?.approvals || []} pagination={false} size="small" scroll={{ x: 1_100 }} locale={{ emptyText: <Empty description="无审批记录" /> }} />
            <div className="subtable-gap" />
            <div className="section-label">权限拒绝记录</div>
            <Table rowKey="event_id" columns={eventColumns} dataSource={(snapshot?.events || []).filter((event) => event.decision === 'DENIED')} pagination={false} size="small" scroll={{ x: 1_200 }} locale={{ emptyText: <Empty description="无拒绝记录" /> }} />
            <div className="subtable-gap" />
            <div className="section-label">跨租户访问拦截记录</div>
            <Table rowKey="event_id" columns={eventColumns} dataSource={(snapshot?.events || []).filter((event) => event.type === 'CROSS_TENANT_BLOCK')} pagination={false} size="small" scroll={{ x: 1_200 }} locale={{ emptyText: <Empty description="无跨租户拦截记录" /> }} />
          </>,
        },
      ]} />
    </Card>

    <Drawer width={600} open={Boolean(selectedUser)} onClose={() => setSelectedUser(null)}
      title={<div className="drawer-title"><div><span>用户详情</span><Text code>{selectedUser?.user_id}</Text></div>{selectedUser && <Tag color={selectedUser.status === 'ACTIVE' ? 'success' : 'error'}>{selectedUser.status}</Tag>}</div>}>
      {selectedUser && <div className="run-detail">
        <Descriptions bordered size="small" column={1}>
          <Descriptions.Item label="姓名">{selectedUser.display_name}</Descriptions.Item>
          <Descriptions.Item label="租户">{selectedUser.tenant_id}</Descriptions.Item>
          <Descriptions.Item label="角色">{selectedUser.roles.join(', ')}</Descriptions.Item>
          <Descriptions.Item label="代理身份"><Text code>{selectedUser.delegated_identity}</Text></Descriptions.Item>
          <Descriptions.Item label="最近活跃">{formatTime(selectedUser.last_active_at)}</Descriptions.Item>
        </Descriptions>
        <div className="section-label">关联权限</div>
        <Space wrap>{(snapshot?.roles || []).filter((role) => selectedUser.roles.includes(role.name)).flatMap((role) => role.permissions).map((permission, index) => <Tag key={`${permission}-${index}`} color="blue">{permission}</Tag>)}</Space>
        <div className="section-label">关联安全事件</div>
        <Table rowKey="event_id" size="small" pagination={false} columns={eventColumns} dataSource={(snapshot?.events || []).filter((event) => event.actor === selectedUser.user_id)} scroll={{ x: 1_100 }} />
      </div>}
    </Drawer>

    <Drawer width={600} open={Boolean(selectedRole)} onClose={() => setSelectedRole(null)}
      title={<div className="drawer-title"><div><span>角色权限详情</span><Text code>{selectedRole?.role_id}</Text></div>{selectedRole && <Tag color="blue">{selectedRole.name}</Tag>}</div>}>
      {selectedRole && <div className="run-detail">
        <Paragraph>{selectedRole.description}</Paragraph>
        <Descriptions bordered size="small" column={1}>
          <Descriptions.Item label="成员数">{selectedRole.member_count}</Descriptions.Item>
          <Descriptions.Item label="角色权限">{selectedRole.permissions.join(', ')}</Descriptions.Item>
        </Descriptions>
        <div className="section-label">成员</div>
        <Space wrap>{(snapshot?.users || []).filter((user) => user.roles.includes(selectedRole.name)).map((user) => <Tag key={user.user_id}>{user.user_id}</Tag>)}</Space>
      </div>}
    </Drawer>
  </>;
}
