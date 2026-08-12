import { useEffect, useMemo, useRef, useState } from "react";
import PageGear from "../components/PageGear";
import { Button, EmptyState, Input, PageHeader, Pill, Select } from "../components/UXKit";
import { sf } from "../lib/api";

const SETTINGS_KEY = "flow:dcop-check:settings:v1";
const PAGE_SIZE = 100;
const INPUT_PAGE_SIZE = 40;
const INITIAL_GRID_ROWS = 3;
const INITIAL_GRID_COLUMNS = 20;

const DEFAULT_SETTINGS = {
  rules: [],
};

const OPERATORS = [
  ["blank", "필수값 누락 (빈값)"],
  ["unique", "유일성 위반 (중복값)"],
  ["max_length", "최대 글자 수 초과"],
  ["comma_count_equals_column", "쉼표 개수+1 ≠ 다른 열의 값"],
  ["numeric", "숫자 형식이 아님"],
  ["max_decimal_places", "최대 소수 자릿수 초과"],
  ["allowed_values", "허용값 목록에 없음"],
  ["not_blank", "비어 있지 않음"],
  ["equals", "같음"],
  ["not_equals", "같지 않음"],
  ["contains", "포함"],
  ["not_contains", "포함하지 않음"],
  ["in", "목록 중 하나 (쉼표 구분)"],
  ["gt", "초과 (숫자)"],
  ["gte", "이상 (숫자)"],
  ["lt", "미만 (숫자)"],
  ["lte", "이하 (숫자)"],
  ["regex", "정규식 일치"],
  ["order_asc", "열 순서 오름차순 위반 (A≤B≤C, 숫자 비교)"],
  ["unclosed_quotes", "닫히지 않은 따옴표 (\" 또는 ')"],
];

const VALUE_OPERATORS = new Set(["max_length", "max_decimal_places", "allowed_values", "equals", "not_equals", "contains", "not_contains", "in", "gt", "gte", "lt", "lte", "regex"]);
const NO_VALUE_OPERATORS = new Set(["blank", "unique", "numeric", "not_blank", "order_asc", "unclosed_quotes"]);
/* unique 처럼 열 이름을 여러 개(쉼표 구분) 받는 연산자. order_asc 는 순서가 의미를
   가지므로(A≤B≤C) 입력한 순서를 그대로 비교에 쓰고, blank 는 지정한 열 중 하나라도
   비어 있으면 위반으로 본다. */
const MULTI_COLUMN_OPERATORS = new Set(["unique", "order_asc", "blank"]);

function operatorLabel(operator) {
  return OPERATORS.find(([key]) => key === operator)?.[1] || operator;
}

/* 규칙은 특정 표의 열을 고르는 게 아니라 **열 이름**으로 세운다.
   붙여넣은 표에 같은 이름의 열이 들어오면 그 열을 대상으로 검사하고, 없으면
   이번 검사만 건너뛴다 (규칙 정의는 그대로 남는다).
   앞뒤 공백·대소문자만 다른 표기는 같은 열로 본다 — 원본 Excel 머리글이
   매번 정확히 같게 오지 않기 때문이다. */
function normalizeHeaderName(value) {
  return String(value ?? "").trim().toLocaleLowerCase();
}

function headerIndex(headers, name) {
  const wanted = String(name ?? "").trim();
  if (!wanted) return -1;
  const exact = headers.indexOf(wanted);
  if (exact >= 0) return exact;
  const loose = normalizeHeaderName(wanted);
  return headers.findIndex((header) => normalizeHeaderName(header) === loose);
}

function hasHeader(headers, name) {
  return headerIndex(headers, name) >= 0;
}

function ruleColumns(rule) {
  if (!MULTI_COLUMN_OPERATORS.has(rule.operator)) return [rule.column];
  return (rule.uniqueColumns || []).length ? rule.uniqueColumns : [rule.column];
}

function parseColumnList(text) {
  return String(text ?? "").split(",").map((item) => item.trim()).filter(Boolean);
}

function uniqueColumnsText(rule) {
  return rule.uniqueColumnsText ?? (rule.uniqueColumns || []).join(", ");
}

/* 규칙에 지정했지만 이번 표에는 없는 열 — 화면에 "건너뜀" 사유로 보여준다. */
function missingRuleColumns(rule, headers) {
  const names = [...ruleColumns(rule)];
  if (rule.operator === "comma_count_equals_column") names.push(rule.compareColumn);
  return [...new Set(names.map((name) => String(name ?? "").trim()).filter(Boolean))]
    .filter((name) => !hasHeader(headers, name));
}

function defaultMessage(rule) {
  const column = rule.column || "대상 열";
  const uniqueLabel = (rule.uniqueColumns || []).length ? rule.uniqueColumns.join(" + ") : column;
  switch (rule.operator) {
    case "blank": return `${uniqueLabel}: 필수값이 비어 있습니다.`;
    case "unique": return `${uniqueLabel}: 열 조합이 중복되어 유일성 조건을 만족하지 않습니다.`;
    case "max_length": return `${column}: ${rule.value || "지정"}자를 초과했습니다.`;
    case "comma_count_equals_column": return `${column}: 쉼표 개수+1이 ${rule.compareColumn || "비교 열"} 값과 다릅니다.`;
    case "numeric": return `${column}: 숫자가 아닌 값입니다.`;
    case "max_decimal_places": return `${column}: 소수 자릿수가 ${rule.value || "지정"}자리를 초과했습니다.`;
    case "allowed_values": return `${column}: 허용값(${rule.value || "목록"})에 없는 값입니다.`;
    case "order_asc": {
      const orderLabel = (rule.uniqueColumns || []).length ? rule.uniqueColumns.join(" ≤ ") : column;
      return `${orderLabel}: 오름차순(순서대로 A≤B≤C) 조건을 만족하지 않습니다.`;
    }
    case "unclosed_quotes": return `${column}: 큰따옴표(") 또는 작은따옴표(')가 닫히지 않았습니다.`;
    default: return `${column}: ${operatorLabel(rule.operator)} 조건에 해당합니다.`;
  }
}

