import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { Button, Result, Space, Typography } from 'antd';

const { Paragraph, Text } = Typography;

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Finora UI render error', error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="auth-loading">
        <Result
          status="error"
          title="页面加载失败"
          subTitle="页面渲染时发生错误，已将错误信息输出到浏览器控制台。你可以刷新页面或返回工作台首页。"
          extra={
            <Space>
              <Button type="primary" onClick={() => window.location.reload()}>刷新页面</Button>
              <Button onClick={() => { window.location.href = '/'; }}>返回首页</Button>
            </Space>
          }
        >
          <Paragraph type="secondary">
            <Text code>{this.state.error.message}</Text>
          </Paragraph>
        </Result>
      </div>
    );
  }
}
