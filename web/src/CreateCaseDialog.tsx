import { useEffect, useState } from "react";
import { Alert, Button, Form, Input, Modal, Progress, Typography } from "antd";
import { ApiError, createCase, uploadCaseDocument } from "./api";
import { createUuid } from "./browserUuid";
import { contractFileError } from "./contract-file";

const { Paragraph } = Typography;

type CreateCaseDialogProps = {
  open: boolean;
  onClose: () => void;
  onCreated: (caseId: string) => void;
  uploadFirst?: boolean;
};

export function CreateCaseDialog({ open, onClose, onCreated, uploadFirst = false }: CreateCaseDialogProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [request, setRequest] = useState<{ title: string; key: string } | null>(null);
  const [form] = Form.useForm<{ title: string }>();
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [caseId, setCaseId] = useState<string | null>(null);
  const [uploadRequest, setUploadRequest] = useState<{ file: File; key: string } | null>(null);
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    if (open) setError(null);
  }, [open]);

  const submit = async (values: { title: string }) => {
    if (busy) return;
    if (uploadFirst && (!file || contractFileError(file))) {
      setFileError(file ? contractFileError(file)! : "请先选择一份主合同。");
      return;
    }
    const currentRequest = request ?? { title: values.title.trim(), key: createUuid() };
    let targetCaseId = caseId;
    setRequest(currentRequest);
    setBusy(true);
    setError(null);
    try {
      targetCaseId ??= (await createCase(currentRequest.title, currentRequest.key)).case_id;
      setCaseId(targetCaseId);
      if (uploadFirst && file) {
        const upload = uploadRequest ?? { file, key: createUuid() };
        setUploadRequest(upload);
        await uploadCaseDocument(targetCaseId, upload.file, "primary_contract", upload.key, setProgress);
      }
      setRequest(null);
      setCaseId(null);
      setFile(null);
      setUploadRequest(null);
      setProgress(0);
      form.resetFields();
      onCreated(targetCaseId);
    } catch (reason) {
      setError(targetCaseId ? "主合同上传未完成，案件已保留。可以使用同一文件重试，或进入案件核对材料。" : reason instanceof ApiError && reason.status === 409
        ? "案件创建请求发生冲突。为避免重复创建，请关闭窗口后刷新列表，再确认是否已有同名案件。"
        : reason instanceof ApiError && reason.status > 0 && reason.status < 500
          ? "案件信息未能保存，请检查名称后重试。"
          : "连接结果暂未确认。保留当前名称并重试会复用同一请求，避免重复创建。",
      );
      if (!targetCaseId && reason instanceof ApiError && reason.status > 0 && reason.status < 500) {
        setRequest(null);
      }
    } finally {
      setBusy(false);
    }
  };

  return <Modal
    open={open}
    title={uploadFirst ? "快速审核 · 上传主合同" : "准备完整审核 · 创建案件"}
    okText={request ? "使用同一请求重试" : uploadFirst ? "创建案件并上传主合同" : "创建案件并继续准备"}
    cancelText="取消"
    confirmLoading={busy}
    onOk={() => form.submit()}
    onCancel={() => { if (!busy) onClose(); }}
    closable={!busy}
    mask={{ closable: !busy }}
    destroyOnHidden={false}
  >
    <Paragraph type="secondary">{uploadFirst ? "上传一份 PDF 或 DOCX 主合同（最大 30 MiB），系统会自动创建案件。材料处理就绪后，还需核实签约主体并确认授权，才能发起当前开发版审核。" : "先建立案件，再到案件详情上传主合同、核实签约主体，并逐步补充背景材料和关联合同。"}</Paragraph>
    {uploadFirst && <div className="review-intake-file"><label htmlFor="review-intake-picker">待审主合同</label><input id="review-intake-picker" type="file" accept=".pdf,.docx" disabled={busy || !!uploadRequest} aria-invalid={!!fileError} aria-describedby={fileError ? "review-intake-error" : undefined} onChange={(event) => {
      const selected = event.target.files?.[0] ?? null;
      setFile(selected);
      setFileError(selected ? contractFileError(selected) ?? null : null);
      if (selected && !caseId && !request) form.setFieldValue("title", selected.name.replace(/\.(pdf|docx)$/i, ""));
    }} />{fileError && <p id="review-intake-error" role="alert">{fileError}</p>}{busy && <Progress percent={progress} size="small" />}</div>}
    {error && <Alert className="document-warning" type="error" showIcon title={error} />}
    <Form
      form={form}
      layout="vertical"
      noValidate
      onFinish={(values) => void submit(values)}
      initialValues={{ title: "" }}
    >
      <Form.Item
        label="案件名称"
        name="title"
        rules={[{ required: true, whitespace: true, message: "请输入案件名称。" }, { max: 255, message: "案件名称不能超过 255 个字符。" }]}
      >
        <Input autoFocus={!uploadFirst} maxLength={255} disabled={busy || !!request || !!caseId} placeholder="例如：华东设备服务合同审查" />
      </Form.Item>
    </Form>
    {caseId && error && <Button type="link" disabled={busy} onClick={() => {
      const target = caseId;
      setCaseId(null); setRequest(null); setUploadRequest(null); setFile(null); setError(null); form.resetFields();
      onCreated(target);
    }}>进入已创建案件核对材料</Button>}
  </Modal>;
}
