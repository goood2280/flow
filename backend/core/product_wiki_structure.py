"""Product structure overlays and read-only Vehicle_matching references."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import uuid
from pathlib import Path

from core import product_wiki as wiki


def mapping_source(product):
    from core.fab_matching_alerts import _vehicle_row_matches
    root = getattr(wiki.PATHS, 'db_root', None)
    result = {'rows': [], 'fingerprint': '', 'warning': '', 'source': 'Vehicle_matching.csv'}
    path = Path(root) / 'Vehicle_matching.csv' if root else None
    if not path or not path.is_file():
        return dict(result, warning='Vehicle_matching.csv를 찾을 수 없습니다. 구조는 직접 작성할 수 있습니다.')
    try:
        with path.open(encoding='utf-8-sig', newline='') as file:
            reader = csv.DictReader(file)
            columns = {str(k).strip().casefold() for k in reader.fieldnames or []}
            if 'step_id' not in columns or not columns.intersection({'product', 'vehicle', 'mask'}):
                return dict(result, warning='매칭 파일에 제품 범위 또는 step_id 열이 없습니다.')
            rows, seen = [], set()
            for raw in reader:
                row = {str(k).strip().casefold(): str(v or '').strip() for k, v in raw.items() if k is not None}
                if not _vehicle_row_matches(row, wiki.product_name(product)) or not row.get('step_id'):
                    continue
                item = {k: row.get(k, '') for k in ('module', 'step_id', 'step_desc')}
                key = tuple(item.values())
                if key not in seen:
                    rows.append(item)
                    seen.add(key)
            # Sort for display without changing the source spelling. Numeric
            # portions therefore sort naturally (A2 before A10) and IDs such
            # as 001200 retain their leading zeroes in the response.
            rows.sort(key=lambda r: (r['module'].casefold(), _natural_step_key(r['step_id']),
                                     r['step_id'].casefold(), r['step_desc'].casefold()))
            result['rows'] = rows
            result['fingerprint'] = hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            warnings = []
            if 'module' not in columns and rows:
                warnings.append('매칭 파일에 module 열이 없어 모듈 미지정으로 표시합니다.')
            by_step = {}
            for row in rows:
                by_step.setdefault(row['step_id'], set()).add((row['module'], row['step_desc']))
            if any(len(values) > 1 for values in by_step.values()):
                warnings.append('같은 Step에 여러 매칭이 있습니다. 원본의 모듈·공정명을 확인하세요.')
            result['warning'] = ' '.join(warnings)
    except (OSError, UnicodeError, csv.Error):
        result['warning'] = '매칭 파일을 읽지 못했습니다. 저장된 구조는 계속 볼 수 있습니다.'
    return result


def _natural_step_key(value):
    parts = re.findall(r'\d+|\D+', str(value or ''))
    return tuple((0, int(part)) if part.isdigit() else (1, part.casefold()) for part in parts)


def _ensure(db):
    db.execute('CREATE TABLE IF NOT EXISTS product_structures (product TEXT PRIMARY KEY, body TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS product_structure_history (product TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY(product,revision))')


def _load(db, product):
    row = db.execute('SELECT body FROM product_structures WHERE product=?', (product.casefold(),)).fetchone()
    state = json.loads(row[0]) if row else {'product': product, 'revision': 0, 'rows': [], 'updated_at': '', 'updated_by': ''}
    # Transition data was added after the first structure release.  Treat old
    # snapshots as an empty, explicit change map so the read contract stays
    # stable without rewriting old history.
    state.setdefault('transitions', [])
    return state


def structure_state(product):
    name = wiki.product_name(product)
    with wiki.database() as db:
        _ensure(db)
        db.execute('BEGIN')
        return _load(db, name)


def _mentioned(text, name):
    return bool(name) and re.search(r'(?<![\w])' + re.escape(name) + r'(?![\w])', text, re.IGNORECASE) is not None


def match_references(text, rows, mapping):
    step_ids = {row['step_id'] for row in mapping['rows'] if _mentioned(text, row['step_id'])}
    paths = []
    leaf_counts = {}
    for row in rows:
        leaf = row['path'].split('/')[-1].casefold()
        leaf_counts[leaf] = leaf_counts.get(leaf, 0) + 1
    explicit_paths = set()
    for row in rows:
        path = f"{row['module']}/{row['path']}"
        leaf = row['path'].split('/')[-1]
        explicit = _mentioned(text, path) or (leaf_counts[leaf.casefold()] == 1 and len(leaf) >= 2 and _mentioned(text, leaf))
        if explicit:
            explicit_paths.add(path)
            step_ids.update(row['step_ids'])
    for row in rows:
        path = f"{row['module']}/{row['path']}"
        if path in explicit_paths or step_ids.intersection(row['step_ids']):
            paths.append(path)
    return {'step_ids': sorted(step_ids), 'paths': paths}


def document(product):
    state = structure_state(product)
    mapping = mapping_source(product)
    known = {row['step_id'] for row in mapping['rows']}
    missing = {step for row in state['rows'] for step in row['step_ids'] if step not in known}
    missing_transition = {step for item in state.get('transitions', []) for step in item.get('step_ids', []) if step not in known}
    warning = mapping['warning']
    if missing:
        warning += ' 저장된 구조의 일부 Step이 현재 매칭 파일에 없습니다. 기존 연결은 보존됩니다.'
    if missing_transition:
        warning += ' 저장된 구조 변화의 일부 Step이 현재 매칭 파일에 없습니다. 기존 근거는 보존됩니다.'
    mapping = dict(mapping, warning=warning.strip())
    links = {}
    for entry in wiki.document(product)['entries']:
        # Raw input remains authoritative; also use original structured fields for older records.
        text = entry.get('source_text') or '\n'.join(str(entry.get(key) or '') for key in ('body', 'structure', 'title'))
        links[entry['id']] = match_references(text, state['rows'], mapping)
    return dict(state, mapping=mapping, entry_links=links)


def _normalize(rows):
    if not isinstance(rows, list) or len(rows) > 500:
        raise ValueError('구조는 최대 500행까지 저장할 수 있습니다.')
    cleaned, seen = [], set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f'{index}행 형식이 올바르지 않습니다.')
        module = str(row.get('module') or '').strip()
        parts = [part.strip() for part in str(row.get('path') or '').split('/')]
        description = str(row.get('description') or '').strip()
        ids = row.get('step_ids') or []
        if not module or len(module) > 100 or '/' in module or any(c in module for c in '\r\n'):
            raise ValueError(f'{index}행 모듈은 / 없이 1~100자로 입력하세요.')
        if not 1 <= len(parts) <= 5 or any(not p or len(p) > 100 or any(c in p for c in '\r\n') for p in parts):
            raise ValueError(f'{index}행 구조 경로는 /로 구분한 1~5단계 이름이어야 합니다.')
        if len(description) > 3000:
            raise ValueError(f'{index}행 설명은 3,000자 이하여야 합니다.')
        if not isinstance(ids, list) or len(ids) > 200 or any(not isinstance(s, str) or not s.strip() or len(s) > 200 for s in ids):
            raise ValueError(f'{index}행 Step ID가 올바르지 않습니다.')
        path = '/'.join(parts)
        key = (module.casefold(), path.casefold())
        if key in seen:
            raise ValueError(f'{index}행 구조 경로가 중복됩니다. 같은 구조의 Step은 한 행에 모아주세요.')
        seen.add(key)
        cleaned.append({'module': module, 'path': path, 'step_ids': list(dict.fromkeys(s.strip() for s in ids)), 'description': description})
    return cleaned


def _normalize_transitions(transitions):
    """Validate administrator-authored structure changes without inferring causality."""
    if transitions is None:
        return None
    if not isinstance(transitions, list) or len(transitions) > 500:
        raise ValueError('구조 변화는 최대 500건까지 저장할 수 있습니다.')
    cleaned = []
    for index, item in enumerate(transitions, 1):
        if not isinstance(item, dict):
            raise ValueError(f'{index}번째 구조 변화 형식이 올바르지 않습니다.')
        module = str(item.get('module') or '').strip()
        from_path = str(item.get('from_path') or '').strip()
        to_path = str(item.get('to_path') or '').strip()
        relation = str(item.get('relation') or '').strip()
        description = str(item.get('description') or '').strip()
        order = item.get('order')
        if not module or len(module) > 100 or '/' in module or any(c in module for c in '\r\n'):
            raise ValueError(f'{index}번째 구조 변화 모듈은 / 없이 1~100자로 입력하세요.')
        if not from_path and not to_path:
            raise ValueError(f'{index}번째 구조 변화에는 이전 구조 또는 다음 구조를 입력하세요.')
        for label, value in (('이전 구조', from_path), ('다음 구조', to_path)):
            if not value:
                continue
            parts = [part.strip() for part in value.split('/')]
            if not 1 <= len(parts) <= 5 or any(not part or len(part) > 100 or any(c in part for c in '\r\n') for part in parts):
                raise ValueError(f'{index}번째 구조 변화의 {label} 경로는 /로 구분한 1~5단계 이름이어야 합니다.')
        if relation and (len(relation) > 80 or any(c in relation for c in '\r\n')):
            raise ValueError(f'{index}번째 구조 변화 관계는 80자 이내로 입력하세요.')
        if len(description) > 3000:
            raise ValueError(f'{index}번째 구조 변화 설명은 3,000자 이하여야 합니다.')
        if order in ('', None):
            normalized_order = None
        else:
            try:
                normalized_order = int(order)
            except (TypeError, ValueError) as exc:
                raise ValueError(f'{index}번째 구조 변화 순서는 1~500 정수여야 합니다.') from exc
            if not 1 <= normalized_order <= 500:
                raise ValueError(f'{index}번째 구조 변화 순서는 1~500 정수여야 합니다.')
        ids = item.get('step_ids') or []
        if not isinstance(ids, list) or len(ids) > 200 or any(not isinstance(step, str) or not step.strip() or len(step) > 200 for step in ids):
            raise ValueError(f'{index}번째 구조 변화 Step ID가 올바르지 않습니다.')
        cleaned.append({
            'order': normalized_order,
            'module': module,
            'from_path': '/'.join(part.strip() for part in from_path.split('/')) if from_path else '',
            'to_path': '/'.join(part.strip() for part in to_path.split('/')) if to_path else '',
            'relation': relation,
            'description': description,
            'step_ids': list(dict.fromkeys(step.strip() for step in ids)),
        })
    return cleaned


def _transition_path(module, path):
    return f'{module}/{path}' if path else ''


def save(product, revision, rows, actor, manager=False, transitions=None):
    if not manager:
        raise PermissionError('제품 구조 편집은 관리자 또는 제품 위키 관리자만 할 수 있습니다.')
    name = wiki.product_name(product)
    rows = _normalize(rows)
    transitions = _normalize_transitions(transitions)
    mapping = mapping_source(name)
    known = {row['step_id'] for row in mapping['rows']}
    with wiki.database() as db:
        _ensure(db)
        db.execute('BEGIN IMMEDIATE')
        previous = _load(db, name)
        if previous['revision'] != revision:
            raise wiki.Conflict('다른 관리자가 제품 구조를 수정했습니다. 최신 구조를 확인하세요.')
        old_links = {(r['module'], r['path'], s) for r in previous['rows'] for s in r['step_ids']}
        old_transition_links = {(item.get('module'), item.get('from_path', ''), item.get('to_path', ''), step)
                                for item in previous.get('transitions', []) for step in item.get('step_ids', [])}
        for row in rows:
            unknown = [s for s in row['step_ids'] if s not in known and (row['module'], row['path'], s) not in old_links]
            if unknown:
                raise ValueError(f"{row['module']}/{row['path']}: 현재 제품의 매칭에 없는 Step ID입니다: {', '.join(unknown[:5])}")
        next_transitions = previous.get('transitions', []) if transitions is None else transitions
        for item in next_transitions:
            unknown = [s for s in item.get('step_ids', []) if s not in known and
                       (item['module'], item.get('from_path', ''), item.get('to_path', ''), s) not in old_transition_links]
            if unknown:
                route = f"{item['module']}/{item.get('from_path') or '생성'}→{item.get('to_path') or '제거'}"
                raise ValueError(f"{route}: 현재 제품의 매칭에 없는 Step ID입니다: {', '.join(unknown[:5])}")
        if rows != previous['rows'] or next_transitions != previous.get('transitions', []):
            state = {'product': name, 'revision': revision + 1, 'rows': rows, 'updated_by': actor,
                     'updated_at': wiki.now(), 'mapping_fingerprint': mapping['fingerprint'],
                     'transitions': next_transitions}
            payload = json.dumps(state, ensure_ascii=False)
            db.execute('INSERT OR REPLACE INTO product_structures VALUES (?,?)', (name.casefold(), payload))
            db.execute('INSERT INTO product_structure_history VALUES (?,?,?)', (name.casefold(), state['revision'], payload))
            db.execute('INSERT OR IGNORE INTO products(key,name,revision) VALUES(?,?,0)', (name.casefold(), name))
    return document(name)


def history(product):
    name = wiki.product_name(product)
    with wiki.database() as db:
        _ensure(db)
        return [json.loads(row[0]) for row in db.execute('SELECT body FROM product_structure_history WHERE product=? ORDER BY revision DESC LIMIT 30', (name.casefold(),))]


def intake_context(product, text):
    state = structure_state(product)
    mapping = mapping_source(product)
    links = match_references(text, state['rows'], mapping)
    steps = [row for row in mapping['rows'] if row['step_id'] in links['step_ids']]
    structures = [row for row in state['rows'] if f"{row['module']}/{row['path']}" in links['paths'] or _mentioned(text, row['module'])]
    linked_paths = set(links['paths'])
    transitions = []
    for change in state.get('transitions', []):
        module = str(change.get('module') or '').strip()
        endpoints = {_transition_path(module, change.get('from_path', '')), _transition_path(module, change.get('to_path', ''))}
        endpoints.discard('')
        path_hit = any(path in linked_paths or any(linked.startswith(f'{path}/') for linked in linked_paths) for path in endpoints)
        step_hit = bool(set(change.get('step_ids') or []).intersection(links['step_ids']))
        if _mentioned(text, module) or path_hit or step_hit:
            transitions.append(change)
    # Explicit module mentions provide a small lookup even when no step is specified.
    if not steps:
        steps = [row for row in mapping['rows'] if _mentioned(text, row['module'])]
    context = {'source': 'Vehicle_matching.csv', 'mapping_fingerprint': mapping['fingerprint'],
               'structure_revision': state['revision'], 'step_ids': links['step_ids'], 'paths': links['paths'],
               'mapping_rows': steps[:80], 'structure_rows': structures[:30], 'transitions': transitions[:80],
               'truncated': len(steps) > 80 or len(structures) > 30 or len(transitions) > 80}
    # Keep the reference appendix bounded even with long admin descriptions.
    while len(json.dumps(context, ensure_ascii=False)) > 16000:
        context['truncated'] = True
        if context['structure_rows']:
            context['structure_rows'].pop()
        elif context['mapping_rows']:
            context['mapping_rows'].pop()
        elif context['step_ids']:
            context['step_ids'].pop()
        elif context['paths']:
            context['paths'].pop()
        elif context['transitions']:
            context['transitions'].pop()
        else:
            break
    return context


def matching_products():
    root = getattr(wiki.PATHS, 'db_root', None)
    path = Path(root) / 'Vehicle_matching.csv' if root else None
    if not path or not path.is_file():
        return []
    names = set()
    try:
        with path.open(encoding='utf-8-sig', newline='') as file:
            for raw in csv.DictReader(file):
                row = {str(k).strip().casefold(): str(v or '').strip() for k, v in raw.items() if k is not None}
                value = next((row.get(k) for k in ('product', 'vehicle', 'mask') if row.get(k)), '')
                names.update(wiki.product_name(part) for part in re.split(r'[.,;|]+', value) if part.strip())
    except (OSError, UnicodeError, csv.Error, ValueError):
        return []
    return sorted(names)


# ── Knob 레지스트리 · LOT 목적 테이블 ────────────────────────────────────
# 제품별 개선 knob 을 "소구조물(module/path) → Step set" 으로 묶어 관리한다.
# 하나의 knob code 가 여러 step 을 함께 바꾸면 steps 목록 전체가 한 set 이다.
# knob 마다 목적, 수율·성능·신뢰성 영향, POR/SOP 반영 상태, 부작용과 보상 관계
# (예: MOL knob A → nRch 저하 → knob B 로 보상)를 기록하고, 각 step 의 PPID 가
# ppid_knob 룰북에 등록돼 있는지 서버가 확인한다. 저장은 구조와 같은
# revision + 이력 방식이며, 새 모듈을 만들지 않으려고 이 파일에 둔다.

KNOB_POR_STATUSES = ('idea', 'experiment', 'validated', 'por', 'sop', 'dropped')
KNOB_POR_LABELS = {'idea': '아이디어', 'experiment': '평가 중', 'validated': '효과 검증',
                   'por': 'POR 반영', 'sop': 'SOP 반영', 'dropped': '중단'}
KNOB_IMPACT_AXES = ('yield', 'performance', 'reliability')
KNOB_IMPACT_EFFECTS = ('', 'up', 'down', 'neutral', 'mixed', 'unknown')
LOT_STATUSES = ('planned', 'running', 'done', 'hold', 'scrapped')
LOT_STATUS_LABELS = {'planned': '계획', 'running': '진행', 'done': '완료', 'hold': '보류', 'scrapped': '폐기'}
_MAX_KNOBS = 500
_MAX_KNOB_STEPS = 60
_MAX_LOTS = 3000
_ID_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,64}$')


def _ensure_registry(db):
    for name in ('product_knobs', 'product_lots'):
        db.execute(f'CREATE TABLE IF NOT EXISTS {name} (product TEXT PRIMARY KEY, body TEXT NOT NULL)')
        db.execute(f'CREATE TABLE IF NOT EXISTS {name}_history (product TEXT NOT NULL, revision INTEGER NOT NULL, '
                   'body TEXT NOT NULL, PRIMARY KEY(product,revision))')


def _load_registry(db, table, product, key):
    row = db.execute(f'SELECT body FROM {table} WHERE product=?', (product.casefold(),)).fetchone()
    return json.loads(row[0]) if row else {'product': product, 'revision': 0, key: [], 'updated_at': '', 'updated_by': ''}


def _text(value, limit, label, *, required=False):
    text = str(value if value is not None else '').strip()
    if required and not text:
        raise ValueError(f'{label}을(를) 입력하세요.')
    if len(text) > limit:
        raise ValueError(f'{label}은(는) {limit:,}자 이하여야 합니다.')
    return text


def _id_list(value, limit, label, pattern=r'[,\s]+'):
    items = value if isinstance(value, list) else re.split(pattern, str(value or ''))
    out = []
    for item in items:
        text = str(item or '').strip()
        if not text:
            continue
        if len(text) > 200:
            raise ValueError(f'{label} 값은 200자 이하여야 합니다.')
        if text not in out:
            out.append(text)
    if len(out) > limit:
        raise ValueError(f'{label}은(는) 최대 {limit}개까지 연결할 수 있습니다.')
    return out


def _normalize_knob(raw, index):
    if not isinstance(raw, dict):
        raise ValueError(f'{index}번째 knob 형식이 올바르지 않습니다.')
    label = f'{index}번째 knob'
    knob_id = str(raw.get('id') or '').strip() or f'k_{uuid.uuid4().hex[:10]}'
    if not _ID_RE.match(knob_id):
        raise ValueError(f'{label}: ID 형식이 올바르지 않습니다.')
    steps, seen = [], set()
    raw_steps = raw.get('steps') or []
    if not isinstance(raw_steps, list) or len(raw_steps) > _MAX_KNOB_STEPS:
        raise ValueError(f'{label}: Step은 최대 {_MAX_KNOB_STEPS}개입니다.')
    for step in raw_steps:
        if not isinstance(step, dict):
            continue
        step_id = _text(step.get('step_id'), 200, f'{label} Step ID')
        ppid = _text(step.get('ppid'), 200, f'{label} PPID')
        change = _text(step.get('change'), 1000, f'{label} 변경 내용')
        if not (step_id or ppid or change):
            continue
        if not step_id:
            raise ValueError(f'{label}: PPID·변경 내용이 있는 행에는 Step ID가 필요합니다.')
        key = (step_id.casefold(), ppid.casefold())
        if key in seen:
            continue
        seen.add(key)
        steps.append({'step_id': step_id, 'ppid': ppid, 'change': change})
    impacts = {}
    raw_impacts = raw.get('impacts') if isinstance(raw.get('impacts'), dict) else {}
    for axis in KNOB_IMPACT_AXES:
        item = raw_impacts.get(axis) if isinstance(raw_impacts.get(axis), dict) else {}
        effect = str(item.get('effect') or '').strip().lower()
        if effect not in KNOB_IMPACT_EFFECTS:
            raise ValueError(f'{label}: {axis} 영향은 {", ".join(e for e in KNOB_IMPACT_EFFECTS if e)} 중 하나여야 합니다.')
        impacts[axis] = {'effect': effect, 'note': _text(item.get('note'), 1000, f'{label} {axis} 설명')}
    status = str(raw.get('por_status') or 'idea').strip().lower()
    if status not in KNOB_POR_STATUSES:
        raise ValueError(f'{label}: POR 상태가 올바르지 않습니다.')
    por_date = _text(raw.get('por_date'), 10, f'{label} POR 일자')
    if por_date and not re.match(r'^\d{4}-\d{2}-\d{2}$', por_date):
        raise ValueError(f'{label}: POR 일자는 YYYY-MM-DD 형식이어야 합니다.')
    return {
        'id': knob_id,
        'name': _text(raw.get('name'), 200, f'{label} 이름', required=True),
        'code': _text(raw.get('code'), 200, f'{label} knob code'),
        'aliases': _id_list(raw.get('aliases'), 20, f'{label} 별칭', r'[,;\n]+'),
        'structure': _text(raw.get('structure'), 300, f'{label} 소구조물'),
        'steps': steps,
        'purpose': _text(raw.get('purpose'), 3000, f'{label} 목적'),
        'impacts': impacts,
        'side_effects': _text(raw.get('side_effects'), 1000, f'{label} 부작용'),
        'compensates': _id_list(raw.get('compensates'), 20, f'{label} 보상 대상'),
        'por_status': status,
        'por_ref': _text(raw.get('por_ref'), 300, f'{label} POR/SOP 근거'),
        'por_date': por_date,
        'lot_ids': _id_list(raw.get('lot_ids'), 100, f'{label} 평가 LOT'),
        'note': _text(raw.get('note'), 3000, f'{label} 메모'),
    }


def _normalize_knobs(knobs):
    if not isinstance(knobs, list) or len(knobs) > _MAX_KNOBS:
        raise ValueError(f'knob은 최대 {_MAX_KNOBS}개까지 저장할 수 있습니다.')
    cleaned = [_normalize_knob(raw, i) for i, raw in enumerate(knobs, 1)]
    ids = [k['id'] for k in cleaned]
    if len(set(ids)) != len(ids):
        raise ValueError('knob ID가 중복됩니다.')
    known = set(ids)
    for knob in cleaned:
        missing = [c for c in knob['compensates'] if c not in known]
        if missing:
            raise ValueError(f"{knob['name']}: 보상 대상 knob을 찾을 수 없습니다: {', '.join(missing[:5])}")
        if knob['id'] in knob['compensates']:
            raise ValueError(f"{knob['name']}: 자기 자신을 보상 대상으로 지정할 수 없습니다.")
    return cleaned


def _rulebook_rows(kind, default_file):
    """룰북 CSV 행(열 이름 casefold)과 스키마. 스플릿테이블 룰북 설정의 파일명/열 이름을 따른다."""
    schema, root = {}, None
    try:
        from app_v2.modules.splittable.rulebook_repository import RulebookRepository, get_base_root, resolve_rulebook_file
        schema = RulebookRepository().get_sch(kind) or {}
        root = get_base_root()
        path = resolve_rulebook_file(root, schema.get('file_name') or default_file)
        if not path.is_file() and kind == 'step_matching':
            path = resolve_rulebook_file(root, 'step_matching.csv')
        if not path.is_file() and kind == 'knob_ppid':
            path = resolve_rulebook_file(root, 'knob_ppid.csv')
    except Exception:
        root = Path(getattr(wiki.PATHS, 'base_root', '') or getattr(wiki.PATHS, 'db_root', ''))
        path = root / default_file
    if not path.is_file():
        return [], schema, ''
    try:
        with path.open(encoding='utf-8-sig', newline='') as file:
            rows = [{str(k).strip().casefold(): str(v or '').strip() for k, v in raw.items() if k is not None}
                    for raw in csv.DictReader(file)]
    except Exception:
        return [], schema, path.name
    return rows, schema, path.name


def _col(schema, key, *fallbacks):
    names = [str(schema.get(key) or '').strip().casefold()] + [f.casefold() for f in fallbacks]
    return [n for n in dict.fromkeys(names) if n]


def _first(row, cols):
    for col in cols:
        if row.get(col):
            return row[col]
    return ''


def _ppid_rule_matches(operator, value, ppid):
    op = str(operator or 'eq').strip().casefold()
    left, right = ppid.casefold(), str(value or '').strip().casefold()
    if not right:
        return False
    if op in ('', 'eq', '=', '==', 'equals'):
        return left == right
    if op in ('in', 'isin'):
        return left in {v.strip() for v in re.split(r'[,|;]', right) if v.strip()}
    if op in ('contains', 'like'):
        return right.replace('%', '') in left
    if op in ('startswith', 'prefix', 'starts_with'):
        return left.startswith(right)
    if op in ('endswith', 'suffix', 'ends_with'):
        return left.endswith(right)
    if op in ('regex', 're', 'match'):
        try:
            return re.search(right, left) is not None
        except re.error:
            return False
    if op in ('ne', '!=', '<>'):
        return left != right
    return left == right


def ppid_registry(product):
    """step_id → step 설명(function_step/step_desc), 그리고 ppid_knob 규칙 목록."""
    name = wiki.product_name(product)
    step_rows, step_schema, step_file = _rulebook_rows('step_matching', 'Vehicle_matching.csv')
    knob_rows, knob_schema, knob_file = _rulebook_rows('knob_ppid', 'ppid_knob.csv')
    step_id_cols = _col(step_schema, 'step_id_col', 'step_id')
    desc_cols = _col(step_schema, 'func_step_col', 'function_step') + _col(step_schema, 'step_desc_col', 'step_desc')
    product_cols = _col(step_schema, 'product_col', 'product')
    step_desc = {}
    for row in step_rows:
        prod = _first(row, product_cols)
        if prod and prod.casefold() != name.casefold():
            continue
        step_id = _first(row, step_id_cols)
        if not step_id:
            continue
        descs = step_desc.setdefault(step_id.casefold(), set())
        for col in desc_cols:
            if row.get(col):
                descs.add(row[col].casefold())
    rules = []
    feature_cols = _col(knob_schema, 'feature_col', 'feature_name')
    rule_step_cols = _col(knob_schema, 'func_step_col', 'function_step') + _col(knob_schema, 'step_desc_col', 'step_desc')
    value_cols = _col(knob_schema, 'ppid_col', 'ppid') + _col(knob_schema, 'value_col', 'value')
    operator_cols = _col(knob_schema, 'operator_col', 'operator')
    for row in knob_rows:
        rules.append({
            'feature': _first(row, feature_cols),
            'steps': {row[c].casefold() for c in rule_step_cols if row.get(c)} | ({row['step_id'].casefold()} if row.get('step_id') else set()),
            'value': _first(row, value_cols),
            'operator': _first(row, operator_cols) or 'eq',
        })
    return {'step_desc': step_desc, 'rules': rules, 'step_file': step_file, 'knob_file': knob_file}


def check_knob_step(registry, step_id, ppid):
    """Step 한 줄의 PPID 등록 여부. status: registered / step_only / ppid_elsewhere / step_unmapped / no_ppid."""
    sid = str(step_id or '').strip().casefold()
    descs = set(registry['step_desc'].get(sid, set())) | {sid}
    step_rules = [r for r in registry['rules'] if r['steps'] & descs]
    result = {'step_desc': sorted(registry['step_desc'].get(sid, set())), 'features': []}
    if not str(ppid or '').strip():
        return dict(result, status='no_ppid', message='PPID 미입력')
    hits = [r for r in step_rules if _ppid_rule_matches(r['operator'], r['value'], str(ppid))]
    if hits:
        return dict(result, status='registered', features=sorted({r['feature'] for r in hits if r['feature']}),
                    message='ppid_knob 등록됨')
    elsewhere = [r for r in registry['rules'] if _ppid_rule_matches(r['operator'], r['value'], str(ppid))]
    if elsewhere:
        return dict(result, status='ppid_elsewhere', features=sorted({r['feature'] for r in elsewhere if r['feature']}),
                    message='PPID는 다른 step 규칙에만 있음')
    if sid not in registry['step_desc'] and not step_rules:
        return dict(result, status='step_unmapped', message='Step 매칭 없음(step_matching 확인)')
    return dict(result, status='step_only', message='ppid_knob 미등록 PPID')


def knob_chains(knobs):
    """보상 관계를 원인 knob 부터 이어 붙인 경로 목록. 순환은 한 번만 따라간다."""
    by_id = {k['id']: k for k in knobs}
    compensated_by = {}
    for knob in knobs:
        for target in knob['compensates']:
            compensated_by.setdefault(target, []).append(knob['id'])
    chains = []

    def walk(path):
        nexts = [n for n in compensated_by.get(path[-1], []) if n not in path]
        if not nexts:
            if len(path) > 1:
                chains.append(path)
            return
        for nxt in nexts:
            walk(path + [nxt])

    starts = [k['id'] for k in knobs if k['id'] in compensated_by and not k['compensates']]
    # 모든 knob 이 서로 보상하는 순환만 있으면 시작점이 없으므로 아무 원인이나 잡는다.
    if not starts and compensated_by:
        starts = [next(iter(compensated_by))]
    for start in starts:
        walk([start])
    return [[{'id': kid, 'name': by_id[kid]['name'], 'side_effects': by_id[kid]['side_effects'],
              'structure': by_id[kid]['structure'], 'por_status': by_id[kid]['por_status']}
             for kid in chain] for chain in chains[:100]]


def knob_groups(knobs):
    """소구조물 → 묶음. 코드가 있는 knob 은 같은 코드끼리 step 을 합쳐 하나의 코드 set 으로,
    코드가 없는 knob 은 step 하나하나를 따로 인식한다(여러 step 이라도 set 으로 보지 않는다)."""
    groups = {}
    code_home = {}
    for knob in knobs:
        structure = knob['structure'] or '(소구조물 미지정)'
        code = str(knob.get('code') or '').strip()
        step_ids = sorted({s['step_id'] for s in knob['steps']}, key=_natural_step_key)
        if code:
            # 같은 코드는 소구조물이 달라도 처음 나온 소구조물 아래 한 set 으로 모은다.
            home = code_home.setdefault(code.casefold(), structure)
            bucket = groups.setdefault(home, {})
            item = bucket.setdefault(f'code:{code.casefold()}', {
                'kind': 'code', 'code': code, 'step_ids': [], 'knob_ids': [], 'structures': []})
            item['step_ids'] = sorted(set(item['step_ids']) | set(step_ids), key=_natural_step_key)
            item['knob_ids'].append(knob['id'])
            if structure not in item['structures']:
                item['structures'].append(structure)
            continue
        bucket = groups.setdefault(structure, {})
        for step_id in step_ids or ['']:
            key = f'step:{step_id.casefold()}' if step_id else 'step:'
            item = bucket.setdefault(key, {'kind': 'step', 'code': '', 'step_ids': [step_id] if step_id else [],
                                           'knob_ids': [], 'structures': [structure]})
            if knob['id'] not in item['knob_ids']:
                item['knob_ids'].append(knob['id'])
    out = []
    for structure, sets in sorted(groups.items(), key=lambda kv: kv[0].casefold()):
        rows = []
        for key, value in sets.items():
            label = (f"{value['code']} · " if value['kind'] == 'code' else '') + (' + '.join(value['step_ids']) or '(Step 미지정)')
            rows.append(dict(value, key=label, multi_step=len(value['step_ids']) > 1))
        rows.sort(key=lambda r: (r['kind'] != 'code', _natural_step_key(r['key'])))
        out.append({'structure': structure, 'step_sets': rows})
    return out


def code_hints(knobs):
    """코드 없이 step 이 여러 개인 knob — 코드를 넣어야 한 set 으로 묶인다."""
    return [k['id'] for k in knobs
            if not str(k.get('code') or '').strip() and len({s['step_id'] for s in k['steps']}) > 1]


_FEATURE_PREFIX = re.compile(r'^\s*\d+(?:\.\d+)*(?:\s+|[.)_:-]\s*)')


def normalize_feature_name(value):
    """ppid_knob feature_name 비교용 키: 앞 순번(10.0 등)·KNOB_ 접두어·공백/기호·대소문자 무시."""
    text = _FEATURE_PREFIX.sub('', str(value or ''))
    text = re.sub(r'^knob[\s_-]*', '', text.strip(), flags=re.IGNORECASE)
    return re.sub(r'[\W_]+', '', text).casefold()


def _feature_index(registry):
    index = {}
    for rule in registry['rules']:
        feature = str(rule.get('feature') or '').strip()
        key = normalize_feature_name(feature)
        if key:
            index.setdefault(key, feature)
    return index


def knob_feature_match(knob, registry, step_checks, index=None):
    """knob 이 ppid_knob 에 등록됐는지: name / alias / ppid(이름 다름) / none(+추천)."""
    import difflib

    index = _feature_index(registry) if index is None else index
    for field in ('name', 'code'):
        key = normalize_feature_name(knob.get(field))
        if key and key in index:
            return {'status': 'name', 'label': '이름 일치', 'feature': index[key], 'by': field, 'suggestions': []}
    for alias in knob.get('aliases') or []:
        key = normalize_feature_name(alias)
        if key and key in index:
            return {'status': 'alias', 'label': '별칭으로 인식', 'feature': index[key], 'by': alias, 'suggestions': []}
    by_ppid = sorted({f for c in step_checks if c.get('status') == 'registered' for f in c.get('features') or []})
    if by_ppid:
        return {'status': 'ppid', 'label': 'PPID로 등록(이름 다름)', 'feature': by_ppid[0],
                'by': 'ppid', 'suggestions': by_ppid[:5]}
    wanted = [normalize_feature_name(v) for v in [knob.get('name'), knob.get('code'), *(knob.get('aliases') or [])]]
    scores = {}
    for want in filter(None, wanted):
        for key in difflib.get_close_matches(want, list(index), n=5, cutoff=0.5):
            ratio = difflib.SequenceMatcher(None, want, key).ratio()
            scores[key] = max(scores.get(key, 0.0), ratio)
        for key in index:
            if len(want) >= 3 and (want in key or key in want):
                scores[key] = max(scores.get(key, 0.0), 0.6)
    ranked = [index[k] for k, _ in sorted(scores.items(), key=lambda kv: -kv[1])][:5]
    return {'status': 'none', 'label': '미등록', 'feature': '', 'by': '', 'suggestions': ranked}


def _lot_purposes_from_lot_management(product):
    """랏 관리 화면의 purpose(읽기 전용 참고). 실패해도 위키는 동작한다."""
    try:
        from routers import lot_management as lm
        doc = lm._load(product)
    except Exception:
        doc = None
    out = {}
    for row in (doc or {}).get('rows', []) if isinstance(doc, dict) else []:
        values = row.get('values', row) if isinstance(row, dict) else {}
        lot_id = str(values.get('lot_id') or values.get('root_lot_id') or '').strip()
        purpose = str(values.get('purpose') or '').strip()
        if lot_id and purpose:
            out[lot_id.casefold()] = purpose
    return out


def knobs_document(product):
    name = wiki.product_name(product)
    with wiki.database() as db:
        _ensure_registry(db)
        state = _load_registry(db, 'product_knobs', name, 'knobs')
    registry = ppid_registry(name)
    knobs = state.get('knobs', [])
    checks = {}
    summary = {'steps': 0, 'registered': 0, 'missing': 0}
    for knob in knobs:
        rows = []
        for step in knob.get('steps', []):
            check = check_knob_step(registry, step['step_id'], step.get('ppid'))
            rows.append(check)
            summary['steps'] += 1
            if check['status'] == 'registered':
                summary['registered'] += 1
            elif check['status'] in ('step_only', 'ppid_elsewhere', 'step_unmapped'):
                summary['missing'] += 1
        checks[knob['id']] = rows
    por = {status: sum(1 for k in knobs if k.get('por_status') == status) for status in KNOB_POR_STATUSES}
    index = _feature_index(registry)
    matches = {k['id']: knob_feature_match(k, registry, checks.get(k['id'], []), index) for k in knobs}
    return dict(state, ppid_checks=checks, ppid_summary=summary, groups=knob_groups(knobs),
                chains=knob_chains(knobs), por_counts=por, feature_matches=matches,
                code_hints=code_hints(knobs),
                rulebook={'step_file': registry['step_file'], 'knob_file': registry['knob_file'],
                          'rules': len(registry['rules'])},
                options={'por_statuses': [{'value': s, 'label': KNOB_POR_LABELS[s]} for s in KNOB_POR_STATUSES],
                         'impact_axes': list(KNOB_IMPACT_AXES),
                         'impact_effects': [e for e in KNOB_IMPACT_EFFECTS if e]})


def _save_registry(table, key, product, revision, items, actor):
    name = wiki.product_name(product)
    with wiki.database() as db:
        _ensure_registry(db)
        db.execute('BEGIN IMMEDIATE')
        previous = _load_registry(db, table, name, key)
        if previous['revision'] != revision:
            raise wiki.Conflict('다른 사용자가 먼저 수정했습니다. 최신 내용을 불러와 다시 저장하세요.')
        if items != previous.get(key, []):
            state = {'product': name, 'revision': revision + 1, key: items,
                     'updated_by': actor, 'updated_at': wiki.now()}
            payload = json.dumps(state, ensure_ascii=False)
            db.execute(f'INSERT OR REPLACE INTO {table} VALUES (?,?)', (name.casefold(), payload))
            db.execute(f'INSERT INTO {table}_history VALUES (?,?,?)', (name.casefold(), state['revision'], payload))
            db.execute('INSERT OR IGNORE INTO products(key,name,revision) VALUES(?,?,0)', (name.casefold(), name))


def save_knobs(product, revision, knobs, actor):
    _save_registry('product_knobs', 'knobs', product, revision, _normalize_knobs(knobs), actor)
    return knobs_document(product)


def _normalize_lots(lots, knob_ids):
    if not isinstance(lots, list) or len(lots) > _MAX_LOTS:
        raise ValueError(f'LOT은 최대 {_MAX_LOTS}개까지 저장할 수 있습니다.')
    cleaned, seen = [], set()
    for index, raw in enumerate(lots, 1):
        if not isinstance(raw, dict):
            raise ValueError(f'{index}행 형식이 올바르지 않습니다.')
        lot_id = _text(raw.get('lot_id'), 200, f'{index}행 LOT ID')
        rest = [raw.get(k) for k in ('purpose', 'knobs', 'knob_ids', 'result', 'owner', 'note')]
        if not lot_id:
            if any(str(v or '').strip() for v in rest):
                raise ValueError(f'{index}행: LOT ID가 비어 있습니다.')
            continue
        if not wiki._LOT_ID.match(lot_id):
            raise ValueError(f'{index}행 LOT ID 형식이 올바르지 않습니다: {lot_id}')
        if lot_id.casefold() in seen:
            raise ValueError(f'{index}행 LOT ID가 중복됩니다: {lot_id}')
        seen.add(lot_id.casefold())
        status = str(raw.get('status') or 'planned').strip().lower()
        status = {v: k for k, v in LOT_STATUS_LABELS.items()}.get(status, status)
        if status not in LOT_STATUSES:
            raise ValueError(f'{index}행 상태는 {", ".join(LOT_STATUS_LABELS.values())} 중 하나여야 합니다.')
        # knob 은 ID 또는 이름으로 입력할 수 있다(표에 붙여넣기 편하도록).
        refs = _id_list(raw.get('knob_ids', raw.get('knobs')), 30, f'{index}행 knob', r'[,;\n]+')
        resolved = []
        for ref in refs:
            hit = knob_ids.get(ref) or knob_ids.get(ref.casefold())
            if not hit:
                raise ValueError(f'{index}행: knob을 찾을 수 없습니다: {ref}')
            if hit not in resolved:
                resolved.append(hit)
        cleaned.append({
            'lot_id': lot_id,
            'purpose': _text(raw.get('purpose'), 1000, f'{index}행 목적'),
            'knob_ids': resolved,
            'status': status,
            'result': _text(raw.get('result'), 1000, f'{index}행 결과'),
            'owner': _text(raw.get('owner'), 100, f'{index}행 담당'),
            'note': _text(raw.get('note'), 1000, f'{index}행 메모'),
        })
    return cleaned


def lots_document(product):
    name = wiki.product_name(product)
    with wiki.database() as db:
        _ensure_registry(db)
        state = _load_registry(db, 'product_lots', name, 'lots')
        knobs = _load_registry(db, 'product_knobs', name, 'knobs').get('knobs', [])
    lot_mgmt = _lot_purposes_from_lot_management(name)
    names = {k['id']: k['name'] for k in knobs}
    lots = [dict(lot, knob_names=[names.get(k, k) for k in lot.get('knob_ids', [])],
                 lot_management_purpose=lot_mgmt.get(lot['lot_id'].casefold(), ''))
            for lot in state.get('lots', [])]
    # knob 에 적어 둔 평가 LOT 중 표에 아직 없는 것 — 표에 추가할 후보로 보여 준다.
    listed = {lot['lot_id'].casefold() for lot in lots}
    suggested = {}
    for knob in knobs:
        for lot_id in knob.get('lot_ids', []):
            if lot_id.casefold() not in listed:
                suggested.setdefault(lot_id, []).append(knob['name'])
    return dict(state, lots=lots,
                suggested=[{'lot_id': k, 'knob_names': v} for k, v in sorted(suggested.items())],
                statuses=[{'value': s, 'label': LOT_STATUS_LABELS[s]} for s in LOT_STATUSES])


def save_lots(product, revision, lots, actor):
    name = wiki.product_name(product)
    with wiki.database() as db:
        _ensure_registry(db)
        knobs = _load_registry(db, 'product_knobs', name, 'knobs').get('knobs', [])
    lookup = {}
    for knob in knobs:
        lookup[knob['id']] = knob['id']
        lookup.setdefault(knob['name'], knob['id'])
        lookup.setdefault(knob['name'].casefold(), knob['id'])
    _save_registry('product_lots', 'lots', name, revision, _normalize_lots(lots, lookup), actor)
    return lots_document(name)


def registry_history(product, kind):
    table = {'knobs': 'product_knobs', 'lots': 'product_lots'}[kind]
    name = wiki.product_name(product)
    with wiki.database() as db:
        _ensure_registry(db)
        return [json.loads(row[0]) for row in db.execute(
            f'SELECT body FROM {table}_history WHERE product=? ORDER BY revision DESC LIMIT 30', (name.casefold(),))]


def unregistered_knob_report():
    """매칭알람용: 제품별로 ppid_knob 에 아직 없는 knob(중단 제외)과 추천 feature 이름."""
    with wiki.database() as db:
        _ensure_registry(db)
        states = [json.loads(row[0]) for row in db.execute('SELECT body FROM product_knobs')]
    products = []
    errors = []
    for state in states:
        knobs = [k for k in state.get('knobs', []) if k.get('por_status') != 'dropped']
        if not knobs:
            continue
        product = state.get('product') or ''
        try:
            registry = ppid_registry(product)
        except Exception as exc:
            errors.append({'product': product, 'error': f'{type(exc).__name__}: {exc}'})
            continue
        index = _feature_index(registry)
        items = []
        for knob in knobs:
            checks = [check_knob_step(registry, s['step_id'], s.get('ppid')) for s in knob.get('steps', [])]
            match = knob_feature_match(knob, registry, checks, index)
            if match['status'] == 'none':
                items.append({'knob_id': knob['id'], 'name': knob['name'], 'code': knob.get('code', ''),
                              'aliases': knob.get('aliases', []), 'structure': knob.get('structure', ''),
                              'por_status': knob.get('por_status', ''), 'suggestions': match['suggestions']})
        if items:
            products.append({'product': product, 'revision': state.get('revision', 0), 'knobs': items})
    products.sort(key=lambda p: p['product'].casefold())
    return {'ok': True, 'products': products, 'count': sum(len(p['knobs']) for p in products), 'errors': errors}


def add_knob_aliases(product, items, actor):
    """[{knob_id, alias}] 를 최신 revision 위에 별칭으로 추가한다(이미 있으면 건너뜀)."""
    name = wiki.product_name(product)
    with wiki.database() as db:
        _ensure_registry(db)
        state = _load_registry(db, 'product_knobs', name, 'knobs')
    knobs = [dict(k, aliases=list(k.get('aliases') or [])) for k in state.get('knobs', [])]
    by_id = {k['id']: k for k in knobs}
    added = 0
    for item in items or []:
        knob = by_id.get(str(item.get('knob_id') or ''))
        alias = str(item.get('alias') or '').strip()
        if not knob or not alias:
            continue
        if alias.casefold() not in {a.casefold() for a in knob['aliases']} and len(knob['aliases']) < 20:
            knob['aliases'].append(alias)
            added += 1
    if added:
        save_knobs(name, state['revision'], knobs, actor)
    return {'ok': True, 'added': added}
