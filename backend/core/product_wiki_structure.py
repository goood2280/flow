"""Product structure overlays and read-only Vehicle_matching references."""
from __future__ import annotations

import csv
import hashlib
import json
import re
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
            rows.sort(key=lambda r: (r['module'], r['step_id'], r['step_desc']))
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
