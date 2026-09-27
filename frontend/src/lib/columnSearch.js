// 컬럼 목록 검색 — SplitTable 커스텀 목록과 인폼 위저드 컬럼 첨부가 같은 규칙을 쓴다.
//   쉼표(, ，)로 검색어 여러 개 → 하나라도 맞으면 통과
//   `*` 또는 `%` → 아무 글자 0개 이상 ("QTIME*M3" 는 QTIME_A_M3, QTIME_M3 모두와 맞는다)
//   대소문자 무시, 앞뒤는 부분일치 (와일드카드가 없는 검색어는 예전처럼 포함 검색)
// `_` 는 컬럼 이름에 흔한 글자라 SQL LIKE 처럼 한 글자 와일드카드로 보지 않는다.
const escapeRegExp = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export function columnSearchTerms(query) {
  return String(query || "").split(/[,，]/).map(term => term.trim().toLowerCase()).filter(Boolean);
}

export function columnSearchMatcher(query) {
  const matchers = columnSearchTerms(query).map(term => {
    if (!/[*%]/.test(term)) return (text) => text.includes(term);
    const re = new RegExp(term.split(/[*%]+/).map(escapeRegExp).join(".*"));
    return (text) => re.test(text);
  });
  if (!matchers.length) return null;
  return (column) => {
    const text = String(column ?? "").toLowerCase();
    return matchers.some(match => match(text));
  };
}
