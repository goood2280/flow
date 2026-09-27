// Excel / Google Sheets copy a range as tab-separated text, one row per line.
// Mirrors backend core/chat_table.py so the chat shows what the server reads.

export const MAX_PROMPT_CHARS = 4000;
export const MAX_TABLE_PROMPT_CHARS = 20000;
const MAX_ROWS = 500;
const MAX_COLUMNS = 20;

function splitTsv(block) {
  // Excel quotes a cell that holds a tab, a line break or a quote.
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < block.length; i += 1) {
    const ch = block[i];
    if (quoted) {
      if (ch === '"' && block[i + 1] === '"') { cell += '"'; i += 1; }
      else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"' && cell === "") quoted = true;
    else if (ch === "\t") { row.push(cell); cell = ""; }
    else if (ch === "\n") { row.push(cell); rows.push(row); row = []; cell = ""; }
    else cell += ch;
  }
  row.push(cell);
  rows.push(row);
  return rows;
}

export function parsePastedTable(text) {
  const source = String(text || "").replace(/\r\n?/g, "\n");
  const lines = source.split("\n");
  const first = lines.findIndex((line) => line.includes("\t"));
  if (first < 0) return null;
  let last = lines.length - 1;
  while (last > first && !lines[last].includes("\t")) last -= 1;
  const rows = splitTsv(lines.slice(first, last + 1).join("\n"))
    .map((cells) => cells.slice(0, MAX_COLUMNS).map((cell) => cell.replace(/\s+/g, " ").trim()))
    .filter((cells) => cells.some(Boolean));
  if (!rows.length) return null;
  let width = Math.max(...rows.map((cells) => cells.length));
  while (width > 1 && !rows.some((cells) => cells.length >= width && cells[width - 1])) width -= 1;
  return {
    intro: lines.slice(0, first).join("\n").trim(),
    outro: lines.slice(last + 1).join("\n").trim(),
    rows: rows.slice(0, MAX_ROWS).map((cells) => [...cells, ...Array(width).fill("")].slice(0, width)),
    truncated: rows.length > MAX_ROWS,
  };
}

export function isTablePaste(text) {
  const table = parsePastedTable(text);
  return Boolean(table && table.rows.length >= 2 && table.rows[0].length >= 2);
}
