import { useCallback, useEffect, useRef, useState } from 'react';
import type { ChangeEvent } from 'react';
import { Button, Card, Empty, Input, Modal, Select, Space, Statistic, Table, Tag, Typography } from 'antd';
import { DeleteOutlined, EditOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons';
import { api } from '../api';
import { useApp } from '../state/AppContext';
import type { KnowledgeCitation, KnowledgeDocument } from '../types';

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

export function KnowledgePage() {
  const { notify, refreshToken, requestRefresh } = useApp();
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [stats, setStats] = useState({ documents: 0, chunks: 0 });
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [title, setTitle] = useState('');
  const [source, setSource] = useState('');
  const [contentType, setContentType] = useState('auto');
  const [content, setContent] = useState('');
  const [fileName, setFileName] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<KnowledgeCitation[]>([]);
  const [searching, setSearching] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [docs, kbStats] = await Promise.all([api.listKnowledgeDocuments(), api.knowledgeStats()]);
      setDocuments(docs);
      setStats(kbStats);
    } catch (error) {
      notify('error', error instanceof Error ? error.message : '加载知识库失败');
    } finally {
      setLoading(false);
    }
  }, [notify, refreshToken]);

  useEffect(() => { void load(); }, [load]);

  const onFileSelected = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > 2_000_000) { notify('error', '文件超过 2MB，请先拆分'); return; }
    try {
      const text = await file.text();
      setContent(text);
      setFileName(file.name);
      if (!title.trim()) setTitle(file.name.replace(/\.[^.]+$/, ''));
      notify('success', `已读取 ${file.name}，将按扩展名自动选择切分策略`);
    } catch (error) {
      notify('error', error instanceof Error ? error.message : '读取文件失败');
    }
    if (fileRef.current) fileRef.current.value = '';
  };

  const closeDocumentModal = () => {
    setCreateOpen(false);
    setEditOpen(false);
    setEditingId(null);
    setTitle(''); setSource(''); setContent(''); setFileName(''); setContentType('auto');
  };

  const openEdit = async (record: KnowledgeDocument) => {
    try {
      const detail = await api.getKnowledgeDocument(record.document_id);
      setEditingId(record.document_id);
      setTitle(detail.title);
      setSource(detail.source || '');
      setContentType(detail.content_type || 'auto');
      setContent(detail.raw_content || detail.chunks.map((chunk) => chunk.content).join('\n\n'));
      setFileName('');
      setEditOpen(true);
    } catch (error) {
      notify('error', error instanceof Error ? error.message : '加载文档原文失败');
    }
  };

  const saveDocument = async () => {
    if (!content.trim()) { notify('error', '请输入文档内容'); return; }
    try {
      const payload = {
        title: title.trim() || '未命名文档', content,
        source: source.trim() || fileName, content_type: contentType,
      };
      const result = editingId
        ? await api.updateKnowledgeDocument(editingId, payload)
        : await api.createKnowledgeDocument(payload);
      notify('success', `${editingId ? '已更新文档' : '已解析入库'}：${result.chunk_count} 个切分块`);
      closeDocumentModal();
      requestRefresh();
      await load();
    } catch (error) {
      notify('error', error instanceof Error ? error.message : (editingId ? '更新失败' : '入库失败'));
    }
  };

  const removeDocument = async (documentId: string) => {
    try {
      await api.deleteKnowledgeDocument(documentId);
      notify('success', '文档已删除');
      requestRefresh();
      await load();
    } catch (error) {
      notify('error', error instanceof Error ? error.message : '删除失败');
    }
  };

  const runSearch = async () => {
    if (!query.trim()) return;
    setSearching(true);
    try {
      setResults(await api.searchKnowledge(query.trim(), 5));
    } catch (error) {
      notify('error', error instanceof Error ? error.message : '检索失败');
    } finally {
      setSearching(false);
    }
  };

  const columns = [
    { title: '文档标题', dataIndex: 'title', ellipsis: true },
    { title: '类型', dataIndex: 'content_type', width: 90, render: (v: string) => <Tag>{v}</Tag> },
    { title: '切分块', dataIndex: 'chunk_count', width: 80 },
    { title: '字符数', dataIndex: 'char_count', width: 90 },
    { title: '来源', dataIndex: 'source', ellipsis: true, render: (v: string) => v || '—' },
    { title: '状态', dataIndex: 'status', width: 90, render: (v: string) => <Tag color="green">{v}</Tag> },
    {
      title: '操作', key: 'action', width: 110,
      render: (_: unknown, record: KnowledgeDocument) => (
        <Space size={0}>
          <Button type="text" size="small" icon={<EditOutlined />} onClick={() => void openEdit(record)} />
          <Button type="text" size="small" danger icon={<DeleteOutlined />} onClick={() => void removeDocument(record.document_id)} />
        </Space>
      ),
    },
  ];

  return (
    <div className="knowledge-page">
      <Card className="panel-card" bordered={false}>
        <Space size="large">
          <Statistic title="文档数" value={stats.documents} />
          <Statistic title="切分块数" value={stats.chunks} />
          <Text type="secondary">文档解析 → 清洗 → 语义切分 → TF-IDF 哈希向量化 → 余弦检索；AI 问答命中时自动标注引用溯源。</Text>
        </Space>
      </Card>

      <Card className="panel-card table-card" bordered={false}
        title={<div className="card-title"><span>知识库文档</span><Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => { closeDocumentModal(); setCreateOpen(true); }}>上传文档</Button></div>}>
        <Table rowKey="document_id" columns={columns} dataSource={documents} loading={loading} pagination={false} size="small"
          locale={{ emptyText: <Empty description="知识库为空，点击「上传文档」开始" /> }} />
      </Card>

      <Card className="panel-card table-card" bordered={false} title={<div className="card-title"><span>检索测试</span><Text type="secondary">验证切分与向量化效果</Text></div>}>
        <Space.Compact style={{ width: '100%' }}>
          <Input value={query} onChange={(event) => setQuery(event.target.value)} onPressEnter={() => void runSearch()} placeholder="输入问题，检索相关知识库片段" />
          <Button type="primary" icon={<SearchOutlined />} loading={searching} onClick={() => void runSearch()}>检索</Button>
        </Space.Compact>
        <div className="kb-results">
          {results.map((item, index) => (
            <div key={item.chunk_id || `${item.document_id}-${item.chunk_index}`} className="kb-result">
              <div className="kb-result-head">
                <Tag color="blue">[{index + 1}]</Tag>
                <b>{item.document_title}</b>
                <Text type="secondary">· {item.heading} · 块{item.chunk_index}</Text>
                <Tag color="green">相关度 {item.score.toFixed(4)}</Tag>
              </div>
              <Paragraph>{item.content}</Paragraph>
            </div>
          ))}
          {results.length === 0 && !searching && <Empty description="输入问题开始检索" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
        </div>
      </Card>

      <Modal open={createOpen || editOpen} title={editingId ? '编辑知识库文档' : '上传文档到知识库'} onCancel={closeDocumentModal} onOk={() => void saveDocument()} okText={editingId ? '保存并重新解析' : '解析入库'} width={720}>
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Space wrap>
            <input ref={fileRef} type="file"
              accept=".md,.markdown,.txt,.log,.html,.htm,.csv,.tsv,.json,.jsonl,.ndjson,.yaml,.yml"
              onChange={(event) => void onFileSelected(event)} style={{ display: 'none' }} />
            <Button icon={<PlusOutlined />} onClick={() => fileRef.current?.click()}>选择文件</Button>
            {fileName && <Text type="secondary">已选择：{fileName}</Text>}
          </Space>
          <Input placeholder="文档标题（留空则取首个标题）" value={title} onChange={(event) => setTitle(event.target.value)} />
          <Input placeholder="来源，如制度文件名 / URL" value={source} onChange={(event) => setSource(event.target.value)} />
          <Select value={contentType} onChange={setContentType} style={{ width: 220 }} options={[
            { label: '自动检测（按扩展名/内容）', value: 'auto' },
            { label: 'Markdown（按标题层级）', value: 'markdown' },
            { label: '纯文本（按段落）', value: 'text' },
            { label: 'HTML（按语义标签）', value: 'html' },
            { label: 'CSV/TSV（按行分组）', value: 'csv' },
            { label: 'JSON（按顶层键/条目）', value: 'json' },
            { label: 'JSONL（按记录）', value: 'jsonl' },
            { label: 'YAML（按配置块）', value: 'yaml' },
          ]} />
          <TextArea rows={14} placeholder="选择文件自动读取，或直接粘贴内容" value={content} onChange={(event) => setContent(event.target.value)} />
          <Text type="secondary">支持 md/txt/html/csv/tsv/json/jsonl/yaml。不同格式采用专属切分策略（标题层级 / 语义标签 / 行分组 / 配置块），统一清洗后生成中文 bigram TF-IDF 哈希向量。</Text>
        </Space>
      </Modal>
    </div>
  );
}
