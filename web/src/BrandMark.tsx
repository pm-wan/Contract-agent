type BrandMarkProps = {
  size?: "sidebar" | "auth";
};

/** Shared contract review mark used in the app shell and authentication screens. */
export function BrandMark({ size = "sidebar" }: BrandMarkProps) {
  return (
    <span className={`brand-mark brand-mark--${size}`}>
      <img src="/contract-review-mark-v2.svg" alt="合同审查标志" draggable={false} />
    </span>
  );
}

