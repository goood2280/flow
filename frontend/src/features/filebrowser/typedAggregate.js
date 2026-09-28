// 파일탐색기 SQL 칸에 직접 쓰는 집계 — 피벗/집계 버튼 대신.
//   SELECT root_lot_id, wafer_id, AVG(value) WHERE item_id = 'VTH' GROUP BY root_lot_id, wafer_id [ORDER BY ...]
// 서버 view 는 GROUP BY 문법을 받지 않으므로(보안상 SELECT/WHERE/ORDER BY 만 허용) AI SQL 과 같은
// agg_func/agg_column/agg_group_by 파라미터로 바꿔 보낸다. 함수는 한 번에 하나, 서버 집계 계약과 같다.

export const TYPED_AGG_FUNCTIONS = {
  latest: "latest", last: "latest",
  avg: "avg", average: "avg", mean: "avg",
  sum: "sum", min: "min", max: "max",
  median: "median", count: "count",
};

const IDENT = "(?:`(?:``|[^`])+`|\"(?:\"\"|[^\"])+\"|[A-Za-z_][A-Za-z0-9_]*)";
const AGG_ITEM_RE = new RegExp(`^([A-Za-z_]+)\\s*\\(\\s*(\\*|${IDENT})\\s*\\)(?:\\s+AS\\s+${IDENT})?$`, "i");

function unquote(value) {
  const text = String(value || "").trim();
  if (text.length >= 2 && text[0] === "`" && text[text.length - 1] === "`") return text.slice(1, -1).replace(/``/g, "`");
  if (text.length >= 2 && text[0] === '"' && text[text.length - 1] === '"') return text.slice(1, -1).replace(/""/g, '"');
  return text;
}

// 괄호·따옴표 안의 쉼표는 나누지 않는다.
function splitList(value) {
  const text = String(value || "");
  const parts = [];
  let buf = "";
  let quote = "";
  let depth = 0;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quote) {
      buf += ch;
      if (ch === quote) quote = "";
      continue;
    }
    if (ch === "`" || ch === '"' || ch === "'") { quote = ch; buf += ch; continue; }
    if (ch === "(") depth += 1;
    if (ch === ")") depth -= 1;
    if (ch === "," && depth === 0) { if (buf.trim()) parts.push(buf.trim()); buf = ""; continue; }
    buf += ch;
  }
  if (buf.trim()) parts.push(buf.trim());
  return parts;
}

// 문자열 리터럴 안의 GROUP BY/ORDER BY 는 무시하도록 같은 길이의 공백으로 가린다.
function maskLiterals(text) {
  return text.replace(/'(?:''|[^'])*'/g, (m) => " ".repeat(m.length));
}

/**
 * GROUP BY 가 없으면 null.
 * 있으면 { aggregate:{function,column,group_by,alias}, serverSql } 또는 { error }.
 * columns 를 주면 열 이름을 실제 대소문자로 맞추고 없는 열을 알려 준다.
 */
export function parseTypedAggregate(sqlText, columns = []) {
  const text = String(sqlText || "").trim();
  const masked = maskLiterals(text);
  const groupMatch = /\bGROUP\s+BY\b/i.exec(masked);
  if (!groupMatch) {
    // GROUP BY 없이 SELECT COUNT(*) WHERE ... 처럼 쓰면 전체 한 줄 집계.
    const sel = masked.match(/^SELECT\s+([\s\S]+?)(?:\s+WHERE\b|\s+ORDER\s+BY\b|$)/i);
    if (!sel || !splitList(sel[1]).some((item) => AGG_ITEM_RE.test(item))) return null;
  }
  const lookup = new Map((columns || []).map((c) => [String(c).toLowerCase(), String(c)]));
  const resolve = (raw) => {
    const name = unquote(raw);
    if (!lookup.size) return name;
    return lookup.get(name.toLowerCase()) || "";
  };

  const groupStart = groupMatch ? groupMatch.index : text.length;
  const orderFrom = groupMatch ? groupStart : 0;
  const orderMatch = /\s+ORDER\s+BY\s+/i.exec(masked.slice(orderFrom));
  const orderStart = orderMatch ? orderFrom + orderMatch.index : text.length;
  const orderSql = orderMatch ? text.slice(orderStart).trim() : "";
  const head = text.slice(0, Math.min(groupStart, orderStart)).trim();
  const groupText = groupMatch ? text.slice(groupStart + groupMatch[0].length, orderStart).trim() : "";

  const groupBy = [];
  for (const raw of splitList(groupText)) {
    const col = resolve(raw);
    if (!col) return { error: `GROUP BY 의 ${unquote(raw)} 열을 찾을 수 없습니다.` };
    if (!groupBy.includes(col)) groupBy.push(col);
  }

  const selectMatch = head.match(/^SELECT\s+([\s\S]+?)(?:\s+WHERE\s+([\s\S]*))?$/i);
  if (!selectMatch) {
    return { error: "집계는 SELECT 묶을열, 함수(열) WHERE 조건 GROUP BY 묶을열 형태로 씁니다." };
  }
  const whereSql = String(selectMatch[2] || "").trim();
  let aggregate = null;
  for (const item of splitList(selectMatch[1])) {
    const fnMatch = item.match(AGG_ITEM_RE);
    if (fnMatch) {
      const fn = TYPED_AGG_FUNCTIONS[fnMatch[1].toLowerCase()];
      if (!fn) return { error: `${fnMatch[1]} 는 쓸 수 없습니다. LATEST·AVG·SUM·MIN·MAX·MEDIAN·COUNT 중 하나를 쓰세요.` };
      if (aggregate) return { error: "집계 함수는 한 번에 하나만 쓸 수 있습니다." };
      const column = fnMatch[2] === "*" ? "" : resolve(fnMatch[2]);
      if (fnMatch[2] !== "*" && !column) return { error: `${fnMatch[1]}(${unquote(fnMatch[2])}) 의 열을 찾을 수 없습니다.` };
      if (!column && fn !== "count") return { error: `${fnMatch[1].toUpperCase()}(*) 는 안 됩니다. 열 이름을 넣으세요.` };
      aggregate = { function: fn, column, group_by: groupBy, alias: `${fn}_${column || "rows"}` };
      continue;
    }
    const col = resolve(item);
    if (!col) return { error: `SELECT 의 ${unquote(item)} 열을 찾을 수 없습니다.` };
    if (!groupBy.includes(col)) return { error: `${col} 을 보려면 GROUP BY 에도 넣으세요.` };
  }
  if (!aggregate) return { error: "SELECT 에 AVG(value) 같은 집계 함수를 하나 넣으세요." };
  return { aggregate, serverSql: [whereSql, orderSql].filter(Boolean).join(" ") };
}
