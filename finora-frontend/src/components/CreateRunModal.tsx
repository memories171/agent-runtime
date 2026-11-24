import { Form, Input, Modal, Select, Alert, Spin, Switch } from 'antd';
import { api } from '../api';
import { useApp } from '../state/AppContext';
import { useAuth } from '../auth/AuthProvider';
import { useAsyncData } from '../hooks/useAsyncData';
import { skillLabel } from '../components/Ui';
import type { RunRecord } from '../types';

type CreateValues = { prompt: string; skill_ids: string[]; graph_enabled: boolean };

export function CreateRunModal({ open, onClose, onCreated }: {
  open: boolean;
  onClose: () => void;
  onCreated?: (run: RunRecord) => void;
}) {
  const [form] = Form.useForm<CreateValues>();
  const { identity, isMockMode, notify } = useApp();
  const auth = useAuth();
  const skills = useAsyncData(() => api.listSkills(), [open]);
  const canCreate = isMockMode || Boolean(auth.profile?.permissions?.includes('agent:execute'));
  const displayTenant = isMockMode ? identity.tenantId : (auth.currentTenant || '-');
  const displayUser = isMockMode ? identity.userId : (auth.profile?.user_id || '-');

  const submit = async () => {
    const values = await form.validateFields();
    if (!canCreate) {
      notify('error', '当前角色没有 agent:execute 权限，无法创建 Agent 任务');
      return;
    }
    try {
      const run = await api.createRun({
        prompt: values.prompt,
        skill_ids: values.skill_ids || [],
        graph: values.graph_enabled ? { enabled: true } : undefined,
      });
      notify('success', 'Agent 任务已提交');
      form.resetFields();
      onCreated?.(run);
      onClose();
    } catch (error) {
      notify('error', error instanceof Error ? error.message : '创建任务失败');
    }
  };

  return <Modal
    title="创建 Agent 任务"
    open={open}
    onCancel={onClose}
    onOk={() => void submit()}
    okText="提交任务"
    cancelText="取消"
    okButtonProps={{ disabled: !canCreate }}
    destroyOnHidden
  >
    <Spin spinning={skills.loading}>
      <Form form={form} layout="vertical" initialValues={{ skill_ids: [], graph_enabled: false }}>
        <Form.Item label="任务描述" name="prompt" rules={[{ required: true, message: '请描述希望 Agent 完成的任务' }]}>
          <Input.TextArea rows={5} placeholder="例如：对 ACC-DEMO-001 运行金融监测演示，解释规则命中交易并整理复核摘要。" />
        </Form.Item>
        <Form.Item label="关联技能" name="skill_ids">
          <Select mode="multiple" placeholder="选择一个或多个技能"
            options={(skills.data || []).map((skill) => ({
              label: skillLabel(skill.skill_id, skill.name, skill.display_name),
              value: skill.skill_id,
            }))} />
        </Form.Item>
        <Form.Item label="使用 Task Graph 规划" name="graph_enabled" extra="开启后由现有模型生成 DAG 提案，经后端校验后并行执行；关闭则沿用线性 Agent Loop。">
          <Switch checkedChildren="DAG" unCheckedChildren="线性" />
        </Form.Item>
      </Form>
    </Spin>
    {!canCreate && (
      <Alert type="warning" showIcon message="当前角色无创建权限"
        description="创建 Agent 任务需要 agent:execute 权限。请使用 Analyst 或 Tenant Admin 账号，或联系管理员为当前角色授权。" />
    )}
    <Alert type="info" showIcon message={isMockMode ? 'DEMO Identity · 本地演示身份' : '当前会话身份'}
      description={isMockMode
        ? `租户 ${displayTenant} · 用户 ${displayUser}。前端身份切换仅用于开发演示，不代表真实登录或权限认证。`
        : `租户 ${displayTenant} · 用户 ${displayUser}。权限由后端验证。`} />
  </Modal>;
}
