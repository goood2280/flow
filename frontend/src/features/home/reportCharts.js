// Keep each saved chart's latest displayed revision; inline edits have their
// own message identity so a user can deliberately select either version.
export function reportChartChoices(messages = [], selected = []) {
  const charts = new Map(selected.map(chart => [chart.id, chart]));
  for (const message of messages) {
    const tool = message.response?.tool;
    if (message.error || message.response?.ok === false || tool?.feature !== "chart"
        || !tool.chart_result || !tool.definition_code?.trim()) continue;
    const id = tool.saved_chart?.id || message.id;
    charts.set(id, {
      id,
      name: tool.chart_result.title || tool.saved_chart?.name || `차트 ${charts.size + 1}`,
      definition_code: tool.definition_code,
    });
  }
  return [...charts.values()];
}

export function toggleReportChart(selected, chart) {
  if (selected.some(item => item.id === chart.id)) return selected.filter(item => item.id !== chart.id);
  return selected.length < 24 ? [...selected, chart] : selected;
}
