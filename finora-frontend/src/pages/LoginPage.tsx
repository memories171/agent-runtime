import { useState } from 'react';
import { Alert, Button, Card, Space, Typography } from 'antd';
import { SafetyCertificateOutlined } from '@ant-design/icons';
import { isOidcConfigured, signIn } from '../auth/oidc';

const { Title, Paragraph, Text } = Typography;

export function LoginPage() {
  const [error, setError] = useState('');

  return (
    <div className="login-page">
      <Card className="login-card" bordered={false}>
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          <div className="login-brand"><SafetyCertificateOutlined /><b>Finora Agent Runtime</b></div>
          <div>
            <Title level={3}>企业级身份认证</Title>
            <Paragraph type="secondary">
              API 模式使用 OIDC Authorization Code + PKCE 登录。浏览器不会保存长期密钥，Token 仅保存在会话存储并在内存中用于请求鉴权。
            </Paragraph>
          </div>
          {error && <Alert type="error" showIcon message="登录跳转失败" description={error} />}
          {isOidcConfigured() ? (
            <Button
              type="primary"
              size="large"
              block
              onClick={() => void signIn().catch((cause: unknown) => {
                setError(cause instanceof Error ? cause.message : '未知错误');
              })}
            >
              使用企业账号登录
            </Button>
          ) : (
            <Text type="warning">OIDC 未配置：请设置 VITE_OIDC_AUTHORITY 与 VITE_OIDC_CLIENT_ID。</Text>
          )}
        </Space>
      </Card>
    </div>
  );
}
