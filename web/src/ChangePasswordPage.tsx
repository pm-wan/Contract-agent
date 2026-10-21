import { useState } from "react";
import { Alert, Button, Card, Form, Input, Typography } from "antd";
import { useNavigate } from "react-router-dom";
import { ApiError, changePassword, type AuthProfile } from "./api";
import { BrandMark } from "./BrandMark";

interface ChangePasswordPageProps {
  onAuthenticated: (profile: AuthProfile) => void;
}

interface PasswordFormValues {
  current_password: string;
  new_password: string;
  confirmation: string;
}

export function ChangePasswordPage({ onAuthenticated }: ChangePasswordPageProps) {
  const navigate = useNavigate();
  const [form] = Form.useForm<PasswordFormValues>();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (values: PasswordFormValues) => {
    setError(null);
    setSubmitting(true);
    try {
      const profile = await changePassword(values.current_password, values.new_password);
      onAuthenticated(profile);
      navigate("/", { replace: true });
    } catch (reason) {
      setError(reason instanceof ApiError && reason.status === 401
        ? "当前密码不正确，请重新输入。"
        : "密码修改失败，请稍后重试。必须设置至少 12 个字符的新密码。");
    } finally {
      setSubmitting(false);
    }
  };

  return <main className="auth-page">
    <section className="auth-intro">
      <div className="auth-brand"><BrandMark size="auth" /><span>合同审计工作台</span></div>
      <Typography.Title level={1}>更新登录密码</Typography.Title>
      <Typography.Paragraph>管理员为你的账号设置了初始密码。首次登录后，请先修改密码再使用工作台。</Typography.Paragraph>
    </section>
    <Card className="auth-card" bordered={false}>
      <div className="auth-card-heading">
        <Typography.Title level={3}>修改密码</Typography.Title>
        <Typography.Text type="secondary">新密码至少 12 个字符。</Typography.Text>
      </div>
      {error && <Alert className="auth-error" type="error" showIcon message={error} />}
      <Form form={form} layout="vertical" requiredMark={false} onFinish={submit}>
        <Form.Item label="当前密码" name="current_password" rules={[{ required: true, message: "请输入当前密码" }]}>
          <Input.Password autoComplete="current-password" size="large" />
        </Form.Item>
        <Form.Item label="新密码" name="new_password" rules={[{ required: true, min: 12, message: "新密码至少 12 个字符" }]}>
          <Input.Password autoComplete="new-password" size="large" />
        </Form.Item>
        <Form.Item
          label="确认新密码"
          name="confirmation"
          dependencies={["new_password"]}
          rules={[
            { required: true, message: "请再次输入新密码" },
            ({ getFieldValue }) => ({
              validator(_, value) {
                if (!value || getFieldValue("new_password") === value) return Promise.resolve();
                return Promise.reject(new Error("两次输入的密码不一致"));
              },
            }),
          ]}
        >
          <Input.Password autoComplete="new-password" size="large" />
        </Form.Item>
        <Button className="auth-submit" type="primary" htmlType="submit" size="large" loading={submitting}>保存新密码</Button>
      </Form>
    </Card>
  </main>;
}
