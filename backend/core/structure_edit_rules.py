"""Deterministic reading of common Korean 3D structure edit requests.

The administrator LLM editor runs these rules first so that frequent requests
(MOL 단 수, epi MOL 직결, 재료 채움 순서, epi 식각, inner gate 층별 치수,
Inline 위치 표시) work the same with or without a connected model. Anything the
rules cannot read is left for the LLM; the rules never invent numbers.
"""
from __future__ import annotations

import re

MODE_LABELS = {"bottom": "아래", "liner": "측벽", "u_liner": "U자", "fill": "나머지 채움"}
SIZE_LABELS = {"thin": "얇게", "thick": "두껍게"}
REGION_LABELS = {"gate": "상부 게이트", "sd_contact": "epi MOL 콘택트", "gate_contact": "게이트 콘택트"}
KOREAN_MATERIALS = {"텅스텐": "W", "코발트": "Co", "루테늄": "Ru", "구리": "Cu", "몰리브덴": "Mo",
                    "알루미늄": "Al", "티타늄": "Ti", "산화막": "SiO2", "질화막": "SiN"}
NUMBER = r"(\d+(?:\.\d+)?)"

_INNER = re.compile(r"inner\s*[-_]?\s*gate|이너\s*게이트|inner\s*게이트|내부\s*게이트|(?<![A-Za-z])ig\s*\d", re.I)
_GATE_CONTACT = re.compile(r"(?:gate|게이트)\s*(?:쪽\s*)?(?:mol|콘택트?|컨택트?|contact)", re.I)
_MOL = re.compile(r"mol|콘택|컨택|contact", re.I)
_GATE = re.compile(r"gate|게이트", re.I)
_EPI = re.compile(r"epi|에피|s/d", re.I)

_SPLIT = re.compile(r"(?<=[줘고])\s+|\s*\n\s*|(?<!\d)\.(?!\d)|;|"
                    r",\s*(?=[가-힣]|(?i:ns|mol|gate|epi|inner|nanosheet|sheet|contact)(?![a-z]))")
_NUMBERING = re.compile(r"^\s*\d+\s*[.,)]\s*(?=\D)")
_MASK = re.compile(
    r"inner\s*[-_]?\s*gate|high\s*-?\s*k|s/d|u\s*자형?|u\s*-?\s*shape|\d+(?:\.\d+)?\s*nm|"
    r"(?<![A-Za-z0-9])(?:mol|gate|epi|beol|feol|inline|parameter|param|tcd|mcd|bcd|ns\d?|nm|m[0-4]|v[0-4]|cd|sdb|rx|"
    r"width|height|pad|via|contact|step|item|id|liner|fill|bottom|side|top|layer)(?![A-Za-z0-9])",
    re.I)
_STOP_LOWER = {"and", "or", "to", "the", "a", "an", "of", "in", "on", "with", "is", "it"}
_TOKEN = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]{0,15})(?![A-Za-z0-9])")


def _targets(text):
    rest, found = text, set()
    for name, pattern in (("inner_gate", _INNER), ("gate_contact", _GATE_CONTACT), ("mol", _MOL),
                          ("gate", _GATE), ("epi", _EPI)):
        if pattern.search(rest):
            found.add(name)
            rest = pattern.sub(" ", rest)
    return found


def _phrases(instruction):
    items = []
    for line in re.split(r"\n+", str(instruction or "")):
        line = _NUMBERING.sub("", line).strip()
        if line:
            items.append([part.strip() for part in _SPLIT.split(line) if part and part.strip()])
    return items


def _materials(text):
    masked = _MASK.sub(" ", text)
    names = []
    for match in re.finditer(r"물질\s*([A-Za-z0-9가-힣]{1,12})|" + _TOKEN.pattern, masked):
        name = match.group(1) or match.group(2)
        if not name or (name.islower() and name in _STOP_LOWER):
            continue
        names.append(name)
    for korean, symbol in KOREAN_MATERIALS.items():
        if korean in text:
            names.append(symbol)
    return list(dict.fromkeys(names))


def _mode(text):
    if re.search(r"u\s*자|u\s*-?\s*shape|유자|컨포멀|conformal", text, re.I):
        return "u_liner"
    if re.search(r"사이드|측벽|측면|옆면|양옆|옆에|옆으로|벽면|side\s*wall|sidewall|(?<![A-Za-z])side(?![A-Za-z])", text, re.I):
        return "liner"
    if re.search(r"라이너|liner", text, re.I):
        return "u_liner"
    if re.search(r"(?:밑|아래|바닥|하부)(?:에서)?\s*부터|bottom\s*up", text, re.I):
        return "bottom"
    if re.search(r"나머지|남은|그\s*위|위는|위를|위에|가운데|중앙|코어|core|remain", text, re.I):
        return "fill"
    if re.search(r"밑|아래|바닥|하부|bottom", text, re.I):
        return "bottom"
    if re.search(r"채워|채우|fill", text, re.I):
        return "fill"
    return None


