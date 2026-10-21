export type EvidenceHighlightResult = "highlighted" | "not_found" | "ambiguous";

/** Mark a quote only when it appears exactly once inside one rendered paragraph/cell. */
export function highlightUniqueDocxQuote(
  page: HTMLElement,
  quote: string,
): EvidenceHighlightResult {
  if (!quote) return "not_found";

  const paragraphs = Array.from(page.querySelectorAll<HTMLElement>("p"));
  const cellsWithoutParagraphs = Array.from(
    page.querySelectorAll<HTMLElement>("td, th"),
  ).filter((cell) => !cell.querySelector("p"));
  const blocks = paragraphs.length > 0
    ? [...paragraphs, ...cellsWithoutParagraphs]
    : [page];

  const matches: Array<{ block: HTMLElement; start: number }> = [];
  for (const block of blocks) {
    const textNodes = collectTextNodes(block);
    const text = textNodes.map((node) => node.nodeValue ?? "").join("");
    let offset = 0;
    while ((offset = text.indexOf(quote, offset)) !== -1) {
      matches.push({ block, start: offset });
      if (matches.length > 1) return "ambiguous";
      offset += 1;
    }
  }
  const match = matches[0];
  if (!match) return "not_found";

  const textNodes = collectTextNodes(match.block);
  const start = locateTextOffset(textNodes, match.start, false);
  const end = locateTextOffset(textNodes, match.start + quote.length, true);
  if (!start || !end) return "not_found";

  const range = page.ownerDocument.createRange();
  range.setStart(start.node, start.offset);
  range.setEnd(end.node, end.offset);
  const mark = page.ownerDocument.createElement("mark");
  mark.dataset.citedEvidence = "true";
  try {
    mark.append(range.extractContents());
    range.insertNode(mark);
  } catch {
    return "not_found";
  }
  return "highlighted";
}

function collectTextNodes(root: HTMLElement): Text[] {
  const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const result: Text[] = [];
  let current = walker.nextNode();
  while (current) {
    if (current.nodeValue) result.push(current as Text);
    current = walker.nextNode();
  }
  return result;
}

function locateTextOffset(
  nodes: Text[],
  target: number,
  preferPrevious: boolean,
): { node: Text; offset: number } | null {
  let consumed = 0;
  for (const node of nodes) {
    const length = node.nodeValue?.length ?? 0;
    const boundary = consumed + length;
    if (target < boundary || (target === boundary && preferPrevious)) {
      return { node, offset: target - consumed };
    }
    consumed = boundary;
  }
  const last = nodes.at(-1);
  if (last && target === consumed) {
    return { node: last, offset: last.nodeValue?.length ?? 0 };
  }
  return null;
}
