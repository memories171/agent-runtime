import { useMemo, useState } from 'react';
import {
  Alert, Button, Card, Descriptions, Drawer, Empty, Form, Input, InputNumber,
  Modal, Popconfirm, Row, Col, Select, Space, Table, Tag, Typography,
} from 'antd';
import type { TableColumnsType } from 'antd';
import {
  DeleteOutlined, EditOutlined, EyeOutlined, InboxOutlined, PlusOutlined, ReloadOutlined,
} from '@ant-design/icons';
import { api } from '../api';
import { useAsyncData } from '../hooks/useAsyncData';
import { useApp } from '../state/AppContext';
import { ApiUnavailable, DemoBanner, formatTime, MemoryTypeTag, ScopeTag, shortId } from '../components/Ui';
import type { MemoryRecord, MemoryScope, MemoryType } from '../types';

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const { Paragraph, Text } = Typography;

type MemoryFormValues = {
  title: string;
  content: string;
  type: MemoryType;
  scope: MemoryScope;
  confidence: number;
  source_run_id?: string;
  expires_at?: string;
  acl_text?: string;
};

export function MemoryPage() {
  const { refreshToken, notify, requestRefresh } = useApp();
  const [keyword, setKeyword] = useState('');
  const [typeFilter, setTypeFilter] = useState<MemoryType | 'ALL'>('ALL');
  const [scopeFilter, setScopeFilter] = useState<MemoryScope | 'ALL'>('ALL');
  const [showArchived, setShowArchived] = useState(true);
  const [selected, setSelected] = useState<MemoryRecord | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<MemoryRecord | null>(null);
  const [form] = Form.useForm<MemoryFormValues>();

  const memoryQuery = useAsyncData(() => api.listMemories(), [refreshToken]);
  const memories = memoryQuery.data || [];

  const filtered = useMemo(() => memories.filter((item) => {
    const matchKeyword = !keyword || `${item.title}${item.content}${item.memory_id}`.toLowerCase().includes(keyword.toLowerCase());
    const matchType = typeFilter === 'ALL' || item.type === typeFilter;
    const matchScope = scopeFilter === 'ALL' || item.scope === scopeFilter;
    const matchArchived = showArchived || !item.archived;
    return matchKeyword && matchType && matchScope && matchArchived;
  }), [memories, keyword, typeFilter, scopeFilter, showArchived]);

  const counts = useMemo(() => ({
    WORKING: memories.filter((item) => item.type === 'WORKING').length,
    EPISODIC: memories.filter((item) => item.type === 'EPISODIC').length,
    SEMANTIC: memories.filter((item) => item.type === 'SEMANTIC').length,
    PROCEDURAL: memories.filter((item) => item.type === 'PROCEDURAL').length,
  }), [memories]);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ type: 'EPISODIC', scope: 'user', confidence: 0.8 });
    setFormOpen(true);
  };

  const openEdit = (record: MemoryRecord) => {
    setEditing(record);
    form.setFieldsValue({
      title: record.title, content: record.content, type: record.type, scope: record.scope,
      confidence: record.confidence, source_run_id: record.source_run_id || '',
      expires_at: record.expires_at || '', acl_text: record.acl.join(', '),
    });
    setFormOpen(true);
  };

  const submit = async () => {
    let values: Awaited<ReturnType<typeof form.validateFields>>;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const acl = (values.acl_text || '').split(',').map((item) => item.trim()).filter(Boolean);
    try {
      if (editing) {
        await api.updateMemory(editing.memory_id, {
          title: values.title, content: values.content, type: values.type, scope: values.scope,
          confidence: values.confidence, expires_at: values.expires_at || null, acl,
        });
        notify('success', '记忆已更新');
      } else {
        await api.createMemory({
          title: values.title, content: values.content, type: values.type, scope: values.scope,
          confidence: values.confidence, source_run_id: values.source_run_id || null,
          expires_at: values.expires_at || null, acl,
        });
        notify('success', '记忆已创建');
      }
      setFormOpen(false);
      requestRefresh();
    } catch (error) {
      notify('error', error instanceof Error ? error.message : '保存失败');
    }
  };

  const archive = async (record: MemoryRecord) => {
    try {
      await api.archiveMemory(record.memory_id);
      notify('success', '记忆已归档');
      requestRefresh();
    } catch (error) {
      notify('error', error instanceof Error ? error.message : '归档失败');
    }
  };

  const remove = async (record: MemoryRecord) => {
    try {
      await api.deleteMemory(record.memory_id);
      notify('success', '记忆已删除');
      requestRefresh();
    } catch (error) {
      notify('error', error instanceof Error ? error.message : '删除失败');
    }
  };

  const columns: TableColumnsType<MemoryRecord> = [
    { title: 'Memory ID', dataIndex: 'memory_id', width: 150, render: (value: string) => <Text code>{shortId(value)}</Text> },
    { title: '标题 / 内容', key: 'title', ellipsis: true, render: (_, record) => <div className="table-primary"><b>{record.title}</b><span>{record.content}</span></div> },
    { title: '类型', dataIndex: 'type', width: 120, render: (value: MemoryType) => <MemoryTypeTag type={value} /> },
    { title: 'Memory Scope', dataIndex: 'scope', width: 100, render: (value: MemoryScope) => <ScopeTag scope={value} /> },
    { title: 'Owner / Tenant', key: 'owner', width: 160, render: (_, record) => <div className="stack-cell"><span>{record.owner}</span><span>{record.tenant_id}</span></div> },
    { title: 'Source Run', dataIndex: 'source_run_id', width: 150, render: (value: string | null) => value ? <Text code>{shortId(value)}</Text> : '—' },
    { title: 'Confidence', dataIndex: 'confidence', width: 100, render: (value: number) => `${Math.round(value * 100)}%` },
    { title: 'Created / Updated', key: 'time', width: 170, render: (_, record) => <div className="stack-cell"><span>{formatTime(record.created_at)}</span><span>{formatTime(record.updated_at)}</span></div> },
    { title: 'Expiration', dataIndex: 'expires_at', width: 150, render: formatTime },
    {
      title: 'ACL', dataIndex: 'acl', width: 180,
      render: (value: string[]) => <Space size={3} wrap>{value.map((item) => <Tag key={item}>{item}</Tag>)}</Space>,
    },
    {
      title: '状态', dataIndex: 'archived', width: 90,
      render: (value: boolean) => value ? <Tag>已归档</Tag> : <Tag color="success">活跃</Tag>,
    },
    {
      title: '', key: 'action', width: 150, fixed: 'right',
      render: (_, record) => <Space size={0}>
        <Button type="text" icon={<EyeOutlined />} onClick={() => setSelected(record)}>详情</Button>
        <Button type="text" icon={<EditOutlined />} onClick={() => openEdit(record)} />
        <Popconfirm title="归档该记忆？" onConfirm={() => void archive(record)}><Button type="text" icon={<InboxOutlined />} /></Popconfirm>
        <Popconfirm title="删除该记忆？" onConfirm={() => void remove(record)}><Button type="text" danger icon={<DeleteOutlined />} /></Popconfirm>
      </Space>,
    },
  ];

  return <>
    <DemoBanner note="Memory 内容、Confidence、ACL、Expiration 与 CRUD 操作均为 DEMO / MOCK。" />
    <Row gutter={[16, 16]} className="memory-type-row">
      {(Object.keys(counts) as MemoryType[]).map((type) => <Col xs={12} md={6} key={type}>
        <Card className="memory-type-card" bordered={false}>
          <div><MemoryTypeTag type={type} /><b>{counts[type]}</b></div>
          <span>{{ WORKING: '当前会话上下文', EPISODIC: '历史任务事实', SEMANTIC: '稳定知识与约束', PROCEDURAL: '可复用流程' }[type]}</span>
        </Card>
      </Col>)}
    </Row>
    {memoryQuery.error && <ApiUnavailable error={memoryQuery.error} feature="长期记忆" />}
    <Card className="panel-card filter-card" bordered={false}>
      <Space wrap className="filter-row">
        <Input.Search allowClear placeholder="搜索标题 / 内容 / Memory ID" value={keyword} onChange={(event) => setKeyword(event.target.value)} onSearch={setKeyword} style={{ width: 300 }} />
        <Select value={typeFilter} onChange={setTypeFilter} style={{ width: 150 }} options={[
          { label: '全部类型', value: 'ALL' }, { label: 'Working', value: 'WORKING' }, { label: 'Episodic', value: 'EPISODIC' },
          { label: 'Semantic', value: 'SEMANTIC' }, { label: 'Procedural', value: 'PROCEDURAL' },
        ]} />
        <Select value={scopeFilter} onChange={setScopeFilter} style={{ width: 130 }} options={[
          { label: '全部范围', value: 'ALL' }, { label: '个人级', value: 'user' }, { label: '团队级', value: 'team' }, { label: '组织级', value: 'organization' },
        ]} />
        <Select value={showArchived ? 'all' : 'active'} onChange={(value) => setShowArchived(value === 'all')} style={{ width: 130 }} options={[
          { label: '含已归档', value: 'all' }, { label: '仅活跃', value: 'active' },
        ]} />
        <Button icon={<ReloadOutlined />} onClick={() => requestRefresh()}>刷新</Button>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>创建记忆</Button>
      </Space>
    </Card>
    <Card className="panel-card table-card" bordered={false} title={<div className="card-title"><span>Memory Center</span><Text type="secondary">{filtered.length} 条记忆 · 个人 / 团队 / 组织级</Text></div>}>
      <Table rowKey="memory_id" columns={columns} dataSource={filtered} loading={memoryQuery.loading} pagination={{ pageSize: 10 }} scroll={{ x: 1_650 }}
        locale={{ emptyText: <Empty description="没有符合条件的记忆" /> }} />
    </Card>

    <Drawer width={640} open={Boolean(selected)} onClose={() => setSelected(null)}
      title={<div className="drawer-title"><div><span>记忆详情</span><Text code>{selected ? shortId(selected.memory_id) : ''}</Text></div>{selected && <ScopeTag scope={selected.scope} />}</div>}>
      {selected && <div className="run-detail">
        <TitleSmall>{selected.title}</TitleSmall>
        <Paragraph>{selected.content}</Paragraph>
        <Descriptions bordered size="small" column={1}>
          <Descriptions.Item label="Memory ID"><Text code>{selected.memory_id}</Text></Descriptions.Item>
          <Descriptions.Item label="类型"><MemoryTypeTag type={selected.type} /></Descriptions.Item>
          <Descriptions.Item label="范围"><ScopeTag scope={selected.scope} /></Descriptions.Item>
          <Descriptions.Item label="Owner / Tenant">{selected.owner} / {selected.tenant_id}</Descriptions.Item>
          <Descriptions.Item label="Source Run">{selected.source_run_id ? <Text code>{selected.source_run_id}</Text> : '—'}</Descriptions.Item>
          <Descriptions.Item label="Confidence">{Math.round(selected.confidence * 100)}%</Descriptions.Item>
          <Descriptions.Item label="Created">{formatTime(selected.created_at)}</Descriptions.Item>
          <Descriptions.Item label="Updated">{formatTime(selected.updated_at)}</Descriptions.Item>
          <Descriptions.Item label="Expiration">{formatTime(selected.expires_at)}</Descriptions.Item>
          <Descriptions.Item label="ACL">{selected.acl.join(', ')}</Descriptions.Item>
          <Descriptions.Item label="状态">{selected.archived ? '已归档' : '活跃'}</Descriptions.Item>
        </Descriptions>
        <Alert className="drawer-alert" type="warning" showIcon message="演示数据边界" description="该记忆内容由 Mock Adapter 生成，可能包含合成金融上下文；不代表真实客户数据或真实记忆检索结果。" />
      </div>}
    </Drawer>

    <Modal title={editing ? '编辑记忆' : '创建记忆'} open={formOpen} onCancel={() => setFormOpen(false)} onOk={() => void submit()}
      okText="保存" cancelText="取消" width={620} destroyOnHidden>
      <Form form={form} layout="vertical">
        <Form.Item label="标题" name="title" rules={[{ required: true, message: '请输入标题' }]}><Input /></Form.Item>
        <Form.Item label="内容" name="content" rules={[{ required: true, message: '请输入内容' }]}><Input.TextArea rows={4} /></Form.Item>
        <Row gutter={12}>
          <Col span={12}><Form.Item label="类型" name="type"><Select options={['WORKING', 'EPISODIC', 'SEMANTIC', 'PROCEDURAL'].map((value) => ({ label: value, value }))} /></Form.Item></Col>
          <Col span={12}><Form.Item label="范围" name="scope"><Select options={[{ label: '个人级', value: 'user' }, { label: '团队级', value: 'team' }, { label: '组织级', value: 'organization' }]} /></Form.Item></Col>
        </Row>
        <Row gutter={12}>
          <Col span={12}><Form.Item label="Confidence (0-1)" name="confidence"><InputNumber min={0} max={1} step={0.05} style={{ width: '100%' }} /></Form.Item></Col>
          <Col span={12}><Form.Item label="Source Run ID" name="source_run_id" rules={[{ validator: (_, value) => (!value || UUID_PATTERN.test(String(value))) ? Promise.resolve() : Promise.reject(new Error('需为 UUID 格式')) }]}><Input placeholder="可选，UUID 格式" /></Form.Item></Col>
        </Row>
        <Row gutter={12}>
          <Col span={12}><Form.Item label="Expiration" name="expires_at"><Input placeholder="ISO 时间，可选" /></Form.Item></Col>
          <Col span={12}><Form.Item label="ACL（逗号分隔）" name="acl_text"><Input placeholder="user:analyst-01, team:compliance" /></Form.Item></Col>
        </Row>
      </Form>
    </Modal>
  </>;
}

function TitleSmall({ children }: { children: string }) {
  return <div className="detail-title-small">{children}</div>;
}
