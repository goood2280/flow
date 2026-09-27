import { useEffect, useState } from "react";
import { sf } from "../../lib/api";
import { featureInfo } from "./HomeDataChat";

const KIND_LABEL = { chart: "차트", table: "표", report: "리포트", download: "다운로드" };

function relativeTime(epochSeconds) {
  const seconds = Math.max(0, Date.now() / 1000 - Number(epochSeconds || 0));
  if (seconds < 60) return "방금";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분 전`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}시간 전`;
  if (seconds < 7 * 86400) return `${Math.floor(seconds / 86400)}일 전`;
  return new Date(Number(epochSeconds) * 1000).toLocaleDateString();
}

/* Home landing list of work finished through Flow-i. Selecting one opens the
   chat on that conversation with its result pane restored. */
export default function HomeRecentResults({ onOpen }) {
  const [rows, setRows] = useState(null);

  useEffect(() => {
    let alive = true;
    sf("/api/home-agent/recent-results?limit=6")
      .then((data) => { if (alive) setRows(Array.isArray(data?.results) ? data.results : []); })
      .catch(() => { if (alive) setRows([]); });
    return () => { alive = false; };
  }, []);

  if (!rows?.length) return null;
  return (
    <section className="home-results" aria-labelledby="home-results-title">
      <div className="home-results__head">
        <h2 id="home-results-title">최근 작업 결과</h2>
      </div>
      <div className="home-results__grid">
        {rows.map((row) => {
          const info = featureInfo(row.feature, { action: row.action, context: { product: row.product, root_lot_id: row.lot } });
          const scope = [row.product, row.lot].filter(Boolean).join(" · ");
          return (
            <button
              type="button"
              key={`${row.conversation_id}:${row.message_id}`}
              className="home-result-card"
              data-kind={row.kind}
              onClick={() => onOpen(row)}
              title={row.question || row.title}
            >
              <span className="home-result-card__top">
                <span className="home-result-card__kind">{KIND_LABEL[row.kind] || "결과"}</span>
                <span className="home-result-card__time">{relativeTime(row.created_at)}</span>
              </span>
              <strong className="home-result-card__title">{row.title || info.title}</strong>
              {row.question && <span className="home-result-card__question">{row.question}</span>}
              {scope && <span className="home-result-card__scope">{scope}</span>}
            </button>
          );
        })}
      </div>
    </section>
  );
}
