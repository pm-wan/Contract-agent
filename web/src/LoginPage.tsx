import { useEffect, useState } from "react";
import { Alert, Button, Card, Form, Input, Typography } from "antd";
import { useNavigate } from "react-router-dom";
import { ApiError, login, type AuthProfile } from "./api";
import { BrandMark } from "./BrandMark";

interface LoginValues {
  username: string;
  password: string;
}

function loginErrorMessage(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 401) return "账号或密码不正确。";
    if (error.status === 0 || error.status >= 500) return "登录服务暂不可用，请检查服务状态后重试。";
  }
  return "登录失败，请稍后重试。";
}

export function LoginPage({ onAuthenticated }: { onAuthenticated: (profile: AuthProfile) => void }) {
  const navigate = useNavigate();
  const [form] = Form.useForm<LoginValues>();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => { document.title = "账号登录 · 合同审计工作台"; }, []);

  const submit = async (values: LoginValues) => {
    setSubmitting(true);
    setError(null);
    try {
      const profile = await login(values.username, values.password);
      onAuthenticated(profile);
      navigate("/", { replace: true });
    } catch (reason) {
      setError(loginErrorMessage(reason));
    } finally {
      setSubmitting(false);
    }
  };

  return <main className="auth-page">
    <section className="auth-intro">
      <div className="auth-brand"><BrandMark size="auth" /><span>合同审计工作台</span></div>
      <Typography.Title level={1}>欢迎回来</Typography.Title>
      <Typography.Paragraph>请使用管理员为你创建的系统账号登录。</Typography.Paragraph>
      <div className="auth-intro-note">
        <span className="auth-note-line" />
        <span>合同审查 · 主体管理 · 证据追溯</span>
      </div>
    </section>
    <Card className="auth-card" bordered={false}>
      <div className="auth-card-heading">
        <Typography.Title level={3}>账号登录</Typography.Title>
        <Typography.Text type="secondary">账号和初始密码由系统管理员提供。</Typography.Text>
      </div>
      {error && <Alert className="auth-error" type="error" showIcon message={error} />}
      <Form<LoginValues>
        form={form}
        layout="vertical"
        requiredMark={false}
        onFinish={(values) => void submit(values)}
      >
        <Form.Item label="账号" name="username" rules={[{ required: true, min: 3, message: "请输入至少 3 个字符的账号。" }]}>
          <Input autoComplete="username" placeholder="输入账号" size="large" />
        </Form.Item>
        <Form.Item label="密码" name="password" rules={[{ required: true, message: "请输入密码。" }]}>
          <Input.Password autoComplete="current-password" placeholder="输入密码" size="large" />
        </Form.Item>
        <Button className="auth-submit" type="primary" htmlType="submit" size="large" loading={submitting}>
          登录工作台
        </Button>
      </Form>
    </Card>
  </main>;
}
