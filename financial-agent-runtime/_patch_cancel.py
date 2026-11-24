from pathlib import Path

p = Path("src/agent_runtime/chat_api.py")
s = p.read_text(encoding="utf-8")

# 1) 引入 asyncio 与取消标志字典
s = s.replace(
    "import json\nfrom uuid import uuid4\n",
    "import asyncio\nimport json\nfrom uuid import uuid4\n"
)
s = s.replace(
    'router = APIRouter()\n',
    'router = APIRouter()\n\n# 进程内流式取消标志：conversation_id -> Event。\n# 前端点\"停止生成\"时先调用 cancel 端点置位，流式循环在下一个检查点退出，\n# 关闭 httpx 上游连接，DeepSeek 随即停止生成，不再继续计费。\n_active_cancels: dict[str, asyncio.Event] = {}\n'
)

# 2) 增加 cancel 端点（放在 stopped 端点前）
marker = '@router.post("/chat/conversations/{conversation_id}/messages/stopped", status_code=201)'
cancel_endpoint = '''@router.post("/chat/conversations/{conversation_id}/cancel")
async def cancel_chat_stream(
    conversation_id: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    """显式取消该会话正在进行的流式生成。前端 stop 按钮调用，避免依赖代理层断连传播。"""
    conv = await _get_owned_conversation(session, conversation_id, principal)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    event = _active_cancels.get(str(conv.conversation_id))
    if event is not None:
        event.set()
        return {"cancelled": True, "active": True}
    return {"cancelled": False, "active": False}


'''
if marker in s and "def cancel_chat_stream" not in s:
    s = s.replace(marker, cancel_endpoint + marker)
    print("cancel endpoint added")

# 3) 流式端点注册取消事件
s = s.replace(
    '    model_name = "deepseek-reasoner" if body.deep_think else settings.model_name\n'
    '    tool_specs = [] if body.deep_think else _tool_specs(principal)\n'
    '    total_usage = {"prompt_tokens": 0, "completion_tokens": 0}\n',
    '    model_name = "deepseek-reasoner" if body.deep_think else settings.model_name\n'
    '    tool_specs = [] if body.deep_think else _tool_specs(principal)\n'
    '    total_usage = {"prompt_tokens": 0, "completion_tokens": 0}\n'
    '    cancel_event = asyncio.Event()\n'
    '    _active_cancels[conv_id] = cancel_event\n'
)
# 4) generator 内每轮检查取消，并在 finally 移除标志
s = s.replace(
    '        tool_log: list[dict] = []\n        try:\n            # 不发 connecting 状态',
    '        tool_log: list[dict] = []\n        stopped_by_user = False\n        try:\n            # 不发 connecting 状态'
)
s = s.replace(
    '            for round_index in range(MAX_TOOL_ROUNDS):\n                payload = {',
    '            for round_index in range(MAX_TOOL_ROUNDS):\n'
    '                if cancel_event.is_set():\n'
    '                    stopped_by_user = True\n'
    '                    break\n'
    '                payload = {'
)
s = s.replace(
    '                        async for line in response.aiter_lines():\n'
    '                            line = line.strip()\n',
    '                        async for line in response.aiter_lines():\n'
    '                            if cancel_event.is_set():\n'
    '                                stopped_by_user = True\n'
    '                                break\n'
    '                            line = line.strip()\n'
)
# 5) 在答案落库前，若被取消则落库并发送 stopped 事件后返回
s = s.replace(
    '            answer = "".join(answer_parts) or "（模型未返回内容）"\n'
    '            from agent_runtime.db import session_for_tenant\n',
    '            partial_answer = "".join(answer_parts)\n'
    '            if stopped_by_user:\n'
    '                answer = "〔已停止〕 " + (partial_answer or "（停止时未产生答案）")\n'
    '                if reasoning_parts:\n'
    '                    answer = "〔已停止〕 [思考过程已省略]\\n\\n" + partial_answer\n'
    '            else:\n'
    '                answer = partial_answer or "（模型未返回内容）"\n'
    '            from agent_runtime.db import session_for_tenant\n'
)
s = s.replace(
    '        except Exception as exc:\n'
    '            yield _sse("error", {"message": f"{type(exc).__name__}: {str(exc)[:300]}"})\n\n'
    '    return StreamingResponse(\n',
    '        except Exception as exc:\n'
    '            yield _sse("error", {"message": f"{type(exc).__name__}: {str(exc)[:300]}"})\n'
    '        finally:\n'
    '            _active_cancels.pop(conv_id, None)\n\n'
    '    return StreamingResponse(\n'
)

if "cancel_event = asyncio.Event()" not in s:
    print("WARNING: cancel event registration not applied")

p.write_text(s, encoding="utf-8")
print("done")
