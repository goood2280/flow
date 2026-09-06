import assert from "node:assert/strict";
import { responseDownloadFilename } from "../src/lib/api.js";

const filename = "SplitTable_20260906-131415_LOT-77_홍길동_KNOB_FAB.xlsx";
const response = { headers: new Headers({ "content-disposition": `attachment; filename="fallback.xlsx"; filename*=UTF-8''${encodeURIComponent(filename)}` }) };
assert.equal(responseDownloadFilename(response, "data.csv"), filename);
assert.equal(responseDownloadFilename({ headers: new Headers({ "content-disposition": 'attachment; filename="ET-download.csv"' }) }, "data.csv"), "ET-download.csv");
assert.equal(responseDownloadFilename({ headers: new Headers() }, "legacy.csv"), "legacy.csv");
assert.equal(responseDownloadFilename({ headers: new Headers({ "content-disposition": "attachment; filename*=UTF-8''%INVALID; filename=valid.csv" }) }), "valid.csv");
console.log("download response filenames: passed");
