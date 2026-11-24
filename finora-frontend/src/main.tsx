import ReactDOM from 'react-dom/client';
import { ConfigProvider, App as AntApp } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import App from './App';
import { AuthProvider } from './auth/AuthProvider';
import { AuthGate } from './components/AuthGate';
import { ErrorBoundary } from './components/ErrorBoundary';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <ConfigProvider
    locale={zhCN}
    theme={{
      token: {
        colorPrimary: '#167d72',
        colorInfo: '#167d72',
        colorSuccess: '#26936b',
        colorWarning: '#d99a32',
        colorError: '#d65d58',
        borderRadius: 10,
        fontFamily: '"DM Sans", "Noto Sans SC", sans-serif',
        colorText: '#243a3d',
        colorTextSecondary: '#7d8b8d',
        colorBgLayout: '#f5f7f6',
      },
      components: {
        Layout: { siderBg: '#102c31', triggerBg: '#102c31' },
        Menu: { darkItemBg: '#102c31', darkSubMenuItemBg: '#102c31', darkItemSelectedBg: '#1c4a49' },
        Table: { headerBg: '#f7f9f8' },
      },
    }}
  >
    <AntApp>
      <AuthProvider>
        <ErrorBoundary>
          <AuthGate>
            <App />
          </AuthGate>
        </ErrorBoundary>
      </AuthProvider>
    </AntApp>
  </ConfigProvider>,
);
