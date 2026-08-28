export function canonicalProductName(value) {
  return String(value || "").trim().replace(/^ML_TABLE_/i, "");
}

export function normalizeProductOrder(values) {
  const out = [];
  const seen = new Set();
  for (const value of Array.isArray(values) ? values : []) {
    const name = canonicalProductName(value);
    const key = name.toLocaleLowerCase();
    if (!name || seen.has(key)) continue;
    seen.add(key);
    out.push(name);
  }
  return out;
}

export function mergeProductOrder(products, order) {
  const names = normalizeProductOrder(products);
  const byKey = new Map(names.map((name) => [name.toLocaleLowerCase(), name]));
  const explicit = normalizeProductOrder(order)
    .map((name) => byKey.get(name.toLocaleLowerCase()))
    .filter(Boolean);
  const used = new Set(explicit.map((name) => name.toLocaleLowerCase()));
  const rest = names
    .filter((name) => !used.has(name.toLocaleLowerCase()))
    .sort((a, b) => a.localeCompare(b));
  return [...explicit, ...rest];
}

export function orderProductItems(items, order, getName = (item) => item) {
  const ranks = new Map(normalizeProductOrder(order).map((name, index) => [name.toLocaleLowerCase(), index]));
  return [...(items || [])].sort((a, b) => {
    const an = canonicalProductName(getName(a));
    const bn = canonicalProductName(getName(b));
    const ar = ranks.get(an.toLocaleLowerCase());
    const br = ranks.get(bn.toLocaleLowerCase());
    if (ar != null || br != null) {
      if (ar == null) return 1;
      if (br == null) return -1;
      return ar - br;
    }
    return an.localeCompare(bn);
  });
}
