export function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function caseStatusLabel(value: string) {
  const labels: Record<string, string> = {
    draft: "草稿",
    ready: "待审准备",
    running: "审查中",
    waiting: "等待处理",
    reviewed: "已审查",
    decided: "已决策",
    closed: "已关闭",
    cancelled: "已取消",
  };
  return labels[value] ?? "状态待核验";
}
