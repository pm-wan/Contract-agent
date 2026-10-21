export const designTokens = {
  navy: "#17324d",
  navyDeep: "#10283f",
  bronze: "#9d7343",
  canvas: "#f4f6f7",
  surface: "#ffffff",
  text: "#182b3a",
  muted: "#5c6b76",
  border: "#dbe1e5",
  focus: "#195d9b",
  success: "#276d54",
  warning: "#9a651d",
  danger: "#b33a3a",
  radius: 8,
  fontFamily: '"Noto Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif',
} as const;

export const antTheme = {
  token: {
    colorPrimary: designTokens.navy,
    colorInfo: designTokens.focus,
    colorSuccess: designTokens.success,
    colorWarning: designTokens.warning,
    colorError: designTokens.danger,
    colorText: designTokens.text,
    colorTextSecondary: designTokens.muted,
    colorBorder: designTokens.border,
    colorBgLayout: designTokens.canvas,
    colorBgContainer: designTokens.surface,
    borderRadius: designTokens.radius,
    fontFamily: designTokens.fontFamily,
    fontSize: 15,
    controlHeight: 38,
  },
  components: {
    Layout: { headerBg: designTokens.navyDeep, siderBg: designTokens.navyDeep },
    Menu: { darkItemBg: designTokens.navyDeep, darkItemSelectedBg: designTokens.navy },
    Table: { headerBg: "#f7f9fa" },
  },
};

export function applyCssTokens(root: HTMLElement = document.documentElement) {
  for (const [name, value] of Object.entries(designTokens)) {
    root.style.setProperty(`--color-${name}`, String(value));
  }
}
