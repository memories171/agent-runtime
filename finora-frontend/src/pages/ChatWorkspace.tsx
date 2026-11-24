import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Drawer, Input, List, Space, Switch, Tag, Typography } from 'antd';
import { BulbOutlined, DeleteOutlined, PauseCircleOutlined, PlusOutlined, RobotOutlined, SendOutlined, ToolOutlined, UserOutlined } from '@ant-design/icons';
import { api } from '../api';
import { useApp } from '../state/AppContext';
import type { ChatConversation, ChatMessage, KnowledgeCitation, KnowledgeDocumentDetail } from '../types';

const { Text, Paragraph, Link } = Typography;
const { TextArea } = Input;

function readableContent(text: string): string {
  if (!text || !text.includes('DSML')) return text;
  return text.split('\n').filter((line) => !line.includes('DSML')).join('\n').trim();
}

function isHttpSource(source?: string): boolean {
  return /^https?:\/\//i.test(source || '');
}

type StreamingMessage = {
  id: string;
  reasoning: string;
  content: string;
  status: string;
  tools: Array<{ phase: string; tool: string; result?: unknown }>;
};

export function ChatWorkspace({ onOpenMemory, onOpenCheckpoint }: {
  onOpenMemory: () => void;
  onOpenCheckpoint: () => void;
}) {
  const { notify } = useApp();
  const [conversations, setConversations] = useState<ChatConversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [useMemory, setUseMemory] = useState(true);
  const [deepThink, setDeepThink] = useState(false);
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(false);
  const [streaming, setStreaming] = useState<StreamingMessage | null>(null);
  const [lastUsage, setLastUsage] = useState<{ prompt_tokens?: number; completion_tokens?: number; memory_hits?: number } | null>(null);
  const [activeCitation, setActiveCitation] = useState<KnowledgeCitation | null>(null);
  const [citationDetail, setCitationDetail] = useState<KnowledgeDocumentDetail | null>(null);
  const [citationLoading, setCitationLoading] = useState(false);
  const [activeChunkIndex, setActiveChunkIndex] = useState<number | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const streamingRef = useRef<StreamingMessage | null>(null);
  const stoppedRef = useRef(false);
  const activeIdRef = useRef<string | null>(null);
  const streamingConversationRef = useRef<string | null>(null);
  const citationRequestRef = useRef(0);

  const closeCitationDetail = () => {
    citationRequestRef.current += 1;
    setActiveCitation(null);
    setCitationDetail(null);
    setCitationLoading(false);
    setActiveChunkIndex(null);
  };

  const openCitation = async (citation: KnowledgeCitation) => {
    const requestId = ++citationRequestRef.current;
    const snapshot: KnowledgeDocumentDetail = {
      document_id: citation.document_id,
      title: citation.document_title,
      source: citation.source || '',
      content_type: 'knowledge',
      char_count: citation.content?.length || 0,
      chunk_count: 1,
      status: 'CITATION_SNAPSHOT',
      chunks: [{
        chunk_id: citation.chunk_id || `${citation.document_id}:${citation.chunk_index}`,
        chunk_index: citation.chunk_index,
        heading: citation.heading,
        content: citation.content,
        char_count: citation.content?.length || 0,
      }],
    };
    setActiveCitation(citation);
    setActiveChunkIndex(citation.chunk_index);
    setCitationDetail(snapshot);
    setCitationLoading(true);
    try {
      const detail = await api.getKnowledgeDocument(citation.document_id);
      if (requestId !== citationRequestRef.current) return;
      const currentChunk = citation.chunk_id
        ? detail.chunks.find((chunk) => chunk.chunk_id === citation.chunk_id)
        : detail.chunks.find((chunk) => chunk.chunk_index === citation.chunk_index);
      const stillMatches = Boolean(currentChunk && currentChunk.content === citation.content);
      setCitationDetail(stillMatches ? detail : snapshot);
    } catch {
      if (requestId === citationRequestRef.current) {
        setCitationDetail(snapshot);
      }
    } finally {
      if (requestId === citationRequestRef.current) setCitationLoading(false);
    }
  };

  const updateStreaming = (updater: (prev: StreamingMessage | null) => StreamingMessage | null) => {
    setStreaming((prev) => {
      const next = updater(prev);
      streamingRef.current = next;
      return next;
    });
  };

  const selectConversation = (conversationId: string | null) => {
    if (conversationId === activeIdRef.current) return;
    closeCitationDetail();
    if (abortRef.current) abortRef.current.abort();
    abortRef.current = null;
    stoppedRef.current = true;
    activeIdRef.current = conversationId;
    streamingConversationRef.current = null;
    updateStreaming(() => null);
    setSending(false);
    setInput('');
    setLastUsage(null);
    setMessages([]);
    setActiveId(conversationId);
  };

  const loadConversations = useCallback(async () => {
    setLoading(true);
    try {
      const list = await api.listConversations();
      setConversations(list);
      const nextId = activeIdRef.current && list.some((x) => x.conversation_id === activeIdRef.current)
        ? activeIdRef.current
        : (list[0]?.conversation_id || null);
      activeIdRef.current = nextId;
      setActiveId(nextId);
    } catch (error) {
      notify('error', error instanceof Error ? error.message : '加载会话失败');
    } finally {
      setLoading(false);
    }
  }, [notify]);

  const loadMessages = useCallback(async (conversationId: string) => {
    try {
      const list = await api.listChatMessages(conversationId);
      if (activeIdRef.current !== conversationId) return;
      setMessages(list);
    } catch (error) {
      if (activeIdRef.current !== conversationId) return;
      notify('error', error instanceof Error ? error.message : '加载消息失败');
    }
  }, [notify]);

  useEffect(() => { void loadConversations(); }, [loadConversations]);
  useEffect(() => { if (activeId) void loadMessages(activeId); }, [activeId, loadMessages]);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, streaming]);

  const createConversation = async () => {
    try {
      const conv = await api.createConversation('新对话');
      setConversations((prev) => [conv, ...prev]);
      selectConversation(conv.conversation_id);
      notify('success', '已新建对话');
    } catch (error) {
      notify('error', error instanceof Error ? error.message : '新建对话失败');
    }
  };

  const removeConversation = async (conversationId: string) => {
    try {
      await api.deleteConversation(conversationId);
      const next = conversations.filter((x) => x.conversation_id !== conversationId);
      setConversations(next);
      if (activeId === conversationId) {
        selectConversation(next[0]?.conversation_id || null);
      }
      notify('success', '对话已删除');
    } catch (error) {
      notify('error', error instanceof Error ? error.message : '删除对话失败');
    }
  };

  const stopGeneration = async () => {
    const partial = streamingRef.current;
    const conversationId = activeId;
    stoppedRef.current = true;
    // 双保险：先让后端置取消标志（停止上游模型计费），再断开前端 SSE 连接。
    if (conversationId) {
      try { await api.cancelChatStream(conversationId); } catch { /* abort 仍会断开连接 */ }
    }
    abortRef.current?.abort();
    updateStreaming(() => null);
    setSending(false);
    abortRef.current = null;
    if (partial && conversationId && (partial.content || partial.reasoning)) {
      try {
        const saved = await api.saveStoppedMessage(conversationId, {
          content: partial.content || '（停止时未产生答案）',
          reasoning: partial.reasoning,
          tool_calls: partial.tools
            .filter((tool) => tool.phase === 'result')
            .map((tool) => ({ tool: tool.tool, status: 'ok', result: tool.result })),
        });
        setMessages((prev) => [...prev, saved]);
        notify('info', '已停止生成，输出的部分内容已保存');
      } catch (error) {
        notify('error', error instanceof Error ? error.message : '保存停止内容失败');
      }
    } else {
      notify('info', '已停止生成');
    }
  };

  const send = async () => {
    const content = input.trim();
    if (!content || !activeId || sending) return;
    const conversationId = activeId;
    setInput('');
    setSending(true);
    stoppedRef.current = false;
    streamingConversationRef.current = conversationId;
    const isCurrent = () => activeIdRef.current === conversationId && !stoppedRef.current;
    setMessages((prev) => [...prev, {
      message_id: `local-${Date.now()}`,
      conversation_id: conversationId,
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    }]);
    const controller = new AbortController();
    abortRef.current = controller;
    updateStreaming(() => ({ id: `stream-${Date.now()}`, reasoning: '', content: '', status: '', tools: [] }));
    try {
      await api.streamChatMessage(
        conversationId,
        content,
        {
          onStatus: (phase, message) => { if (!isCurrent()) return; updateStreaming((prev) => prev ? { ...prev, status: phase === 'summarizing' ? message : '' } : prev); },
          onReasoning: (delta) => { if (!isCurrent()) return; updateStreaming((prev) => prev ? { ...prev, reasoning: prev.reasoning + delta } : prev); },
          onDelta: (delta) => { if (!isCurrent()) return; updateStreaming((prev) => prev ? { ...prev, content: prev.content + delta } : prev); },
          onTool: (event) => { if (!isCurrent()) return; updateStreaming((prev) => prev ? {
            ...prev,
            tools: [...prev.tools, event],
          } : prev); },
          onDone: (result) => {
            if (!isCurrent()) return;
            setMessages((prev) => [...prev, {
              message_id: result.message_id,
              conversation_id: conversationId,
              role: 'assistant',
              content: result.content,
              tool_calls: result.tool_calls || [],
              created_at: new Date().toISOString(),
              ...(result.reasoning ? { reasoning: result.reasoning } : {}),
              ...(result.citations ? { citations: result.citations } : {}),
            } as ChatMessage]);
            setLastUsage({
              prompt_tokens: result.usage?.prompt_tokens,
              completion_tokens: result.usage?.completion_tokens,
              memory_hits: result.memory_hits,
            });
            if (result.memory_saved) notify('success', `已记住：${result.memory_saved.title}`);
            updateStreaming(() => null);
            streamingConversationRef.current = null;
          },
          onError: (message) => {
            if (!isCurrent()) return;
            notify('error', message);
            updateStreaming(() => null);
            streamingConversationRef.current = null;
          },
        },
        useMemory,
        deepThink,
        controller.signal,
      );
      await loadConversations();
    } catch (error) {
      if (!stoppedRef.current) {
        notify('error', error instanceof Error ? error.message : '发送失败');
        updateStreaming(() => null);
      }
    } finally {
      if (activeIdRef.current === conversationId) {
        abortRef.current = null;
        setSending(false);
        streamingConversationRef.current = null;
      }
    }
  };

  return (
    <div className="chat-workspace">
      <div className="chat-sidebar">
        <div className="chat-sidebar-head">
          <Button type="primary" icon={<PlusOutlined />} block onClick={() => void createConversation()}>新建对话</Button>
        </div>
        <List
          loading={loading}
          dataSource={conversations}
          locale={{ emptyText: <Text type="secondary">还没有对话，点上方新建</Text> }}
          renderItem={(conv) => (
            <List.Item
              className={`chat-conv-item ${conv.conversation_id === activeId ? 'active' : ''}`}
              onClick={() => selectConversation(conv.conversation_id)}
              actions={[
                <Button key="del" type="text" size="small" danger icon={<DeleteOutlined />}
                  onClick={(event) => { event.stopPropagation(); void removeConversation(conv.conversation_id); }} />,
              ]}
            >
              <List.Item.Meta title={conv.title} description={conv.updated_at ? new Date(conv.updated_at).toLocaleString('zh-CN', { hour12: false }) : ''} />
            </List.Item>
          )}
        />
      </div>
      <div className="chat-main">
        <div className="chat-toolbar">
          <Space wrap>
            <Text type="secondary">长期记忆</Text>
            <Switch checked={useMemory} onChange={setUseMemory} size="small" />
            <Text type="secondary">深度思考</Text>
            <Switch checked={deepThink} onChange={setDeepThink} size="small" />
            <Text type="secondary" className="chat-hint">{deepThink ? 'R1 思考链（不支持工具）' : '快速模式（可调用工具）'}</Text>
            {lastUsage && <Tag color="blue">tokens {lastUsage.prompt_tokens ?? '?'} + {lastUsage.completion_tokens ?? '?'} · 记忆命中 {lastUsage.memory_hits ?? 0}</Tag>}
          </Space>
          <Space>
            <Button size="small" onClick={onOpenMemory}>查看长期记忆</Button>
            <Button size="small" onClick={onOpenCheckpoint}>查看 Checkpoint</Button>
          </Space>
        </div>
        <div className="chat-messages">
          {messages.length === 0 && !streaming && (
            <div className="chat-empty">
              <RobotOutlined style={{ fontSize: 40, color: '#167d72' }} />
              <Paragraph type="secondary">输入问题开始对话。开启深度思考可查看模型思考链；快速模式可调用行情等工具。</Paragraph>
            </div>
          )}
          {messages.map((msg) => (
            <div key={msg.message_id} className={`chat-bubble-row ${msg.role}`}>
              <div className="chat-avatar">{msg.role === 'user' ? <UserOutlined /> : <RobotOutlined />}</div>
              <div className="chat-bubble">
                <div className="chat-bubble-role">{msg.role === 'user' ? '我' : 'Finora'}</div>
                {msg.role === 'assistant' && (msg as ChatMessage & { reasoning?: string }).reasoning && (
                  <div className="chat-reasoning">
                    <div className="chat-reasoning-title"><BulbOutlined /> 思考过程</div>
                    <Paragraph>{(msg as ChatMessage & { reasoning?: string }).reasoning}</Paragraph>
                  </div>
                )}
                {msg.role === 'assistant' && (msg.tool_calls || []).length > 0 && (
                  <div className="chat-tools">
                    {msg.tool_calls!.map((call, index) => (
                      <Tag key={index} icon={<ToolOutlined />} color={call.status === 'ok' ? 'green' : 'red'}>{String(call.tool)}</Tag>
                    ))}
                  </div>
                )}
                <Paragraph>{readableContent(msg.content)}</Paragraph>
                {msg.role === 'assistant' && (() => {
                  const citations = msg.citations || (msg.tool_calls || [])
                    .filter((call) => call.kind === 'citation')
                    .map((call) => call as unknown as KnowledgeCitation);
                  return citations.length > 0 ? (
                    <div className="chat-citations">
                      <div className="chat-citations-title">引用溯源 · 知识库</div>
                      {citations.map((item) => (
                        <div
                          key={`${item.document_id}-${item.chunk_index}`}
                          className="chat-citation"
                          role="button"
                          tabIndex={0}
                          title="点击查看原文片段"
                          style={{ cursor: 'pointer' }}
                          onClick={() => void openCitation(item)}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' || event.key === ' ') {
                              event.preventDefault();
                              void openCitation(item);
                            }
                          }}
                        >
                          <Tag color="blue">[{item.index}]</Tag>
                          <Text strong>{item.document_title}</Text>
                          <Text type="secondary"> · {item.heading} · 块{item.chunk_index} · 相关度 {Number(item.score).toFixed(3)}</Text>
                          <Text type="secondary"> · 查看原文</Text>
                          <Paragraph ellipsis={{ rows: 3 }}>{item.content}</Paragraph>
                        </div>
                      ))}
                    </div>
                  ) : null;
                })()}
              </div>
            </div>
          ))}
          {streaming && (
            <div className="chat-bubble-row assistant">
              <div className="chat-avatar"><RobotOutlined /></div>
              <div className="chat-bubble">
                <div className="chat-bubble-role">Finora</div>
                {streaming.reasoning && (
                  <div className="chat-reasoning">
                    <div className="chat-reasoning-title"><BulbOutlined /> 思考过程</div>
                    <Paragraph>{streaming.reasoning}</Paragraph>
                  </div>
                )}
                {streaming.tools.length > 0 && (
                  <div className="chat-tools">
                    {streaming.tools.map((tool, index) => (
                      <Tag key={index} icon={<ToolOutlined />} color={tool.phase === 'result' ? 'green' : 'blue'}>
                        {tool.phase === 'start' ? `调用 ${tool.tool}` : `${tool.tool} 完成`}
                      </Tag>
                    ))}
                  </div>
                )}
                {streaming.content ? <Paragraph>{readableContent(streaming.content)}<span className="chat-cursor">▌</span></Paragraph> : (
                  streaming.status ? <Text type="secondary">{streaming.status}</Text> : <span className="chat-cursor">▌</span>
                )}
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>
        <div className="chat-composer">
          <TextArea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onPressEnter={(event) => { if (!event.shiftKey) { event.preventDefault(); void send(); } }}
            autoSize={{ minRows: 2, maxRows: 6 }}
            placeholder="输入问题，Enter 发送，Shift+Enter 换行"
            disabled={!activeId || sending}
          />
          {sending ? (
            <Button danger icon={<PauseCircleOutlined />} onClick={() => void stopGeneration()}>停止生成</Button>
          ) : (
            <Button type="primary" icon={<SendOutlined />} disabled={!input.trim() || !activeId} onClick={() => void send()}>发送</Button>
          )}
        </div>
      </div>
      <Drawer
        width={720}
        title={`原文片段 · ${activeCitation?.document_title || citationDetail?.title || ''}`}
        open={Boolean(activeCitation)}
        onClose={closeCitationDetail}
      >
        {citationDetail ? (
          <Space direction="vertical" size={10} style={{ width: '100%' }}>
            {citationLoading ? <Text type="secondary">正在补全完整文档，当前先显示引用原文快照...</Text> : null}
            <div>
              <Text strong>文档：</Text>
              <Text>{citationDetail.title}</Text>
            </div>
            <div>
              <Text strong>来源：</Text>
              {isHttpSource(citationDetail.source)
                ? <Link href={citationDetail.source} target="_blank" rel="noreferrer">打开来源</Link>
                : <Text type="secondary">{citationDetail.source || '未记录来源'}</Text>}
            </div>
            <div>
              <Text strong>类型：</Text>
              <Text>{citationDetail.content_type}</Text>
              <Text type="secondary"> · 共 {citationDetail.chunk_count} 块 · {citationDetail.char_count} 字</Text>
              {citationDetail.status === 'CITATION_SNAPSHOT' ? <Text type="secondary"> · 引用快照</Text> : null}
              {activeCitation ? <Text type="secondary"> · 当前引用相关度 {Number(activeCitation.score).toFixed(3)}</Text> : null}
            </div>
            {citationDetail.chunks.length === 0
              ? <Text type="secondary">该文档暂无可展示的原文切片。</Text>
              : citationDetail.chunks.map((chunk) => {
                const isActive = activeChunkIndex === chunk.chunk_index;
                return (
                  <div
                    key={chunk.chunk_id || chunk.chunk_index}
                    style={{
                      border: `1px solid ${isActive ? '#167d72' : '#f0f0f0'}`,
                      borderRadius: 8,
                      padding: 12,
                      background: isActive ? '#e6fffb' : '#fff',
                    }}
                  >
                    <Space wrap>
                      <Tag color={isActive ? 'green' : 'default'}>块 {chunk.chunk_index}</Tag>
                      {chunk.heading ? <Text strong>{chunk.heading}</Text> : null}
                      {isActive ? <Tag color="blue">当前引用</Tag> : null}
                    </Space>
                    <Paragraph style={{ whiteSpace: 'pre-wrap', marginTop: 8, marginBottom: 0 }}>{chunk.content}</Paragraph>
                  </div>
                );
              })}
          </Space>
        ) : <Text type="secondary">原文片段不可用。</Text>}
      </Drawer>
    </div>
  );
}
