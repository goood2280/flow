import { useState } from "react";
import { Icon } from "../../components/ui/Icon";

// 파일설정 > 정렬로직 편집기.
// 값은 기존 form 과 같은 "한 줄 = 정렬 키" 텍스트로 주고받는다(parse/format 은 부모가 준다).
// 위에서부터 1순위, 2순위 … 로 적용된다.

export const SORT_TYPE_OPTIONS = [
  ["string", "문자열"],
  ["numeric", "숫자로 변환"],
  ["date", "날짜/시간으로 변환"],
  ["leading_number", "앞 숫자 (3.0 VTN)"],
  ["natural", "자연 정렬 (A2 < A10)"],
  ["rule_order", "rule_order (R1…RO)"],
  ["custom", "직접 순서 지정"],
];

const cellStyle = {
  padding: "5px 7px", borderRadius: 4, border: "1px solid var(--border)",
  background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, minWidth: 0,
};
const iconBtn = {
  padding: "3px 7px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent",
  color: "var(--text-secondary)", fontSize: 12, cursor: "pointer",
};

export default function SortRuleEditor({ value, onChange, columns = [], parseLines, formatLines, listId = "fb-sort-rule-columns", placeholder = "" }) {
  const [textMode, setTextMode] = useState(false);
  // 편집 중 컬럼 칸을 비워도 행이 사라지지 않게 로컬 행을 두고, 바깥 값(LLM 초안 적용 등)이
  // 바뀌었을 때만 다시 읽는다.
  const [local, setLocal] = useState(() => ({ text: value || "", specs: parseLines(value) }));
  const specs = local.text === (value || "") ? local.specs : parseLines(value);
  const commit = (next) => {
    const text = formatLines(next.filter((s) => String(s.column || "").trim()));
    setLocal({ text, specs: next });
    onChange(text);
  };
  const update = (idx, patch) => commit(specs.map((s, i) => (i === idx ? { ...s, ...patch } : s)));
  const move = (idx, delta) => {
    const target = idx + delta;
    if (target < 0 || target >= specs.length) return;
    const next = [...specs];
    [next[idx], next[target]] = [next[target], next[idx]];
    commit(next);
  };
  const remove = (idx) => commit(specs.filter((_, i) => i !== idx));
  const add = () => {
    const used = new Set(specs.map((s) => String(s.column).toLowerCase()));
    const column = columns.find((c) => !used.has(String(c).toLowerCase())) || "column";
    commit([...specs, { column, direction: "asc", type: "string", nulls: "last" }]);
  };

  return (
    <div style={{ display: "grid", gap: 6 }}>
      <datalist id={listId}>{columns.map((c) => <option key={c} value={c} />)}</datalist>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: "var(--text-secondary)" }}>저장 정렬 키 (위가 1순위)</span>
        <button type="button" onClick={() => setTextMode((v) => !v)} style={iconBtn}>{textMode ? "표로 편집" : "텍스트로 편집"}</button>
      </div>
      {textMode ? (
        <textarea value={value || ""} onChange={(e) => onChange(e.target.value)} rows={4} spellCheck={false} placeholder={placeholder}
          style={{ ...cellStyle, width: "100%", boxSizing: "border-box", resize: "vertical", fontSize: 13, fontFamily: "monospace", lineHeight: 1.45 }} />
      ) : (
        <div style={{ display: "grid", gap: 6 }}>
          {!specs.length && <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>정렬 키가 없습니다. 저장 시 행 순서를 바꾸지 않습니다.</div>}
          {specs.map((s, idx) => {
            const extra = s.type === "custom" || s.type === "string";
            return (
              <div key={idx} style={{ display: "grid", gap: 5, padding: 7, border: "1px solid var(--border)", borderRadius: 4, background: "var(--bg-primary)" }}>
                <div style={{ display: "grid", gridTemplateColumns: "22px minmax(120px,1.4fr) minmax(88px,0.8fr) minmax(130px,1.2fr) minmax(96px,0.8fr) auto", gap: 5, alignItems: "center" }}>
                  <span style={{ fontSize: 12, fontWeight: 800, color: "var(--text-secondary)", textAlign: "center" }}>{idx + 1}</span>
                  <input list={listId} value={s.column} onChange={(e) => update(idx, { column: e.target.value.replace(/\s/g, "") })} title="정렬할 컬럼" style={{ ...cellStyle, fontFamily: "monospace" }} />
                  <select value={s.direction === "desc" ? "desc" : "asc"} onChange={(e) => update(idx, { direction: e.target.value })} style={cellStyle}>
                    <option value="asc">오름차순</option><option value="desc">내림차순</option>
                  </select>
                  <select value={s.type || "string"} onChange={(e) => update(idx, { type: e.target.value, ...(e.target.value === "custom" && !(s.values || []).length ? { values: [] } : {}) })} style={cellStyle}>
                    {SORT_TYPE_OPTIONS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
                  </select>
                  <select value={s.nulls === "first" ? "first" : "last"} onChange={(e) => update(idx, { nulls: e.target.value })} style={cellStyle}>
                    <option value="last">빈 값 맨 뒤</option><option value="first">빈 값 맨 앞</option>
                  </select>
                  <span style={{ display: "flex", gap: 3 }}>
                    <button type="button" onClick={() => move(idx, -1)} disabled={idx === 0} title="우선순위 올리기" style={{ ...iconBtn, opacity: idx === 0 ? 0.4 : 1 }}>↑</button>
                    <button type="button" onClick={() => move(idx, 1)} disabled={idx === specs.length - 1} title="우선순위 내리기" style={{ ...iconBtn, opacity: idx === specs.length - 1 ? 0.4 : 1 }}>↓</button>
                    <button type="button" onClick={() => remove(idx)} title="이 정렬 키 삭제" style={iconBtn}><Icon name="close" /></button>
                  </span>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: s.type === "custom" ? "22px minmax(160px,1.6fr) minmax(140px,1fr) auto" : "22px minmax(140px,1fr) auto", gap: 5, alignItems: "center" }}>
                  <span />
                  {s.type === "custom" && (
                    <input value={(s.values || []).join("|")} onChange={(e) => update(idx, { values: e.target.value.split("|").map((v) => v.trim()).filter(Boolean) })}
                      placeholder="순서대로 값 입력: RUN|HOLD|DONE (목록 밖 값은 뒤로)" style={{ ...cellStyle, fontFamily: "monospace" }} />
                  )}
                  <input value={s.pattern || ""} onChange={(e) => update(idx, { pattern: e.target.value.replace(/\s/g, "") || undefined })}
                    placeholder="추출 정규식(선택): _(\d+)$ → 괄호 부분만 비교" title="값에서 정규식으로 일부만 떼어 비교합니다. 괄호(캡처 그룹)가 있으면 그 부분, 없으면 일치한 전체." style={{ ...cellStyle, fontFamily: "monospace" }} />
                  {extra ? (
                    <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12, color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
                      <input type="checkbox" checked={s.case === "insensitive"} onChange={(e) => update(idx, { case: e.target.checked ? "insensitive" : undefined })} />
                      대소문자 무시
                    </label>
                  ) : <span />}
                </div>
              </div>
            );
          })}
          <div><button type="button" onClick={add} style={{ ...iconBtn, color: "var(--accent)", borderColor: "var(--accent)", fontWeight: 700 }}>+ 정렬 키 추가</button></div>
        </div>
      )}
    </div>
  );
}
