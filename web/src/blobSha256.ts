/** Read and hash a Blob without retaining its bytes beyond the caller's lifetime. */
export async function blobSha256(blob: Blob): Promise<string> {
  if (!globalThis.crypto?.subtle) throw new Error("original_integrity_unavailable");
  const bytes = typeof blob.arrayBuffer === "function"
    ? await blob.arrayBuffer()
    : await readWithFileReader(blob);
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function readWithFileReader(blob: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("original_read_failed"));
    reader.onload = () => {
      if (reader.result instanceof ArrayBuffer) resolve(reader.result);
      else reject(new Error("original_read_failed"));
    };
    reader.readAsArrayBuffer(blob);
  });
}