function makeRule() {
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
    column: "",
    operator: "blank",
    value: "",
    compareColumn: "",
    uniqueColumns: [],
    uniqueColumnsText: "",
    severity: "fail",
    message: "",
    enabled: true,
  };
}

function loadSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "null");
    if (saved && Array.isArray(saved.rules)) return { ...DEFAULT_SETTINGS, ...saved };
  } catch (_) {}
  return DEFAULT_SETTINGS;
}

function uniqueHeaders(rawHeaders) {
  const used = new Map();
  return rawHeaders.map((value, index) => {
    const base = String(value || "").trim() || `열 ${index + 1}`;
    const count = (used.get(base) || 0) + 1;
    used.set(base, count);
    return count === 1 ? base : `${base} (${count})`;
  });
}

function blankGrid() {
  return Array.from({ length: INITIAL_GRID_ROWS }, () => Array(INITIAL_GRID_COLUMNS).fill(""));
}

function gridBounds(grid) {
  let lastRow = -1;
  let lastColumn = -1;
  grid.forEach((row, rowIndex) => row.forEach((value, columnIndex) => {
    if (String(value ?? "") !== "") {
      lastRow = Math.max(lastRow, rowIndex);
      lastColumn = Math.max(lastColumn, columnIndex);
    }
  }));
  return { rows: lastRow + 1, columns: lastColumn + 1 };
}

function tableFromGrid(grid) {
  const bounds = gridBounds(grid);
  if (!bounds.rows || !bounds.columns) return { headers: [], rows: [] };
  const matrix = Array.from({ length: bounds.rows }, (_, rowIndex) =>
    Array.from({ length: bounds.columns }, (_, columnIndex) => grid[rowIndex]?.[columnIndex] ?? "")
  );
  const sourceHeaders = matrix.shift();
  const headers = uniqueHeaders(Array.from({ length: bounds.columns }, (_, i) => sourceHeaders?.[i] ?? ""));
  const rows = matrix
    .filter((row) => row.some((cell) => String(cell).trim() !== ""))
    .map((row) => Array.from({ length: bounds.columns }, (_, i) => row[i] ?? ""));
  return { headers, rows };
}

function excelColumnName(index) {
  let value = index + 1;
  let name = "";
  while (value > 0) {
    value -= 1;
    name = String.fromCharCode(65 + (value % 26)) + name;
    value = Math.floor(value / 26);
  }
  return name;
}

function parseClipboardGrid(text) {
  const source = String(text || "");
  const rows = [[]];
  let value = "";
  let quoted = false;
  for (let index = 0; index < source.length; index += 1) {
    const char = source[index];
    if (char === '"') {
      if (quoted && source[index + 1] === '"') {
        value += '"';
        index += 1;
      } else if (quoted) quoted = false;
      else if (value === "") quoted = true;
      else value += '"';
    } else if (char === "\t" && !quoted) {
      rows[rows.length - 1].push(value);
      value = "";
    } else if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && source[index + 1] === "\n") index += 1;
      rows[rows.length - 1].push(value);
      rows.push([]);
      value = "";
    } else {
      value += char;
    }
  }
  rows[rows.length - 1].push(value);
  while (rows.length > 1 && rows[rows.length - 1].every((cell) => cell === "")) rows.pop();
  return rows;
}

