import type { ReactNode } from 'react';
import { Button, Card, Space, Spin, Typography } from 'antd';
import { useAuth } from '../auth/AuthProvider';
import { LoginPage } from '../pages/LoginPage';

const { Paragraph } = Typography;

export function AuthGate({ children }: { children: ReactNode }) {
  const { ready, isApiMode, isAuthenticated, authError, signOut } = useAuth();

  if (!isApiMode) return <>{children}</>;
  if (!ready) return <div className="auth-loading"><Spin size="large" /></div>;

  if (authError) {
    return (
      <div className="auth-loading">
        <Card style={{ maxWidth: 520 }} title="无法进入工作台">
          <Space direction="vertical">
            <Paragraph type="secondary">{authError}</Paragraph>
            <Paragraph type="warning">
              这通常表示该账号在 Keycloak 或 Finora 数据库中未配置完整：需要 tenant_id、organization_id 属性，以及对应的租户成员关系和角色。
            </Paragraph>
            <Button type="primary" onClick={() => void signOut()}>重新登录</Button>
          </Space>
        </Card>
      </div>
    );
  }

  if (!isAuthenticated) return <LoginPage />;
  return <>{children}</>;
}
