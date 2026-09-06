import assert from "node:assert/strict";
import { historyShareUrl, historyIdFromLocation, copyHistoryShareLink } from "../src/lib/historyShare.js";

globalThis.window = { location: { origin: "http://127.0.0.1:8080", search: "?history_id=RH-1234ABCD" } };
assert.equal(historyShareUrl("/reformatize", "RH-1234ABCD"), "http://127.0.0.1:8080/reformatize?history_id=RH-1234ABCD");
assert.equal(historyShareUrl("/reformatize", "RH-1234ABCD", "https://flow.example.com:8443/flow/"), "https://flow.example.com:8443/flow/reformatize?history_id=RH-1234ABCD");
assert.equal(historyIdFromLocation(/^RH-[0-9A-F]{8}$/i), "RH-1234ABCD");
assert.throws(() => historyShareUrl("/reformatize", "RH-1234ABCD", "javascript:alert(1)"));
assert.throws(() => historyShareUrl("/reformatize", "RH-1234ABCD", "https://user:pass@host"));
let copied;
Object.defineProperty(globalThis, "navigator", { configurable: true, value: { clipboard: { writeText: async value => { copied = value; } } } });
await copyHistoryShareLink("/reformatize", "RH-1234ABCD", "https://flow.example.com");
assert.equal(copied, "https://flow.example.com/reformatize?history_id=RH-1234ABCD");
console.log("history share: passed");
