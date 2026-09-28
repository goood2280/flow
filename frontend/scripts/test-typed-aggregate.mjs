import assert from "node:assert/strict";
import { parseTypedAggregate as parse } from "../src/features/filebrowser/typedAggregate.js";

const cols = ["root_lot_id", "wafer_id", "item_id", "value", "tkout_time", "step_id"];

// GROUP BY·집계 함수가 없으면 기존 SELECT/WHERE 경로 그대로.
assert.equal(parse("root_lot_id = 'A1000'", cols), null);
assert.equal(parse("SELECT root_lot_id, wafer_id WHERE item_id = 'VTH'", cols), null);

assert.deepEqual(
  parse("SELECT root_lot_id, wafer_id, AVG(value) WHERE item_id = 'VTH' GROUP BY root_lot_id, wafer_id", cols),
  { aggregate: { function: "avg", column: "value", group_by: ["root_lot_id", "wafer_id"], alias: "avg_value" }, serverSql: "item_id = 'VTH'" },
);
// 열 대소문자 맞춤 + ORDER BY 는 서버로 그대로.
assert.deepEqual(
  parse("select ROOT_LOT_ID, latest(TKOUT_TIME) group by root_lot_id order by root_lot_id desc", cols),
  { aggregate: { function: "latest", column: "tkout_time", group_by: ["root_lot_id"], alias: "latest_tkout_time" }, serverSql: "order by root_lot_id desc" },
);
// GROUP BY 없는 전체 집계, 문자열 안 GROUP BY 는 무시.
assert.deepEqual(parse("SELECT COUNT(*) WHERE item_id = 'GROUP BY'", cols).aggregate.group_by, []);
assert.equal(parse("SELECT COUNT(*) WHERE item_id = 'GROUP BY'", cols).serverSql, "item_id = 'GROUP BY'");

assert.match(parse("SELECT wafer_id, AVG(value), MAX(value) GROUP BY wafer_id", cols).error, /하나만/);
assert.match(parse("SELECT wafer_id, AVG(foo) GROUP BY wafer_id", cols).error, /찾을 수 없습니다/);
assert.match(parse("SELECT step_id, AVG(value) GROUP BY wafer_id", cols).error, /GROUP BY 에도/);
assert.match(parse("SELECT wafer_id, STDDEV(value) GROUP BY wafer_id", cols).error, /LATEST/);
assert.match(parse("item_id = 'VTH' GROUP BY wafer_id", cols).error, /SELECT/);
assert.match(parse("SELECT wafer_id, AVG(*) GROUP BY wafer_id", cols).error, /열 이름/);

console.log("typed aggregate ok");