function SpreadsheetInput({ grid, setGrid }) {
  const rootRef = useRef(null);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState({ row: 0, column: 0 });
  const bounds = useMemo(() => gridBounds(grid), [grid]);
  const rowCount = Math.max(INITIAL_GRID_ROWS, grid.length, bounds.rows);
  const columnCount = Math.max(INITIAL_GRID_COLUMNS, bounds.columns, grid.reduce((max, row) => Math.max(max, row.length), 0));
  const pageCount = Math.max(1, Math.ceil(rowCount / INPUT_PAGE_SIZE));
  const firstRow = (page - 1) * INPUT_PAGE_SIZE;
  const visibleRowCount = Math.min(INPUT_PAGE_SIZE, rowCount - firstRow);

  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  const updateCell = (rowIndex, columnIndex, value) => setGrid((current) => {
    const next = current.map((row) => [...row]);
    while (next.length <= rowIndex) next.push(Array(columnCount).fill(""));
    while (next[rowIndex].length <= columnIndex) next[rowIndex].push("");
    next[rowIndex][columnIndex] = value;
    return next;
  });

  const pasteAt = (event, startRow, startColumn) => {
    event.preventDefault();
    const pasted = parseClipboardGrid(event.clipboardData.getData("text/plain"));
    setGrid((current) => {
      const requiredColumns = Math.max(columnCount, startColumn + Math.max(...pasted.map((row) => row.length)));
      const next = current.map((row) => [...row, ...Array(Math.max(0, requiredColumns - row.length)).fill("")]);
      while (next.length < startRow + pasted.length) next.push(Array(requiredColumns).fill(""));
      pasted.forEach((row, rowOffset) => row.forEach((value, columnOffset) => {
        next[startRow + rowOffset][startColumn + columnOffset] = value;
      }));
      return next;
    });
  };

  const focusCell = (row, column) => {
    if (row < 0 || column < 0 || column >= columnCount || row >= rowCount) return;
    const targetPage = Math.floor(row / INPUT_PAGE_SIZE) + 1;
    setSelected({ row, column });
    if (targetPage !== page) {
      setPage(targetPage);
      setTimeout(() => rootRef.current?.querySelector(`[data-grid-cell="${row}-${column}"]`)?.focus(), 0);
    } else {
      rootRef.current?.querySelector(`[data-grid-cell="${row}-${column}"]`)?.focus();
    }
  };

  const onCellKeyDown = (event, row, column) => {
    if (event.key === "ArrowUp") { event.preventDefault(); focusCell(row - 1, column); }
    if (event.key === "ArrowDown" || event.key === "Enter") { event.preventDefault(); focusCell(row + 1, column); }
    if (event.key === "ArrowLeft" && event.currentTarget.selectionStart === 0) { event.preventDefault(); focusCell(row, column - 1); }
    if (event.key === "ArrowRight" && event.currentTarget.selectionStart === event.currentTarget.value.length) { event.preventDefault(); focusCell(row, column + 1); }
  };

  return (
    <div ref={rootRef} style={{ display: "grid", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--text-secondary)", fontSize: 13 }}>
        <span>선택 셀 <b style={{ color: "var(--accent)" }}>{excelColumnName(selected.column)}{selected.row + 1}</b></span>
        <span>·</span>
        <span>{bounds.rows ? `${bounds.rows.toLocaleString()}행 × ${bounds.columns.toLocaleString()}열 입력됨` : "빈 시트"}</span>
        <span style={{ marginLeft: "auto" }}>Excel 범위 복사(Ctrl+C) → 시작 셀 선택 → Ctrl+V</span>
      </div>
      <div style={{ overflow: "auto", maxHeight: "calc(100vh - 260px)", border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
        <table style={{ borderCollapse: "separate", borderSpacing: 0, tableLayout: "fixed", minWidth: columnCount * 140 + 48 }}>
          <thead><tr>
            <th style={{ position: "sticky", top: 0, left: 0, zIndex: 4, width: 48, minWidth: 48, height: 30, background: "var(--bg-tertiary)", borderRight: "1px solid var(--border)", borderBottom: "1px solid var(--border)" }} />
            {Array.from({ length: columnCount }, (_, column) => <th key={column} style={{ position: "sticky", top: 0, zIndex: 3, width: 140, minWidth: 140, height: 36, background: "var(--bg-tertiary)", borderRight: "1px solid var(--border)", borderBottom: "1px solid var(--border)", fontSize: 12, color: "var(--text-secondary)" }}>
              <span>{excelColumnName(column)}</span>
              {grid[0]?.[column] && <span style={{ marginLeft: 6, color: "var(--accent)", fontWeight: 700 }}>· {grid[0][column]}</span>}
            </th>)}
          </tr></thead>
          <tbody>
            {Array.from({ length: visibleRowCount }, (_, offset) => {
              const row = firstRow + offset;
              return <tr key={row}>
                <th style={{ position: "sticky", left: 0, zIndex: 2, width: 48, height: 29, padding: 0, textAlign: "center", background: row === 0 ? "var(--accent-glow)" : "var(--bg-tertiary)", borderRight: "1px solid var(--border)", borderBottom: "1px solid var(--border)", color: row === 0 ? "var(--accent)" : "var(--text-secondary)", fontSize: 12 }}>{row === 0 ? "열 이름" : row + 1}</th>
                {Array.from({ length: columnCount }, (_, column) => {
                  const active = selected.row === row && selected.column === column;
                  return <td key={column} style={{ width: 140, height: 29, padding: 0, borderRight: "1px solid var(--border)", borderBottom: "1px solid var(--border)", background: row === 0 ? "var(--accent-glow)" : "var(--bg-primary)" }}>
                    <input
                      data-grid-cell={`${row}-${column}`}
                      aria-label={`${excelColumnName(column)}${row + 1}`}
                      value={grid[row]?.[column] ?? ""}
                      onFocus={() => setSelected({ row, column })}
                      onChange={(event) => updateCell(row, column, event.target.value)}
                      onPaste={(event) => pasteAt(event, row, column)}
                      onKeyDown={(event) => onCellKeyDown(event, row, column)}
                      style={{ width: "100%", height: "100%", boxSizing: "border-box", padding: "4px 7px", border: active ? "2px solid var(--accent)" : 0, outline: 0, background: "transparent", color: "var(--text-primary)", fontSize: 13 }}
                    />
                  </td>;
                })}
              </tr>;
            })}
          </tbody>
        </table>
      </div>
      {pageCount > 1 && <div style={{ display: "flex", justifyContent: "center", alignItems: "center", gap: 8 }}>
        <Button size="sm" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>이전</Button>
        <span style={{ fontSize: 13 }}>{page} / {pageCount} 페이지</span>
        <Button size="sm" disabled={page >= pageCount} onClick={() => setPage((value) => value + 1)}>다음</Button>
      </div>}
    </div>
  );
}

function isNumericText(value) {
  return /^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?$/.test(String(value ?? "").trim());
}

function decimalPlaces(value) {
  const text = String(value ?? "").trim().toLowerCase();
  if (!isNumericText(text)) return null;
  const [mantissa, exponentText] = text.split("e");
  const fractionLength = (mantissa.split(".")[1] || "").length;
  const exponent = Number(exponentText || 0);
  return Math.max(0, fractionLength - exponent);
}

function ruleMatches(value, rule, context) {
  const actual = String(value ?? "").trim();
  const expected = String(rule.value ?? "").trim();
  const a = actual.toLocaleLowerCase();
  const e = expected.toLocaleLowerCase();
  switch (rule.operator) {
    case "blank": {
      const columns = ruleColumns(rule).filter((column) => String(column ?? "").trim() !== "");
      if (!columns.length) return actual === "";
      return columns.some((column) => String(context.cells[headerIndex(context.headers, column)] ?? "").trim() === "");
    }
    case "unique": return context.uniqueKey !== null && (context.uniqueCounts.get(context.uniqueKey) || 0) > 1;
    case "max_length": return actual.length > Number(expected);
    case "comma_count_equals_column": {
      const compareValue = String(context.cells[headerIndex(context.headers, rule.compareColumn)] ?? "").trim();
      const itemCount = (actual.match(/,/g) || []).length + 1;
      return !/^\d+$/.test(compareValue) || itemCount !== Number(compareValue);
    }
    case "numeric": return !isNumericText(actual);
    case "max_decimal_places": {
      const places = decimalPlaces(actual);
      return places !== null && places > Number(expected);
    }
    case "allowed_values": {
      const allowed = expected.split(/[,\n]/).map((item) => item.trim().toLocaleLowerCase()).filter(Boolean);
      return !allowed.includes(a);
    }
    case "not_blank": return actual !== "";
    case "equals": return a === e;
    case "not_equals": return a !== e;
    case "contains": return a.includes(e);
    case "not_contains": return !a.includes(e);
    case "in": return expected.split(",").map((item) => item.trim().toLocaleLowerCase()).filter(Boolean).includes(a);
    case "gt": return Number.isFinite(Number(actual)) && Number(actual) > Number(expected);
    case "gte": return Number.isFinite(Number(actual)) && Number(actual) >= Number(expected);
    case "lt": return Number.isFinite(Number(actual)) && Number(actual) < Number(expected);
    case "lte": return Number.isFinite(Number(actual)) && Number(actual) <= Number(expected);
    case "regex": {
      try { return new RegExp(expected, "i").test(actual); } catch (_) { return false; }
    }
    case "order_asc": {
      const columns = ruleColumns(rule).filter((column) => String(column ?? "").trim() !== "");
      if (columns.length < 2) return false;
      const values = columns.map((column) => String(context.cells[headerIndex(context.headers, column)] ?? "").trim());
      if (values.some((item) => !isNumericText(item))) return true;
      const numbers = values.map(Number);
      for (let index = 1; index < numbers.length; index += 1) {
        if (numbers[index - 1] > numbers[index]) return true;
      }
      return false;
    }
    case "unclosed_quotes": {
      const doubleQuotes = (actual.match(/"/g) || []).length;
      const singleQuotes = (actual.match(/'/g) || []).length;
      return doubleQuotes % 2 !== 0 || singleQuotes % 2 !== 0;
    }
    default: return false;
  }
}

/* 규칙 자체가 완성됐는지 — 표와 무관하게 판단한다. */
function isRuleConfigured(rule) {
  if (!rule.enabled) return false;
  const columns = ruleColumns(rule);
  if (!columns.length || columns.some((column) => !String(column ?? "").trim())) return false;
  if (rule.operator === "order_asc" && columns.length < 2) return false;
  if (rule.operator === "comma_count_equals_column") return String(rule.compareColumn ?? "").trim() !== "";
  if (VALUE_OPERATORS.has(rule.operator)) return String(rule.value ?? "").trim() !== "";
  return NO_VALUE_OPERATORS.has(rule.operator);
}

/* 완성된 규칙이 이번에 들어온 표에도 적용되는지 — 지정한 열 이름이 다 있어야 한다. */
function ruleAppliesTo(rule, headers) {
  return isRuleConfigured(rule) && missingRuleColumns(rule, headers).length === 0;
}

function inspectRows(headers, rows, rules) {
  const activeRules = rules
    .map((rule, ruleIndex) => ({ ...rule, ruleNumber: ruleIndex + 1 }))
    .filter((rule) => ruleAppliesTo(rule, headers));
  const uniqueCountsByRule = new Map();
  activeRules.filter((rule) => rule.operator === "unique").forEach((rule) => {
    const columnIndexes = ruleColumns(rule).map((column) => headerIndex(headers, column));
    const counts = new Map();
    rows.forEach((cells) => {
      const values = columnIndexes.map((columnIndex) => String(cells[columnIndex] ?? "").trim().toLocaleLowerCase());
      if (values.every(Boolean)) {
        const key = JSON.stringify(values);
        counts.set(key, (counts.get(key) || 0) + 1);
      }
    });
    uniqueCountsByRule.set(rule.id, counts);
  });
  return rows.map((cells, rowIndex) => {
    const hits = activeRules.filter((rule) => {
      const uniqueValues = ruleColumns(rule)
        .map((column) => String(cells[headerIndex(headers, column)] ?? "").trim().toLocaleLowerCase());
      return ruleMatches(cells[headerIndex(headers, rule.column)], rule, {
        cells,
        headers,
        uniqueCounts: uniqueCountsByRule.get(rule.id) || new Map(),
        uniqueKey: uniqueValues.every(Boolean) ? JSON.stringify(uniqueValues) : null,
      });
    });
    const severity = hits.some((rule) => rule.severity === "fail")
      ? "fail"
      : hits.some((rule) => rule.severity === "warning") ? "warning" : "pass";
    const messages = hits
      .filter((rule) => rule.severity === severity)
      .map((rule) => rule.message.trim() || defaultMessage(rule));
    return {
      rowIndex,
      cells,
      severity,
      detail: [...new Set(messages)].join(" / "),
      ruleHits: hits.map((rule) => ({
        ruleNumber: rule.ruleNumber,
        severity: rule.severity,
        message: rule.message.trim() || defaultMessage(rule),
      })),
    };
  });
}

function ruleFindingSummary(results, rules) {
  return rules.map((rule, ruleIndex) => {
    const rowNumbers = results
      .filter((row) => row.ruleHits?.some((hit) => hit.ruleNumber === ruleIndex + 1))
      .map((row) => row.rowIndex + 2);
    return {
      rule_number: ruleIndex + 1,
      severity: rule.severity,
      count: rowNumbers.length,
      row_numbers: rowNumbers.slice(0, 4),
      rows_over_limit: rowNumbers.length >= 5,
      message: rule.message.trim() || defaultMessage(rule),
    };
  }).filter((finding) => finding.count > 0);
}

function fallbackInspectionSummary(findings) {
  if (!findings.length) return "FAIL 및 WARNING 항목이 없습니다.";
  return findings.map((finding) => {
    const rows = finding.rows_over_limit
      ? "5행 이상"
      : finding.row_numbers.map((row) => `${row.toLocaleString()}행`).join(", ");
    return `규칙 ${finding.rule_number}번 ${finding.severity.toUpperCase()} ${finding.count.toLocaleString()}건 (${rows})`;
  }).join(" · ");
}

function ruleConditionText(rule) {
  const target = ruleColumns(rule).filter(Boolean).join(rule.operator === "order_asc" ? " ≤ " : " + ");
  const comparison = rule.operator === "comma_count_equals_column"
    ? rule.compareColumn
    : (VALUE_OPERATORS.has(rule.operator) ? rule.value : "");
  return [target || "열 이름 미설정", operatorLabel(rule.operator), comparison].filter(Boolean).join(" · ");
}

function xmlEscape(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function downloadExcel(headers, results) {
  const cell = (value, style = "") => `<Cell${style ? ` ss:StyleID="${style}"` : ""}><Data ss:Type="String">${xmlEscape(value)}</Data></Cell>`;
  const headerRow = `<Row>${cell("검사 결과", "Header")}${headers.map((h) => cell(h, "Header")).join("")}</Row>`;
  const bodyRows = results.map((row) => {
    const style = row.severity === "fail" ? "Fail" : row.severity === "warning" ? "Warning" : "Normal";
    // 줄바꿈(\n)을 그대로 넣으면 WrapText 없이도 행이 여러 줄로 늘어나므로, 검사 결과는
    // 한 줄로 이어 붙인다 — 대신 "검사 결과" 열 너비를 아래에서 넉넉하게 잡는다.
    const resultText = row.severity === "pass" ? "PASS" : `${row.severity.toUpperCase()}${row.detail ? ` - ${row.detail}` : ""}`;
    return `<Row>${cell(resultText, style)}${row.cells.map((value) => cell(value, style)).join("")}</Row>`;
  }).join("");
  const workbook = `<?xml version="1.0"?><?mso-application progid="Excel.Sheet"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
<Styles>
  <Style ss:ID="Normal"><Alignment ss:Vertical="Center" ss:WrapText="0"/><Borders><Border ss:Position="Bottom" ss:LineStyle="Continuous" ss:Weight="1" ss:Color="#D1D5DB"/></Borders></Style>
  <Style ss:ID="Header"><Font ss:Bold="1" ss:Color="#FFFFFF"/><Interior ss:Color="#374151" ss:Pattern="Solid"/><Alignment ss:Vertical="Center" ss:WrapText="0"/></Style>
  <Style ss:ID="Fail"><Interior ss:Color="#FECACA" ss:Pattern="Solid"/><Alignment ss:Vertical="Center" ss:WrapText="0"/></Style>
  <Style ss:ID="Warning"><Interior ss:Color="#FEF3C7" ss:Pattern="Solid"/><Alignment ss:Vertical="Center" ss:WrapText="0"/></Style>
</Styles>
<Worksheet ss:Name="DCOP 검사결과"><Table><Column ss:Width="420"/>${headerRow}${bodyRows}</Table></Worksheet></Workbook>`;
  const blob = new Blob(["\ufeff", workbook], { type: "application/vnd.ms-excel;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `DCOP_검사결과_${new Date().toISOString().slice(0, 10)}.xls`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const tableCell = {
  padding: "7px 10px",
  borderBottom: "1px solid var(--border)",
  whiteSpace: "nowrap",
  fontSize: 13,
  maxWidth: 260,
  overflow: "hidden",
  textOverflow: "ellipsis",
};

function RuleEditor({ settings, setSettings, headers }) {
  const updateRule = (id, patch) => setSettings((current) => ({
    ...current,
    rules: current.rules.map((rule) => rule.id === id ? { ...rule, ...patch } : rule),
  }));
  const removeRule = (id) => setSettings((current) => ({ ...current, rules: current.rules.filter((rule) => rule.id !== id) }));
  const duplicateRule = (source) => setSettings((current) => ({
    ...current,
    rules: [...current.rules, { ...source, id: `${Date.now()}-${Math.random().toString(36).slice(2)}` }],
  }));
  const addRule = () => setSettings((current) => ({ ...current, rules: [...current.rules, makeRule()] }));
  const toggleUniqueColumn = (rule, column) => {
    const current = parseColumnList(uniqueColumnsText(rule));
    const next = current.some((item) => normalizeHeaderName(item) === normalizeHeaderName(column))
      ? current.filter((item) => normalizeHeaderName(item) !== normalizeHeaderName(column))
      : [...current, column];
    updateRule(rule.id, { uniqueColumns: next, uniqueColumnsText: next.join(", "), message: "" });
  };
  const setUniqueColumnsText = (rule, text) =>
    updateRule(rule.id, { uniqueColumnsText: text, uniqueColumns: parseColumnList(text), message: "" });

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <datalist id="dcop-header-options">
        {headers.map((header) => <option key={header} value={header} />)}
      </datalist>
      <div style={{ color: "var(--text-secondary)", fontSize: 12, lineHeight: 1.6 }}>
        아래 조건은 모두 <b>위반 조건</b>입니다. 위반한 행에 지정한 판정과 상세 내용이 들어가며, 여러 규칙이 맞으면 FAIL이 WARNING보다 우선합니다.
        <br />
        규칙은 <b>열 이름</b>으로 세웁니다. 붙여넣은 표에 같은 이름의 열이 들어오면 그 열을 검사하고, 없으면 이번 검사만 건너뜁니다(규칙은 그대로 유지).
        앞뒤 공백·대소문자만 다른 이름은 같은 열로 봅니다.
      </div>
      {settings.rules.map((rule, index) => (
        <div key={rule.id} style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 10, display: "grid", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <b style={{ fontSize: 13 }}>규칙 {index + 1}</b>
            <label style={{ marginLeft: "auto", fontSize: 12 }}><input type="checkbox" checked={rule.enabled} onChange={(e) => updateRule(rule.id, { enabled: e.target.checked })} /> 사용</label>
            <button type="button" onClick={() => duplicateRule(rule)} style={{ border: 0, background: "transparent", color: "var(--accent)", cursor: "pointer" }}>복제</button>
            <button type="button" onClick={() => removeRule(rule.id)} style={{ border: 0, background: "transparent", color: "var(--danger)", cursor: "pointer" }}>삭제</button>
          </div>
          {MULTI_COLUMN_OPERATORS.has(rule.operator) ? (
            <div style={{ display: "grid", gap: 6 }}>
              <div style={{ fontSize: 12, fontWeight: 700 }}>
                {rule.operator === "order_asc"
                  ? "순서를 검사할 열 이름 (쉼표 구분, 입력한 순서대로 A≤B≤C 비교)"
                  : rule.operator === "blank"
                    ? "필수값 여부를 검사할 열 이름 (쉼표 구분, 하나라도 비어 있으면 위반)"
                    : "유일성을 검사할 열 이름 조합 (쉼표 구분)"}
              </div>
              <Input
                value={uniqueColumnsText(rule)}
                list="dcop-header-options"
                placeholder={rule.operator === "order_asc" ? "예: START_TIME, END_TIME" : rule.operator === "blank" ? "예: PRODUCT, STEP_ID, DCOP_NAME" : "예: PRODUCT, STEP_ID"}
                onChange={(e) => setUniqueColumnsText(rule, e.target.value)}
              />
              {headers.length > 0 && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {headers.map((header) => {
                    const checked = parseColumnList(uniqueColumnsText(rule))
                      .some((item) => normalizeHeaderName(item) === normalizeHeaderName(header));
                    return <button
                      key={header}
                      type="button"
                      aria-pressed={checked}
                      title="현재 표의 열 이름을 넣거나 뺍니다"
                      onClick={() => toggleUniqueColumn(rule, header)}
                      style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "5px 8px", borderRadius: 4, border: checked ? "1px solid var(--accent)" : "1px solid var(--border)", background: checked ? "var(--accent-glow)" : "var(--bg-secondary)", color: checked ? "var(--accent)" : "var(--text-primary)", fontSize: 12, cursor: "pointer" }}
                    >
                      <span aria-hidden="true">{checked ? "✓" : "＋"}</span>
                      {header}
                    </button>;
                  })}
                </div>
              )}
              <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                {rule.operator === "order_asc"
                  ? (parseColumnList(uniqueColumnsText(rule)).join(" ≤ ") || "열 이름을 두 개 이상 입력하세요 (입력한 순서로 비교합니다).")
                  : (parseColumnList(uniqueColumnsText(rule)).join(" + ") || "열 이름을 한 개 이상 입력하세요.")}
              </div>
              {rule.operator === "blank" && parseColumnList(uniqueColumnsText(rule)).length > 0 && (
                <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                  지정한 열 중 하나라도 값이 비어 있으면 위반으로 표시합니다.
                </div>
              )}
            </div>
          ) : (
            <Input
              value={rule.column}
              list="dcop-header-options"
              placeholder="검사할 열 이름 (예: DCOP_NAME)"
              onChange={(e) => updateRule(rule.id, { column: e.target.value, message: "" })}
            />
          )}
          <Select value={rule.operator} onChange={(e) => {
            const operator = e.target.value;
            const seeded = MULTI_COLUMN_OPERATORS.has(operator) && String(rule.column || "").trim() ? [rule.column.trim()] : [];
            updateRule(rule.id, {
              operator,
              value: "",
              compareColumn: "",
              uniqueColumns: seeded,
              uniqueColumnsText: seeded.join(", "),
              message: "",
            });
          }}>
            {OPERATORS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </Select>
          {rule.operator === "comma_count_equals_column" && (
            <Input
              value={rule.compareColumn || ""}
              list="dcop-header-options"
              placeholder="쉼표 개수+1과 비교할 열 이름"
              onChange={(e) => updateRule(rule.id, { compareColumn: e.target.value })}
            />
          )}
          {VALUE_OPERATORS.has(rule.operator) && (
            <Input
              value={rule.value}
              inputMode={["max_length", "max_decimal_places", "gt", "gte", "lt", "lte"].includes(rule.operator) ? "decimal" : undefined}
              placeholder={rule.operator === "max_length" ? "최대 글자 수 (예: 40)" : rule.operator === "max_decimal_places" ? "최대 소수 자릿수 (예: 10)" : rule.operator === "allowed_values" ? "허용값 (예: N,Y)" : "기준값"}
              onChange={(e) => updateRule(rule.id, { value: e.target.value })}
            />
          )}
          <Select value={rule.severity} onChange={(e) => updateRule(rule.id, { severity: e.target.value })}>
            <option value="warning">WARNING</option>
            <option value="fail">FAIL</option>
          </Select>
          <Input value={rule.message} placeholder={defaultMessage(rule)} onChange={(e) => updateRule(rule.id, { message: e.target.value })} />
          {!isRuleConfigured(rule) ? (
            <div style={{ fontSize: 12, color: "var(--warn)" }}>열 이름과 필요한 기준값을 입력해야 이 규칙이 실행됩니다.</div>
          ) : missingRuleColumns(rule, headers).length > 0 && (
            <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
              현재 표에 <b>{missingRuleColumns(rule, headers).join(", ")}</b> 열이 없어 이번 검사에서는 건너뜁니다. 규칙은 그대로 유지됩니다.
            </div>
          )}
        </div>
      ))}
      <Button variant="primary" onClick={addRule}>＋ 검사 규칙 추가</Button>
    </div>
  );
}

export default function MyDcopCheck() {
  const [settings, setSettings] = useState(loadSettings);
  const [draftGrid, setDraftGrid] = useState(blankGrid);
  const [table, setTable] = useState({ headers: [], rows: [] });
  const [results, setResults] = useState([]);
  const [statusFilter, setStatusFilter] = useState("all");
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [inspectionSummary, setInspectionSummary] = useState({ text: "", source: "", loading: false });

  const draftHeaders = useMemo(() => {
    const firstRow = draftGrid[0] || [];
    let lastColumn = -1;
    firstRow.forEach((value, index) => { if (String(value ?? "").trim()) lastColumn = index; });
    return lastColumn >= 0 ? uniqueHeaders(firstRow.slice(0, lastColumn + 1)) : [];
  }, [draftGrid]);
  const ruleHeaders = table.headers.length ? table.headers : draftHeaders;

  useEffect(() => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  }, [settings]);

  const summary = useMemo(() => results.reduce((acc, row) => {
    acc[row.severity] += 1;
    return acc;
  }, { pass: 0, warning: 0, fail: 0 }), [results]);

  // 규칙 번호는 전체 목록 기준으로 고정한다 — 건너뛴 규칙이 있어도 번호가 밀리지 않는다.
  const numberedRules = useMemo(
    () => settings.rules.map((rule, index) => ({ rule, index })), [settings.rules]);
  const appliedRules = useMemo(
    () => numberedRules.filter(({ rule }) => ruleAppliesTo(rule, table.headers)),
    [numberedRules, table.headers]);
  const skippedRules = useMemo(
    () => numberedRules.filter(({ rule }) =>
      isRuleConfigured(rule) && missingRuleColumns(rule, table.headers).length > 0),
    [numberedRules, table.headers]);

  const filtered = useMemo(() => statusFilter === "all" ? results : results.filter((row) => row.severity === statusFilter), [results, statusFilter]);
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const visibleRows = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  useEffect(() => setPage(1), [statusFilter, results]);

  // 규칙은 열 이름으로 정의된다 — 새 표를 받아도 규칙을 건드리지 않는다.
  // (예전에는 새 표에 없는 열을 가진 규칙의 대상 열을 지워버려 규칙이 사라졌다.)
  const receiveTable = () => {
    setTable(tableFromGrid(draftGrid));
    setResults([]);
    setStatusFilter("all");
  };

  const runInspection = () => {
    if (!table.rows.length || busy) return;
    setBusy(true);
    setInspectionSummary({ text: "", source: "", loading: true });
    setTimeout(() => {
      const inspected = inspectRows(table.headers, table.rows, settings.rules);
      const findings = ruleFindingSummary(inspected, settings.rules);
      const fallback = fallbackInspectionSummary(findings);
      setResults(inspected);
      setBusy(false);
      if (!findings.length) {
        setInspectionSummary({ text: fallback, source: "rule", loading: false });
        return;
      }
      sf("/api/llm/dcop/summary", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ findings }),
      }).then((response) => {
        const text = String(response?.summary || "").trim();
        setInspectionSummary({
          text: response?.used && text ? text : fallback,
          source: response?.used ? "gpt_oss_120b" : "rule",
          loading: false,
        });
      }).catch(() => setInspectionSummary({ text: fallback, source: "rule", loading: false }));
    }, 20);
  };

  const reset = () => {
    setDraftGrid(blankGrid());
    setTable({ headers: [], rows: [] });
    setResults([]);
    setStatusFilter("all");
    setInspectionSummary({ text: "", source: "", loading: false });
  };

  return (
    <div style={{ minHeight: "calc(100vh - 52px)", background: "var(--bg-primary)" }}>
      <PageHeader
        right={<div style={{ display: "flex", gap: 8 }}>
          {table.rows.length > 0 && <Button onClick={reset}>새로 붙여넣기</Button>}
          <Button variant="primary" disabled={!table.rows.length || busy} onClick={runInspection}>{busy ? "검사 중..." : "검사 실행"}</Button>
          <Button disabled={!results.length} onClick={() => downloadExcel(table.headers, results)}>Excel 다운로드</Button>
        </div>}
      />

      <div style={{ padding: 16, display: "grid", gap: 12 }}>
        {!table.rows.length ? (
          <section style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", borderRadius: 6, padding: 16 }}>
            <div style={{ fontWeight: 700, marginBottom: 6 }}>작성한 DCOP 붙여넣기</div>
            <div style={{ color: "var(--text-secondary)", fontSize: 13, marginBottom: 10 }}>Excel에서 전체 범위를 복사해 A1 셀에 붙여넣으세요. 첫 번째 행은 자동으로 열 이름으로 인식됩니다.</div>
            <SpreadsheetInput grid={draftGrid} setGrid={setDraftGrid} />
            <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}>
              <Button variant="primary" disabled={!gridBounds(draftGrid).rows} onClick={receiveTable}>입력 완료</Button>
            </div>
          </section>
        ) : (
          <>
            <section style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 10, padding: "10px 12px", border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-secondary)" }}>
              {results.length ? (
                <>
                  <strong>총 {results.length.toLocaleString()}행</strong>
                  <button onClick={() => setStatusFilter("fail")} style={{ border: 0, background: "transparent", cursor: "pointer" }}><Pill tone="bad">FAIL {summary.fail.toLocaleString()}행</Pill></button>
                  <button onClick={() => setStatusFilter("warning")} style={{ border: 0, background: "transparent", cursor: "pointer" }}><Pill tone="warn">WARNING {summary.warning.toLocaleString()}행</Pill></button>
                  <button onClick={() => setStatusFilter("pass")} style={{ border: 0, background: "transparent", cursor: "pointer" }}><Pill tone="ok">PASS {summary.pass.toLocaleString()}행</Pill></button>
                  {statusFilter !== "all" && <Button size="sm" onClick={() => setStatusFilter("all")}>전체 보기</Button>}
                </>
              ) : (
                <span><strong>{table.rows.length.toLocaleString()}행 · {table.headers.length.toLocaleString()}열</strong>을 불러왔습니다. 톱니바퀴에서 조건을 설정하고 검사를 실행하세요.</span>
              )}
            </section>

            <section style={{ border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-secondary)", padding: "12px 14px", display: "grid", gap: 8 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <strong>적용 규칙</strong>
                <Pill tone="neutral">{appliedRules.length}개</Pill>
                {skippedRules.length > 0 && <Pill tone="warn">건너뜀 {skippedRules.length}개</Pill>}
              </div>
              <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                현재 표에 설정된 규칙 목록입니다. 각 규칙에 이상이 발생한 행에는 규칙에 지정한 FAIL 또는 WARNING이 표시됩니다.
              </div>
              {appliedRules.length ? (
                <div style={{ display: "grid" }}>
                  {appliedRules.map(({ rule, index }, position) => (
                    <div key={rule.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 2px", borderBottom: position < appliedRules.length - 1 ? "1px solid var(--border)" : "none" }}>
                      <b style={{ fontSize: 12, minWidth: 60, flexShrink: 0 }}>규칙 {index + 1}번</b>
                      <Pill tone={rule.severity === "fail" ? "bad" : "warn"}>{rule.severity.toUpperCase()}</Pill>
                      <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{ruleConditionText(rule)}</span>
                    </div>
                  ))}
                </div>
              ) : <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>현재 표에 있는 열 이름과 일치하는 규칙이 없습니다.</span>}
              {skippedRules.length > 0 && (
                <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.6 }}>
                  이번 표에 없는 열이라 건너뛴 규칙:{" "}
                  {skippedRules.map(({ rule, index }) =>
                    `규칙 ${index + 1}번(${missingRuleColumns(rule, table.headers).join(", ")})`).join(" · ")}
                </div>
              )}
            </section>

            {results.length > 0 && (
              <section style={{ border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-secondary)", padding: "12px 14px", display: "grid", gap: 6 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <strong>검사 결과 요약</strong>
                  {!inspectionSummary.loading && inspectionSummary.source === "gpt_oss_120b" && <Pill tone="ok">GPT OSS 120B</Pill>}
                  {!inspectionSummary.loading && inspectionSummary.source === "rule" && <Pill tone="neutral">규칙 요약</Pill>}
                </div>
                <div style={{ fontSize: 13, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
                  {inspectionSummary.loading ? "FAIL 및 WARNING 내용을 요약하고 있습니다..." : inspectionSummary.text}
                </div>
              </section>
            )}

            {results.length === 0 && appliedRules.length === 0 ? (
              <EmptyState
                title={skippedRules.length ? "이 표에 적용되는 규칙이 없습니다" : "검사 규칙이 없습니다"}
                description={skippedRules.length
                  ? "규칙에 지정한 열 이름이 이 표에 없습니다. 톱니바퀴에서 열 이름을 확인하세요."
                  : "왼쪽 아래 톱니바퀴에서 검사 조건을 추가하세요."}
              />
            ) : (
              <div style={{ overflow: "auto", maxHeight: "calc(100vh - 235px)", border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-secondary)" }}>
                <table style={{ borderCollapse: "separate", borderSpacing: 0, minWidth: "100%" }}>
                  <thead><tr>
                    <th style={{ ...tableCell, position: "sticky", top: 0, left: 0, zIndex: 3, minWidth: 220, background: "var(--bg-tertiary)", textAlign: "left" }}>검사 결과</th>
                    {table.headers.map((header) => <th key={header} style={{ ...tableCell, position: "sticky", top: 0, zIndex: 2, background: "var(--bg-tertiary)", textAlign: "left" }}>{header}</th>)}
                  </tr></thead>
                  <tbody>
                    {(results.length ? visibleRows : table.rows.slice(0, PAGE_SIZE).map((cells, rowIndex) => ({ cells, rowIndex, severity: "", detail: "" }))).map((row) => {
                      const rowBg = row.severity === "fail" ? "var(--danger-50, #fee2e2)" : row.severity === "warning" ? "var(--warn-50, #fef3c7)" : "transparent";
                      return <tr key={row.rowIndex} style={{ background: rowBg }}>
                        <td style={{ ...tableCell, position: "sticky", left: 0, zIndex: 1, whiteSpace: "normal", minWidth: 220, background: rowBg || "var(--bg-secondary)" }}>
                          {row.severity ? <><Pill tone={row.severity === "fail" ? "bad" : row.severity === "warning" ? "warn" : "ok"}>{row.severity.toUpperCase()}</Pill>{row.detail && <div style={{ marginTop: 4, color: "var(--text-primary)", lineHeight: 1.35 }}>{row.detail}</div>}</> : <span style={{ color: "var(--text-secondary)" }}>검사 전</span>}
                        </td>
                        {row.cells.map((value, index) => <td key={index} title={value} style={tableCell}>{value}</td>)}
                      </tr>;
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {results.length > PAGE_SIZE && <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}>
              <Button size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>이전</Button>
              <span style={{ fontSize: 13 }}>{page.toLocaleString()} / {pageCount.toLocaleString()} 페이지 · 현재 {filtered.length.toLocaleString()}행</span>
              <Button size="sm" disabled={page >= pageCount} onClick={() => setPage((p) => p + 1)}>다음</Button>
            </div>}
          </>
        )}
      </div>

      <PageGear title="DCOP 검사 조건" position="bottom-left">
        <RuleEditor settings={settings} setSettings={setSettings} headers={ruleHeaders} />
      </PageGear>
    </div>
  );
}
