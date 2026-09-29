import { useCallback, useEffect, useState } from "react";
import { sf, postJson } from "../../lib/api";
import { toast } from "../../components/Toast";
import { Banner, Button, Card, Pill } from "../../components/UXKit";
import { setVisibleInterval } from "../../lib/visibleInterval";

// 매칭알람 · 제품위키 knob 칸.
// 제품위키에 등록된 knob 중 이름·코드·별칭·PPID 어느 것으로도 ppid_knob feature_name 과
// 맞지 않는 것(중단 knob 제외)을 제품별로 보여 주고, 추천 이름을 별칭으로 한 번에 등록한다.
// FAB 검사 목록과 별도 API 라 FAB 알람이 실패해도 이 칸은 따로 표시된다.

const API = "/api/valve-alerts/wiki-knobs";

export default function WikiKnobAlerts({ canManage, product = "" }) {
  const [data, setData] = useState(null);
  const [picked, setPicked] = useState({}); // `${product}|${knob_id}` -> alias
  const [busy, setBusy] = useState("");

  const load = useCallback(() => sf(API)
    .then((d) => {
      setData(d);
      // 추천 1순위를 기본 선택으로 둔다(이미 고른 값은 유지).
      setPicked((prev) => {
        const next = { ...prev };
        (d.products || []).forEach((p) => p.knobs.forEach((k) => {
          const key = `${p.product}|${k.knob_id}`;
          if (next[key] === undefined && k.suggestions?.length) next[key] = k.suggestions[0];
        }));
        return next;
      });
    })
    .catch((e) => setData({ ok: false, error: String(e.message || e), products: [], count: 0 })), []);

  useEffect(() => {
    load();
    return setVisibleInterval(load, 120000);
  }, [load]);

  const register = async (entry) => {
    const items = entry.knobs
      .map((k) => ({ knob_id: k.knob_id, alias: String(picked[`${entry.product}|${k.knob_id}`] || "").trim() }))
      .filter((i) => i.alias);
    if (!items.length) {
      toast.error("등록할 추천 이름이 없습니다.");
      return;
    }
    setBusy(entry.product);
    try {
      const out = await postJson(`${API}/aliases`, { product: entry.product, items });
      toast.ok(`${entry.product}: 별칭 ${out.added}건 등록`);
      await load();
    } catch (e) {
      toast.error(String(e.message || e));
    } finally {
      setBusy("");
    }
  };

  const products = (data?.products || []).filter((p) => !product || p.product.toLocaleLowerCase() === product.toLocaleLowerCase());
  const count = products.reduce((n, p) => n + p.knobs.length, 0);

  return (
    <Card
      id="valve-wiki-knob"
      className="ds-card--section"
      title="제품위키 knob"
      right={<Pill tone={count ? "danger" : "neutral"}>ppid_knob 미등록 knob · {count}건</Pill>}
    >
      <p className="ds-section-desc">
        제품위키의 knob 이름·코드·별칭을 ppid_knob feature_name과 비교합니다(앞 순번·KNOB_ 접두어·공백·기호·대소문자 무시).
        step PPID로도 찾지 못한 knob만 표시하며 중단 상태는 제외합니다. 추천 이름을 골라 별칭으로 등록하면 다음 검사부터 인식됩니다.
      </p>
      {data && data.ok === false && <Banner tone="danger"><b>제품위키 knob 확인 실패</b> {data.error}</Banner>}
      {(data?.errors || []).map((e) => <Banner key={e.product} tone="warn">{e.product}: {e.error}</Banner>)}
      {data && data.ok !== false && !count && <p className="ds-section-desc">미등록 knob이 없습니다.</p>}
      {products.map((entry) => (
        <div key={entry.product} style={{ border: "1px solid var(--border)", borderRadius: 4, padding: "8px 10px", marginTop: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
            <b>{entry.product}</b>
            <Pill tone="danger">{entry.knobs.length}건</Pill>
            <span style={{ flex: 1 }} />
            <Button size="sm" variant="primary" disabled={!canManage || !!busy} onClick={() => register(entry)}
              title={canManage ? "선택한 추천 이름을 각 knob의 별칭으로 한 번에 등록합니다" : "매칭알람 관리자만 등록할 수 있습니다"}>
              {busy === entry.product ? "등록 중…" : "추천 이름 별칭으로 등록"}
            </Button>
          </div>
          <table className="valve-wiki-knob-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--text-secondary)" }}>
                <th style={{ padding: "4px 6px" }}>knob</th>
                <th style={{ padding: "4px 6px" }}>코드</th>
                <th style={{ padding: "4px 6px" }}>소구조물</th>
                <th style={{ padding: "4px 6px" }}>현재 별칭</th>
                <th style={{ padding: "4px 6px" }}>추천 feature 이름 → 별칭</th>
              </tr>
            </thead>
            <tbody>
              {entry.knobs.map((k) => {
                const key = `${entry.product}|${k.knob_id}`;
                return (
                  <tr key={k.knob_id} style={{ borderTop: "1px solid var(--border)" }}>
                    <td style={{ padding: "4px 6px", fontWeight: 700 }}>{k.name}</td>
                    <td style={{ padding: "4px 6px", fontFamily: "monospace" }}>{k.code || "-"}</td>
                    <td style={{ padding: "4px 6px" }}>{k.structure || "-"}</td>
                    <td style={{ padding: "4px 6px" }}>{(k.aliases || []).join(", ") || "-"}</td>
                    <td style={{ padding: "4px 6px" }}>
                      {k.suggestions?.length ? (
                        <select value={picked[key] ?? ""} disabled={!canManage}
                          onChange={(e) => setPicked((prev) => ({ ...prev, [key]: e.target.value }))}>
                          <option value="">등록 안 함</option>
                          {k.suggestions.map((s) => <option key={s} value={s}>{s}</option>)}
                        </select>
                      ) : <span style={{ color: "var(--text-secondary)" }}>비슷한 이름 없음 — ppid_knob에 feature 추가 필요</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ))}
    </Card>
  );
}