def _size(text):
    if re.search(r"얇게|얇은|thin|살짝|약간|조금|slightly", text, re.I):
        return "thin"
    if re.search(r"두껍게|두꺼운|thick|많이", text, re.I):
        return "thick"
    return None


def _numbers_after(text, keywords):
    match = re.search(rf"(?:{keywords})\s*(?:은|는|을|를|:|=|값)?\s*((?:\d+(?:\.\d+)?\s*(?:nm)?\s*[,/·]\s*)*\d+(?:\.\d+)?)",
                      text, re.I)
    return [float(value) for value in re.findall(NUMBER, match.group(1))] if match else []


def _inner_gate(text, sheet_count):
    """Return ({param: value}, needs_values) for one phrase about inner gates."""
    edits = {}
    width_words, height_words = r"width|폭|너비|가로|길이", r"height|높이|세로|두께"
    chunks = re.split(r"(?=(?:inner\s*[-_]?\s*gate|이너\s*게이트|(?<![A-Za-z])ig)\s*\d(?!\s*[,~\-/]\s*\d))", text, flags=re.I)
    for chunk in chunks:
        listed = re.search(r"(?:inner\s*[-_]?\s*gate|이너\s*게이트|(?<![A-Za-z])ig)\s*(\d(?:\s*[,~\-/와과및]\s*\d)*)", chunk, re.I)
        if listed:
            spec = listed.group(1)
            if re.fullmatch(r"\d\s*[~\-]\s*\d", spec.strip()):
                first, last = (int(value) for value in re.findall(r"\d", spec))
                indices = list(range(first, last + 1))
            else:
                indices = [int(value) for value in re.findall(r"\d", spec)]
        else:
            indices = list(range(1, sheet_count + 1)) if _INNER.search(chunk) else []
        indices = [index for index in indices if 1 <= index <= 5]
        if not indices:
            continue
        after = chunk[listed.end():] if listed else chunk
        for field, words in (("width", width_words), ("height", height_words)):
            values = _numbers_after(after, words)
            if not values:
                continue
            if len(values) == 1:
                values = values * len(indices)
            if len(values) != len(indices):
                return edits, True
            for index, value in zip(indices, values):
                edits[f"inner_gate{index}_{field}_nm"] = value
    return edits, not edits


