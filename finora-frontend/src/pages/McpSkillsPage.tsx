import { useState } from 'react';
import { Alert, Button, Card, Drawer, Empty, Space, Switch, Table, Tabs, Tag, Typography } from 'antd';
import type { TableColumnsType } from 'antd';
import { ApiOutlined, BookOutlined, ToolOutlined } from '@ant-design/icons';
import { api } from '../api';
import { useAsyncData } from '../hooks/useAsyncData';
import { useApp } from '../state/AppContext';
import { ApiUnavailable, DemoBanner, riskLabel, skillLabel, toolLabel } from '../components/Ui';
import type { ManagedSkill, McpServerRecord, McpToolRecord } from '../types';

const { Text, Paragraph } = Typography;

function WorkTag({ status }: { status: McpServerRecord['status'] }) {
  if (status === 'HEALTHY') return <Tag color="success">正在工作</Tag>;
  if (status === 'DEGRADED') return <Tag color="warning">部分可用</Tag>;
  return <Tag color="error">未工作</Tag>;
}

export function McpSkillsPage() {
  const { refreshToken, notify, requestRefresh } = useApp();
  const [selectedSkill, setSelectedSkill] = useState<ManagedSkill | null>(null);
  const serversQuery = useAsyncData(() => api.listMcpServers(), [refreshToken]);
  const toolsQuery = useAsyncData(() => api.listMcpTools(), [refreshToken]);
  const skillsQuery = useAsyncData(() => api.listManagedSkills(), [refreshToken]);
  const gatewayToolsQuery = useAsyncData(() => api.listTools(), [refreshToken]);

  const tools = toolsQuery.data || [];
  const skills = skillsQuery.data || [];

  const toggleSkill = async (skill: ManagedSkill, enabled: boolean) => {
    try {
      await api.updateSkillEnabled(skill.skill_id, enabled);
      notify('success', `技能“${skillLabel(skill.skill_id, skill.name, skill.display_name)}”已${enabled ? '启用' : '停用'}`);
      requestRefresh();
    } catch (error) {
      notify('error', error instanceof Error ? error.message : '操作失败');
    }
  };

  const serverColumns: TableColumnsType<McpServerRecord> = [
    {
      title: '详细描述', key: 'description',
      render: (_, record) => <div className="table-primary"><b>{record.name}</b><span>{record.description}</span></div>,
    },
    {
      title: '是否正在工作', dataIndex: 'status', width: 140,
      render: (status: McpServerRecord['status']) => <WorkTag status={status} />,
    },
  ];

  const mcpToolColumns: TableColumnsType<McpToolRecord> = [
    { title: '工具名称', key: 'name', render: (_, record) => <b>{toolLabel(record.name, record.display_name)}</b> },
    { title: '功能说明', dataIndex: 'description' },
    { title: '风险级别', dataIndex: 'risk_level', width: 110, render: (value: string) => <Tag color={value === 'high' || value === 'critical' ? 'volcano' : value === 'medium' ? 'gold' : 'green'}>{riskLabel(value)}</Tag> },
    { title: '幂等性', dataIndex: 'idempotent', width: 110, render: (value: boolean) => value ? <Tag color="success">支持幂等</Tag> : <Tag color="warning">需谨慎</Tag> },
  ];

  const skillColumns: TableColumnsType<ManagedSkill> = [
    { title: '技能名称', key: 'name', render: (_, record) => <b>{skillLabel(record.skill_id, record.name, record.display_name)}</b> },
    { title: '功能说明', dataIndex: 'description' },
    { title: '启用状态', dataIndex: 'enabled', width: 150, render: (value: boolean, record) => <Space size={6}><Switch size="small" checked={value} onChange={(next) => void toggleSkill(record, next)} /><Text type="secondary">{value ? '已启用' : '已停用'}</Text></Space> },
    { title: '操作', key: 'action', width: 80, render: (_, record) => <Button type="link" onClick={() => setSelectedSkill(record)}>详情</Button> },
  ];

  const gatewayColumns: TableColumnsType<{ name: string; display_name?: string; description: string; required_scopes: string[]; risk_level: string; idempotent: boolean }> = [
    { title: '工具名称', key: 'name', render: (_, record) => <b>{toolLabel(record.name, record.display_name)}</b> },
    { title: '功能说明', dataIndex: 'description' },
    { title: '风险级别', dataIndex: 'risk_level', width: 110, render: (value: string) => <Tag color={value === 'high' || value === 'critical' ? 'volcano' : value === 'medium' ? 'gold' : 'green'}>{riskLabel(value)}</Tag> },
    { title: '幂等性', dataIndex: 'idempotent', width: 110, render: (value: boolean) => value ? <Tag color="success">支持幂等</Tag> : <Tag color="warning">需谨慎</Tag> },
  ];

  return <>
    <DemoBanner note="MCP 服务说明与工作状态来自后端连接结果；技能说明为演示数据。" />
    <Card className="panel-card" bordered={false}>
      <Tabs items={[
        {
          key: 'mcp', label: <span><ApiOutlined /> MCP 服务管理</span>,
          children: serversQuery.error
            ? <ApiUnavailable error={serversQuery.error} feature="MCP 服务管理" />
            : <>
              <Alert className="inline-alert" type="info" showIcon message="这里只展示服务说明和是否正在工作"
                description="MCP 的连接、工具发现和调用全部由后端完成；前端不直接连接外部 MCP。" />
              <Table rowKey="server_id" columns={serverColumns} dataSource={serversQuery.data || []} loading={serversQuery.loading} pagination={false}
                locale={{ emptyText: <Empty description="暂无 MCP 服务；请在后端配置 MCP_SERVERS_JSON" /> }} />
              <div className="subtable-gap" />
              <div className="section-label"><ToolOutlined /> MCP 工具目录</div>
              {toolsQuery.error
                ? <ApiUnavailable error={toolsQuery.error} feature="MCP 工具目录" />
                : <Table rowKey="name" columns={mcpToolColumns} dataSource={tools} pagination={false} size="small" locale={{ emptyText: <Empty description="暂无 MCP 工具" /> }} />}
            </>,
        },
        {
          key: 'skills', label: <span><BookOutlined /> 技能管理</span>,
          children: skillsQuery.error
            ? <ApiUnavailable error={skillsQuery.error} feature="技能管理" />
            : <>
              <Alert className="inline-alert" type="warning" showIcon message="技能是给 Agent 的执行指引"
                description="这里只展示技能能做什么。真实权限由后端工具网关统一校验，技能文件本身不授予任何权限。" />
              <Table rowKey="skill_id" columns={skillColumns} dataSource={skills} loading={skillsQuery.loading} pagination={false}
                scroll={{ x: 900 }} locale={{ emptyText: <Empty description="暂无技能数据" /> }} />
            </>,
        },
        {
          key: 'gateway', label: <span><ToolOutlined /> 工具网关</span>,
          children: <>
            <Alert className="inline-alert" type="info" showIcon message="工具是 Agent 可调用的具体能力"
              description="这里只展示工具的业务用途；权限、风险和幂等保护由后端在调用时强制执行。" />
            <Table rowKey="name" columns={gatewayColumns} dataSource={gatewayToolsQuery.data || []} loading={gatewayToolsQuery.loading} pagination={false}
              locale={{ emptyText: <Empty description="工具注册表暂不可用" /> }} />
          </>,
        },
      ]} />
    </Card>

    <Drawer width={520} open={Boolean(selectedSkill)} onClose={() => setSelectedSkill(null)}
      title={<div className="drawer-title"><span>技能详情</span>{selectedSkill && <Tag color={selectedSkill.enabled ? 'success' : 'default'}>{selectedSkill.enabled ? '已启用' : '已停用'}</Tag>}</div>}>
      {selectedSkill && <div className="run-detail">
        <div className="detail-title-small">{skillLabel(selectedSkill.skill_id, selectedSkill.name, selectedSkill.display_name)}</div>
        <Paragraph>{selectedSkill.description}</Paragraph>
        <Alert className="drawer-alert" type="info" showIcon message="说明" description="技能内容用于指导任务规划；真正的执行边界由后端工具网关与权限系统约束。" />
      </div>}
    </Drawer>
  </>;
}
