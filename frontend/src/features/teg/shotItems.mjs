const SHOT_LIGHT_PRIORITY = Object.freeze({ red: 0, orange: 1, yellow: 2, purple: 3, green: 4, gray: 5, dim: 6 });
const MAX_TOOLTIP_NAMES = 5;
const MAX_TOOLTIP_REASONS = 5;

function finiteCoordinate(value) {
  if (value === null || value === undefined || String(value).trim() === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function finitePositive(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : 0;
}

function normalizedCoordinate(value) {
  const number = finiteCoordinate(value);
  return number === 0 ? 0 : number;
}

function addBoundedUnique(group, value, setKey, valuesKey, limit) {
  const text = String(value ?? "").trim();
  if (!text || group[setKey].has(text)) return;
  group[setKey].add(text);
  if (group[valuesKey].length < limit) group[valuesKey].push(text);
}

/** Collapse only rows at the same normalized numeric coordinate. */
export function consolidateShotItems(items, keyPrefix = "item") {
  if (!Array.isArray(items) || items.length === 0) return [];
  const groups = new Map();
  for (const item of items) {
    const mmX = normalizedCoordinate(item?.mm_x);
    const mmY = normalizedCoordinate(item?.mm_y);
    if (mmX === null || mmY === null) continue;
    const key = `${mmX},${mmY}`;
    let group = groups.get(key);
    if (!group) {
      group = {
        key: `${keyPrefix}-${key}`,
        mm_x: mmX,
        mm_y: mmY,
        count: 0,
        representative: item,
        representativeRank: SHOT_LIGHT_PRIORITY[item.light] ?? 9,
        fallbackSize: null,
        fallbackFlat: "",
        nameSet: new Set(),
        names: [],
        reasonSet: new Set(),
        reasons: [],
      };
      groups.set(key, group);
    }
    group.count += 1;
    const rank = SHOT_LIGHT_PRIORITY[item.light] ?? 9;
    if (rank < group.representativeRank) {
      group.representative = item;
      group.representativeRank = rank;
    }
    if (!group.fallbackSize && finitePositive(item.w) > 0 && finitePositive(item.h) > 0) {
      group.fallbackSize = item;
    }
    if (!group.fallbackFlat && item.flat_used) group.fallbackFlat = item.flat_used;
    addBoundedUnique(group, item.name ?? item.teg, "nameSet", "names", MAX_TOOLTIP_NAMES);
    if (item.light === "red" || item.light === "orange" || item.light === "yellow") {
      addBoundedUnique(group, item.light_reason, "reasonSet", "reasons", MAX_TOOLTIP_REASONS);
    }
  }

  return Array.from(groups.values(), group => {
    const representative = group.representative;
    const count = group.count;
    const representativeName = String(representative.name ?? representative.teg ?? "").trim();
    // The worst-status row is the geometry/status representative, but legacy
    // MAIN anchor rows can have a blank name.  Do not let that blank hide the
    // named TEG rows consolidated at the same coordinate.
    const primaryName = representativeName || group.names[0] || "";
    const uniqueNameCount = group.nameSet.size;
    const uniqueReasonCount = group.reasonSet.size;
    const lines = [`${primaryName}${count > 1 ? ` (${count}건)` : ""}`];
    if (uniqueNameCount > 1) {
      lines.push(`포함 TEG (${uniqueNameCount}개): ${group.names.join(", ")}${uniqueNameCount > MAX_TOOLTIP_NAMES ? ` 외 ${uniqueNameCount - MAX_TOOLTIP_NAMES}개` : ""}`);
    }
    lines.push(`좌표: (${group.mm_x}, ${group.mm_y}) mm`);
    if (uniqueReasonCount > 0) {
      // A late red error must not disappear behind the first five warnings.
      const reason = String(representative.light_reason || "").trim();
      const shownReasons = [...new Set([reason, ...group.reasons].filter(Boolean))].slice(0, MAX_TOOLTIP_REASONS);
      lines.push(`상태: ${representative.light} (${shownReasons.join("; ")}${uniqueReasonCount > shownReasons.length ? ` 외 ${uniqueReasonCount - shownReasons.length}개` : ""})`);
    } else {
      lines.push(`상태: ${representative.light || "정상"}`);
    }
    const representativeWidth = finitePositive(representative.w);
    const representativeHeight = finitePositive(representative.h);
    const representativeHasSize = representativeWidth > 0 && representativeHeight > 0;
    const fallback = representativeHasSize ? representative : group.fallbackSize;
    return {
      ...representative,
      key: group.key,
      mm_x: group.mm_x,
      mm_y: group.mm_y,
      w: representativeHasSize ? representativeWidth : finitePositive(fallback?.w),
      h: representativeHasSize ? representativeHeight : finitePositive(fallback?.h),
      flat_used: representative.flat_used || group.fallbackFlat,
      name: primaryName,
      count,
      uniqueNames: Array.from(group.nameSet),
      tooltip: lines.join("\n"),
    };
  });
}

// Ownership is indexed before coordinate consolidation: two MAINs may share a
// position but have different errors. Never infer ownership from the worst row.
export function indexShotMains(rawItems, cells = []) {
  const groups = new Map();
  const ensure = name => {
    if (!groups.has(name)) groups.set(name, { name, rawItems: [], cells: [] });
    return groups.get(name);
  };
  const validCells = cells.filter(c => [c.x, c.y, c.w, c.h].every(v => finiteCoordinate(v) !== null)
    && Number(c.w) > 0 && Number(c.h) > 0);
  for (const cell of validCells) if (cell.name) ensure(String(cell.name).trim()).cells.push(cell);
  for (const item of rawItems) {
    const x = finiteCoordinate(item.mm_x), y = finiteCoordinate(item.mm_y);
    if (x === null || y === null) continue;
    const owner = String(item.group || item.main_group || "").trim();
    const names = new Set(owner ? owner.split(/,\s*/).filter(Boolean) : []);
    // Explicit MAIN ownership keeps out-of-bounds errors in their own MAIN.
    // Unowned S/L markers are relevant to every die they overlap.
    if (!owner) for (const cell of validCells) {
      if (cell.name && x <= Number(cell.x) + Number(cell.w) && x + finitePositive(item.w) >= Number(cell.x)
        && y <= Number(cell.y) + Number(cell.h) && y + finitePositive(item.h) >= Number(cell.y)) {
        names.add(String(cell.name).trim());
      }
    }
    for (const name of names) ensure(name).rawItems.push(item);
  }
  return Array.from(groups.values()).sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
}

/** Keep only markers that touch one of the selected MAIN rectangles.
 * The detail viewport clips partial overlaps at the MAIN boundary, so nearby
 * markers cannot widen or leak into the MAIN-only view. */
export function shotItemsInCells(items, cells = []) {
  const validCells = cells.filter(c => [c.x, c.y, c.w, c.h].every(v => finiteCoordinate(v) !== null)
    && Number(c.w) > 0 && Number(c.h) > 0);
  if (!validCells.length) return [];
  return (items || []).filter(item => {
    const x = finiteCoordinate(item?.mm_x), y = finiteCoordinate(item?.mm_y);
    if (x === null || y === null) return false;
    const right = x + finitePositive(item?.w), top = y + finitePositive(item?.h);
    return validCells.some(cell => {
      const cellX = Number(cell.x), cellY = Number(cell.y);
      const cellRight = cellX + Number(cell.w), cellTop = cellY + Number(cell.h);
      return x <= cellRight && right >= cellX && y <= cellTop && top >= cellY;
    });
  });
}

export function shotFocusBounds(items, cells = []) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const item of items) {
    const x = finiteCoordinate(item.mm_x), y = finiteCoordinate(item.mm_y);
    if (x === null || y === null) continue;
    minX = Math.min(minX, x); minY = Math.min(minY, y);
    maxX = Math.max(maxX, x + finitePositive(item.w)); maxY = Math.max(maxY, y + finitePositive(item.h));
  }
  for (const cell of cells) {
    const x = finiteCoordinate(cell.x), y = finiteCoordinate(cell.y);
    if (x === null || y === null) continue;
    minX = Math.min(minX, x); minY = Math.min(minY, y);
    maxX = Math.max(maxX, x + finitePositive(cell.w)); maxY = Math.max(maxY, y + finitePositive(cell.h));
  }
  if (!Number.isFinite(minX)) return null;
  return { centerX: (minX + maxX) / 2, centerY: (minY + maxY) / 2,
    width: Math.max(0.05, maxX - minX), height: Math.max(0.05, maxY - minY) };
}
