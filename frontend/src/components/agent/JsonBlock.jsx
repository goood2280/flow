// v9.1.x: 에이전트/관리 화면 공용 JSON 표시 블록 (My_Diagnosis에서 추출).
export default function JsonBlock({ value, maxHeight = 160 }) {
  return (
    <pre style={{
      margin: 0,
      maxHeight,
      overflow: "auto",
      padding: 8,
      border: "1px solid var(--border)",
      background: "var(--bg-primary)",
      color: "var(--text-secondary)",
      fontSize: 12,
      lineHeight: 1.45,
      whiteSpace: "pre-wrap",
      wordBreak: "break-word",
    }}>
      {JSON.stringify(value ?? {}, null, 2)}
    </pre>
  );
}
