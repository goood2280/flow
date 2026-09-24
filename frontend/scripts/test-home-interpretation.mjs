import assert from "node:assert/strict";
import { createServer } from "vite";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const server = await createServer({
  server: { middlewareMode: true },
  optimizeDeps: { noDiscovery: true, entries: [], include: [] },
  appType: "custom", logLevel: "error",
});
try {
  const { default: Panel, hitlSections } = await server.ssrLoadModule("/src/features/home/InterpretationPanel.jsx");
  const response = { tool: { missing: ["semantic_target"], table: { rows: [
    { "번호": 1, module: "PHOTO", term: "PC", step_id: "A100", item_id: "I1" },
    { "번호": 2, module: "ETCH", term: "PC", step_id: "A200", item_id: "I2" },
  ] } } };
  assert.deepEqual(hitlSections(response)[0].options.map(o => o.value), ["1", "2"]);
  const markup = renderToStaticMarkup(React.createElement(Panel, { response, onSubmit: () => {} }));
  assert.match(markup, /PHOTO/);
  assert.match(markup, /ETCH/);
  const evidenceMarkup = renderToStaticMarkup(React.createElement(Panel, { response: {
    evidence: { steps: [{ tool: "splittable", status: "completed", sources: ["actual.parquet"],
      targets: { product: "REAL_ALPHA" }, query: { sql: "SELECT count(*) FROM source" } }] },
  } }));
  assert.match(evidenceMarkup, /actual.parquet/);
  assert.match(evidenceMarkup, /REAL_ALPHA/);
  assert.match(evidenceMarkup, /SELECT count/);
  const detailed = renderToStaticMarkup(React.createElement(Panel, { response: {
    interpretation: {summary: "PC CD의 SITE 원본을 tkout_time으로 봅니다.", status: "needs_input",
      origin: "실행 계획", details: [{label: "데이터 종류", value: "INLINE <raw>"}], unresolved: ["AAA KNOB 선택 필요"]},
  } }));
  assert.match(detailed, /이렇게 이해했어요/);
  assert.match(detailed, /PC CD의 SITE/);
  assert.match(detailed, /INLINE &lt;raw&gt;/);
  assert.match(detailed, /AAA KNOB 선택 필요/);
  assert.match(detailed, /<details/);
  console.log("Home interpretation: semantic choices and active response evidence passed");
} finally {
  await server.close();
}
