import assert from 'node:assert/strict';
import { reportChartChoices, toggleReportChart } from '../src/features/home/reportCharts.js';

const message = (id, savedId, code, title = id) => ({id, response:{tool:{feature:'chart', saved_chart:{id:savedId}, definition_code:code, chart_result:{title}}}});
const messages = [message('first','saved','old'), message('edited','saved','new'), message('inline','','inline-code')];
const choices = reportChartChoices(messages);
assert.deepEqual(choices.map(c=>c.definition_code), ['new','inline-code']);
let selected = toggleReportChart([],choices[1]);
selected = toggleReportChart(selected,choices[0]);
assert.deepEqual(selected.map(c=>c.id),['inline','saved']);
assert.deepEqual(toggleReportChart(selected,choices[1]).map(c=>c.id),['saved']);
assert.equal(reportChartChoices([{...messages[0],error:true}]).length,0);
assert.equal(reportChartChoices([{id:'report',response:{tool:{feature:'report.template',definition_code:'code',chart_result:{}}}}]).length,0);
assert.deepEqual(reportChartChoices([],selected),selected);
const limit = Array.from({length:24},(_,id)=>({id:String(id)}));
assert.equal(toggleReportChart(limit,{id:'overflow'}).length,24);
console.log('Home report chart selection: revision, ordering, restore and limits passed');