def parse(instruction, *, sheet_count=3):
    """Read an admin instruction into edits, material recipes and display switches."""
    params, stacks, display, notes = {}, {}, {}, []
    recognized, unparsed, needs_values = [], [], []
    for item in _phrases(instruction):
        inherited = set()
        for phrase in item:
            targets = _targets(phrase) or inherited
            if _targets(phrase):
                inherited = targets
            hit = False
            lowered = phrase.casefold()
            # 6. Display switches for the 3D overlays.
            if re.search(r"inline|인라인", phrase, re.I) and re.search(r"표시|보여|보이게|위치|켜|display|show", phrase, re.I):
                display["inline_parameters"] = not re.search(r"숨|끄|꺼|off|hide|안\s*보", phrase, re.I)
                hit = True
            elif re.search(r"이름|라벨|label", phrase, re.I) and re.search(r"표시|보여|보이게|켜|숨|끄|꺼|show|hide", phrase, re.I):
                visible = not re.search(r"숨|끄|꺼|off|hide|안\s*보", phrase, re.I)
                key = "part_labels" if re.search(r"소\s*구조|하위|세부|sub|재료|물질", phrase, re.I) else "structure_labels"
                display[key] = visible
                hit = True
            if hit:
                recognized.append(phrase)
                continue
            # 5. Inner gate per-level width/height.
            if "inner_gate" in targets and _INNER.search(phrase + " " + " ".join(item)):
                values, missing = _inner_gate(phrase, sheet_count)
                if values:
                    params.update(values)
                    hit = True
                elif re.search(r"width|폭|너비|가로|height|높이|세로|두께|어떻게", phrase, re.I):
                    needs_values.append("Inner gate 층별 폭·높이 값(nm)을 적어 주세요. 예: inner gate 1,2,3 각각 "
                                        "width 30,28,26 nm height 12,10,8 nm")
                    hit = True
                if hit:
                    recognized.append(phrase)
                    continue
            mol_like = bool(targets & {"mol", "gate_contact"}) or ("epi" in targets and re.search(r"단", phrase))
            # 1. MOL 단 수.
            levels = re.findall(r"(\d)\s*(?:단|층|level|레벨)\s*(으로|로)?", phrase, re.I)
            if mol_like and levels and not re.search(r"시트|nanosheet|(?<![A-Za-z])ns(?![A-Za-z])", phrase, re.I):
                chosen = [value for value, towards in levels if towards] or [levels[-1][0]]
                value = int(chosen[-1])
                if 1 <= value <= 4:
                    params["gate_mol_level_count" if "gate_contact" in targets and "epi" not in targets
                           else "mol_level_count"] = value
                    hit = True
            # 2. epi MOL 네모(landing pad) 제거 → 바로 연결.
            if targets & {"mol", "epi"}:
                if (re.search(r"네모|사각|박스|box|패드|pad|랜딩|landing|구분", phrase, re.I)
                        and re.search(r"없애|없이|제거|빼|지워|삭제|remove", phrase, re.I)) or \
                        re.search(r"(?:바로|직접|곧바로)\s*(?:연결|붙|이어)|직결", phrase):
                    params["mol_sd_landing_pad"] = 0
                    hit = True
                elif re.search(r"네모|패드|pad", phrase, re.I) and re.search(r"다시|살려|추가|있게|넣어", phrase):
                    params["mol_sd_landing_pad"] = 1
                    hit = True
            # 4. epi 위를 깎는 contact recess.
            if targets & {"epi", "mol"} and re.search(r"깎|식각|리세스|recess|파고|파\s*들어|파줘|etch|들어가게|박히게|박아", phrase, re.I):
                number = re.search(NUMBER + r"\s*(?:nm|나노)?", phrase)
                if number and not re.search(r"\d\s*단", phrase):
                    params["contact_epi_recess_nm"] = float(number.group(1))
                elif re.search(r"많이|깊게|깊이", phrase):
                    params["contact_epi_recess_nm"] = 10.0
                    notes.append("epi 식각 깊이를 10 nm로 가정했습니다. 원하는 값(nm)을 적으면 그 값으로 바꿉니다.")
                elif re.search(r"조금|약간|살짝", phrase):
                    params["contact_epi_recess_nm"] = 3.0
                    notes.append("epi 식각 깊이를 3 nm로 가정했습니다. 원하는 값(nm)을 적으면 그 값으로 바꿉니다.")
                else:
                    params["contact_epi_recess_nm"] = 5.0
                    notes.append("epi 식각 깊이가 없어 5 nm로 가정했습니다. 원하는 값(nm)을 적으면 그 값으로 바꿉니다.")
                hit = True
            # 3. Material fill recipes.
            if not hit:
                names = _materials(phrase)
                mode = _mode(phrase)
                if names and mode is None and re.search(r"넣어|넣고|채워|채우", phrase):
                    mode = "u_liner"
                region = ("gate_contact" if "gate_contact" in targets else "sd_contact" if "mol" in targets
                          else "gate" if "gate" in targets else None)
                if names and mode and region:
                    size = _size(phrase)
                    explicit = re.search(NUMBER + r"\s*nm", phrase, re.I)
                    for name in names:
                        layer = {"material": name, "mode": mode}
                        if explicit and mode != "fill":
                            layer["thickness_nm"] = float(explicit.group(1))
                        elif size and mode != "fill":
                            layer["size"] = size
                        stacks.setdefault(region, []).append(layer)
                    hit = True
            if hit:
                recognized.append(phrase)
            elif targets or re.search(r"[A-Za-z0-9]", phrase):
                unparsed.append(phrase)
    for region, layers in stacks.items():
        fills = [layer for layer in layers if layer["mode"] == "fill"]
        stacks[region] = [layer for layer in layers if layer["mode"] != "fill"] + fills[-1:]
    edits = [{"kind": "parameter", "name": name, "role": "", "value": value} for name, value in params.items()]
    return {"edits": edits, "material_stacks": stacks, "display": display, "notes": notes,
            "needs_values": list(dict.fromkeys(needs_values)), "recognized": recognized, "unparsed": unparsed,
            "summary": summarize(edits, stacks, display)}


def has_changes(result):
    return bool(result.get("edits") or result.get("material_stacks") or result.get("display"))


def summarize(edits, stacks, display):
    parts = []
    names = {"mol_level_count": "epi MOL {}단", "gate_mol_level_count": "게이트 MOL {}단",
             "contact_epi_recess_nm": "epi 식각 {:g} nm"}
    for edit in edits:
        name, value = edit["name"], edit["value"]
        if name == "mol_sd_landing_pad":
            parts.append("epi MOL 네모 패드 제거·바로 연결" if not value else "epi MOL 패드 복원")
        elif name in names:
            parts.append(names[name].format(int(value) if name.endswith("count") else value))
        elif name.startswith("inner_gate"):
            match = re.match(r"inner_gate(\d)_(width|height)_nm", name)
            parts.append(f"Inner gate {match.group(1)} {'폭' if match.group(2) == 'width' else '높이'} {value:g} nm")
        else:
            parts.append(f"{name} {value:g}")
    for region, layers in stacks.items():
        recipe = " → ".join(f"{layer['material']}({MODE_LABELS[layer['mode']]}"
                            f"{', ' + SIZE_LABELS[layer['size']] if layer.get('size') in SIZE_LABELS else ''}"
                            f"{', ' + format(layer['thickness_nm'], 'g') + ' nm' if layer.get('thickness_nm') else ''})"
                            for layer in layers)
        parts.append(f"{REGION_LABELS[region]}: {recipe}")
    for key, value in display.items():
        label = {"inline_parameters": "Inline parameter 위치", "part_labels": "소구조물 이름",
                 "structure_labels": "구조 이름"}[key]
        parts.append(f"{label} {'표시' if value else '숨김'}")
    return " · ".join(parts)[:300]
