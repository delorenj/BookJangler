import argparse
import datetime as dt
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

from capture_usage import export_session

ROOT = Path('/home/delorenj/code/BookJangler')
START = '2026-10-06T23:07:51Z'
MAIN = 'ses_eec86cf8effeOs4w01XBV6IxIF'
ROLES = {
    MAIN: 'main_orchestration',
    'ses_eec865c09ffe0V08iyu3v8LGAv': 'research',
    'ses_eec3a0e62ffe6uCSpz7Ueuyfnh': 'research',
    'ses_eec38daf2ffeOi3nEvS10iWO2y': 'pipeline_engineering',
    'ses_eec37da22ffelzgiBtIjf1MSHN': 'source_preparation',
    'ses_eec362397ffeZiV1R7abvolnBa': 'audio_quality_engineering',
    'ses_eec253af3ffeldwl0lKSvKL6ov': 'rendering_orchestration',
}
PRIORITY = ['audio_quality_engineering', 'source_preparation',
            'pipeline_engineering', 'rendering_orchestration', 'research']
TOKEN_KEYS = ['input_tokens', 'output_tokens', 'reasoning_tokens',
              'cache_read_tokens', 'cache_write_tokens', 'reported_total_tokens',
              'normalized_input_tokens']
SESSION_KEYS = {'session_id', 'sessionID', 'sessionId', 'thread_id', 'threadId',
                'invocation_id'}
MESSAGE_KEYS = {'message_id', 'messageID', 'messageId'}
REQUEST_KEYS = {'request_id', 'requestID', 'requestId', 'x-request-id'}
PARENT_KEYS = {'parent_session_id', 'parentID', 'parent_invocation_id'}


def instant(value: Any) -> dt.datetime | None:
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return dt.datetime.fromtimestamp(value / 1000, dt.UTC)
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.replace(tzinfo=dt.UTC) if parsed.tzinfo is None else parsed.astimezone(dt.UTC)
    except (ValueError, TypeError, OverflowError):
        return None


def iso(value: Any) -> str | None:
    parsed = instant(value)
    return parsed.isoformat() if parsed else None


def within(value: Any, start: Any, end: Any) -> bool:
    stamp, lower, upper = instant(value), instant(start), instant(end)
    return bool(stamp and lower and upper and lower <= stamp <= upper)


def safe(value: Any) -> str | int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if not isinstance(value, str) or len(value) > 200:
        return None
    if re.search(r'(?i)(?:sk-|ghp_|gho_|op://|bearer|password|secret|credential|api[_-]?key)', value):
        return None
    if re.fullmatch(r'[0-9a-fA-F]{40,}', value):
        return None
    return value if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/@+-]*', value) else None


def number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def values(obj: Any, keys: set[str]) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in keys and isinstance(value, str):
                found.add(value)
            elif isinstance(value, (dict, list)):
                found.update(values(value, keys))
    elif isinstance(obj, list):
        for value in obj:
            if isinstance(value, (dict, list)):
                found.update(values(value, keys))
    return found


def canonical_correlation(session_id: str) -> str:
    try:
        return str(uuid.UUID(session_id))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f'bloodbank:opencode:{session_id}'))


def verify_conversion(hook_root: Path) -> dict[str, Any]:
    base = hook_root / 'clients/base.py'
    native = hook_root / 'clients/native.py'
    text = base.read_text()
    adapter = native.read_text()
    if 'uuid.uuid5(uuid.NAMESPACE_URL, f"bloodbank:{self.name}:{session.session_id}")' not in text:
        raise RuntimeError('Canonical correlation conversion changed; do not guess UUIDs')
    if 'class OpenCodeAdapter(NativeAdapter):' not in adapter or 'name = "opencode"' not in adapter:
        raise RuntimeError('OpenCode adapter identity not verified')
    codex = (hook_root / 'clients/codex.py').read_text()
    if 'def get_correlation_id(' in adapter or 'def get_correlation_id(' in codex:
        raise RuntimeError('Adapter overrides correlation conversion; inspect before collecting')
    return {'verified': True, 'source': str(base),
            'line': text[:text.index('    def get_correlation_id(')].count('\n') + 1,
            'adapter_source': str(native),
            'rule': 'Native UUID unchanged; otherwise UUIDv5(NAMESPACE_URL, bloodbank:opencode:<native_session_id>)'}


class CandyClient:
    def __init__(self, base_url: str, page_size: int = 500, max_pages: int = 50,
                 timeout: float = 15, budget_seconds: float = 180,
                 fetch: Callable[[str, dict[str, Any]], Any] | None = None):
        self.base_url = base_url.rstrip('/')
        self.page_size = page_size
        self.max_pages = max_pages
        self.timeout = timeout
        self.deadline = time.monotonic() + budget_seconds
        self.fetch = fetch
        self.query_log: list[dict[str, Any]] = []
        self.raw_log: list[dict[str, Any]] = []
        self.requests = 0

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if time.monotonic() >= self.deadline:
            raise TimeoutError('Bounded collection deadline reached')
        self.requests += 1
        if self.fetch:
            return self.fetch(path, params or {})
        suffix = '?' + urllib.parse.urlencode(params) if params else ''
        request = urllib.request.Request(self.base_url + path + suffix,
                                         headers={'Accept': 'application/json'})
        with urllib.request.urlopen(request, timeout=min(self.timeout, max(0.1, self.deadline - time.monotonic()))) as response:
            return json.load(response)

    def collect(self, label: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
        audit: dict[str, Any] = {'label': label, 'filters': filters, 'page_size': self.page_size,
                                 'pages': [], 'exhausted': False, 'errors': []}
        self.query_log.append(audit)
        rows: dict[str, dict[str, Any]] = {}
        cursor: Any = None
        offset = 0
        seen_cursors: set[str] = set()
        try:
            for _ in range(self.max_pages):
                params = filters | {'limit': self.page_size, 'total': 1, 'tools': 1}
                params.update({'cursor': cursor} if cursor is not None else {'offset': offset})
                page = self.get('/events', params)
                events = page.get('events', page.get('items', []))
                has_more = page.get('has_more')
                next_cursor = page.get('next_cursor', page.get('nextCursor'))
                audit['pages'].append({'offset': offset if cursor is None else None,
                                       'cursor_used': cursor is not None, 'returned': len(events),
                                       'total': page.get('total'), 'total_capped': page.get('total_capped', False),
                                       'has_more': has_more, 'next_cursor_present': next_cursor is not None,
                                       'window': page.get('window')})
                for event in events:
                    if event.get('id'):
                        rows[str(event['id'])] = event
                if has_more is False:
                    audit['exhausted'] = True
                    break
                if next_cursor is not None:
                    audit['pagination'] = 'cursor'
                    if str(next_cursor) in seen_cursors:
                        raise RuntimeError('Repeated pagination cursor')
                    seen_cursors.add(str(next_cursor))
                    cursor = next_cursor
                    continue
                if has_more is True and cursor is not None:
                    raise RuntimeError('has_more without a next cursor')
                audit['pagination'] = 'offset'
                limit = int(page.get('limit', self.page_size))
                total = page.get('total')
                if has_more is None and (len(events) < limit or
                    (isinstance(total, int) and not page.get('total_capped') and offset + len(events) >= total)):
                    audit['exhausted'] = True
                    break
                if not events:
                    raise RuntimeError('Empty page still claims more events')
                offset += len(events)
            if not audit['exhausted']:
                audit['errors'].append('Maximum page budget reached')
        except (OSError, ValueError, RuntimeError) as error:
            audit['errors'].append(type(error).__name__)
        audit['unique_event_count'] = len(rows)
        audit['duplicate_rows'] = sum(p['returned'] for p in audit['pages']) - len(rows)
        return list(rows.values())

    def verify_raw(self, event: dict[str, Any]) -> None:
        entry: dict[str, Any] = {'event_id': safe(event.get('id')), 'type': safe(event.get('type'))}
        self.raw_log.append(entry)
        try:
            raw = self.get('/events/' + urllib.parse.quote(str(event['id']), safe='') + '/raw')
            entry['data_matches_listing'] = raw.get('data') == event.get('data')
            entry['identity_matches_listing'] = raw.get('id') == event.get('id') and raw.get('type') == event.get('type')
        except (OSError, ValueError) as error:
            entry['error'] = type(error).__name__


def tool_categories(name: str, arguments: dict[str, Any]) -> list[str]:
    command = str(arguments.get('command', '')).lower() if name in ('bash', 'shell', 'exec') else ''
    path = str(arguments.get('filePath', arguments.get('path', ''))).lower()
    prompt = str(arguments.get('prompt', '')).lower() if name == 'task' else ''
    text = command + ' ' + path + ' ' + prompt
    groups = set()
    if name in ('websearch', 'web_search', 'webfetch', 'web_fetch') or re.search(r'web-search\s+(search|fetch)', command):
        groups.add('research')
    if any(x in text for x in ('verify_audio', 'qa-chunks', 'qa-refs', 'ffprobe', 'silencedetect', 'loudnorm')):
        groups.add('audio_quality_engineering')
    if any(x in text for x in ('prepare_source', 'chapter.txt', 'pdftotext', 'ocrmypdf', 'tesseract', 'the-good-earth.pdf')):
        groups.add('source_preparation')
    if 'render_chapter' in text:
        groups.add('rendering_orchestration' if command and '--out' in command and not any(x in command for x in ('ruff', 'unittest', 'ty check')) else 'pipeline_engineering')
    if any(x in text for x in ('capture_usage', 'test_render_chapter', 'test_verify_audio')):
        groups.add('pipeline_engineering')
    if any(x in text for x in ('requests.jsonl', 'chapter.mp3', 'mpv', 'metrics.json', 'prepared.json')):
        groups.add('rendering_orchestration')
    if name == 'task':
        if 'source' in prompt and ('prepare' in prompt or 'preparation' in prompt):
            groups.add('source_preparation')
        if 'research' in prompt or 'explore' in prompt and not groups:
            groups.add('research')
        if 'render' in prompt and 'worker' in prompt:
            groups.add('rendering_orchestration')
    return [group for group in PRIORITY if group in groups]


def search_attempts(name: str, args: dict[str, Any], output: Any) -> dict[str, Any] | None:
    kind = None
    command = str(args.get('command', '')) if name in ('bash', 'shell', 'exec') else ''
    commands = re.findall(r'(?<![\w-])web-search\s+(search|fetch)\b', command)
    if name in ('websearch', 'web_search'):
        kind = 'search'
    elif name in ('webfetch', 'web_fetch'):
        kind = 'fetch'
    elif commands:
        kind = 'search' if 'search' in commands else 'fetch'
    if kind is None:
        return None
    recorded = None
    provider_counts: Counter[str] = Counter()
    receipt_count = 0
    objects = []
    if isinstance(output, dict):
        objects.append(output)
    elif isinstance(output, str):
        decoder = json.JSONDecoder()
        position = 0
        while position < len(output):
            candidate = output.find('{', position)
            if candidate < 0:
                break
            try:
                obj, size = decoder.raw_decode(output[candidate:])
                position = candidate + size
                if isinstance(obj, dict) and ('provider' in obj or 'results_by_provider' in obj or 'attempts' in obj):
                    objects.append(obj)
            except ValueError:
                position = candidate + 1
    for obj in objects:
        providers = set(obj.get('results_by_provider', {}))
        if obj.get('provider') in ('tavily', 'brave'):
            providers.add(obj['provider'])
        attempts = obj.get('attempts')
        if isinstance(attempts, list):
            providers.update(a.get('provider') for a in attempts if isinstance(a, dict))
        known = [provider for provider in providers if provider in ('tavily', 'brave')]
        if known:
            provider_counts.update(known)
            receipt_count += 1
        elif isinstance(attempts, list):
            recorded = (recorded or 0) + len(attempts)
        elif isinstance(attempts, int):
            recorded = (recorded or 0) + attempts
    if not objects and isinstance(output, str):
        winners = re.findall(r'web-search: [^\n]* via (tavily|brave)\b', output)
        failures = re.findall(r'(?:note: |(?:all providers failed|search failed): )(tavily|brave):', output)
        if winners or failures:
            provider_counts.update(winners + failures)
            receipt_count = len(winners)
    if provider_counts:
        recorded = sum(provider_counts.values())
    return {'operation': kind, 'invocations': max(1, len(commands)),
            'wrapper_recorded_upstream_attempts': recorded,
            'wrapper_provider_attempts_by_provider': dict(provider_counts),
            'structured_or_text_receipts': receipt_count,
            'upstream_attempts_known': recorded is not None,
            'underlying_http_requests_including_retries': None,
            'attempt_count_basis': 'Observed wrapper provider executions (winner plus fallback records), not hidden HTTP retries or billable requests'}


def normalize_native(tokens: dict[str, Any]) -> dict[str, Any]:
    cache = tokens.get('cache') or {}
    result = {'input_tokens': number(tokens.get('input')), 'output_tokens': number(tokens.get('output')),
              'reasoning_tokens': number(tokens.get('reasoning')),
              'cache_read_tokens': number(cache.get('read')), 'cache_write_tokens': number(cache.get('write')),
              'reported_total_tokens': number(tokens.get('total'))}
    components = [result[k] for k in ('input_tokens', 'cache_read_tokens', 'cache_write_tokens')]
    result['normalized_input_tokens'] = sum(x for x in components if x is not None) if all(x is not None for x in components) else None
    return result


def normalize_gateway(usage: dict[str, Any]) -> dict[str, Any]:
    return {'input_tokens': number(usage.get('uncached_input_tokens')),
            'normalized_input_tokens': number(usage.get('input_tokens')),
            'output_tokens': number(usage.get('output_tokens')),
            'reasoning_tokens': number(usage.get('reasoning_output_tokens')),
            'cache_read_tokens': number(usage.get('cache_read_input_tokens')),
            'cache_write_tokens': number(usage.get('cache_write_input_tokens')),
            'reported_total_tokens': None}


def phase_at(stamp: Any, cutoff: Any, report_start: Any) -> str:
    value, end, report = instant(stamp), instant(cutoff), instant(report_start)
    if not value:
        return 'unknown_time'
    if end and value <= end:
        return 'core_experiment'
    if report and value < report:
        return 'post_snapshot_confirmation'
    return 'report_overhead'


def native_rows(exports: dict[str, dict[str, Any]], cutoff: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    main_data = exports.get(MAIN, {})
    users = []
    for message in main_data.get('messages', []):
        info = message.get('info') or {}
        if info.get('role') != 'user':
            continue
        original = next((part.get('text', '') for part in message.get('parts', [])
                         if part.get('type') == 'text' and not part.get('synthetic')), '')
        if phase_at(info.get('time', {}).get('created'), cutoff, None) != 'core_experiment' and re.search(r'\bhtml\b', original, re.I) and re.search(r'\breport\b', original, re.I):
            users.append(info)
    report_start = min((stamp for i in users if (stamp := iso(i['time']['created'])) is not None), default=None)
    markers = sorted((iso(data.get('info', {}).get('time', {}).get('created')), ROLES[sid])
                     for sid, data in exports.items() if sid != MAIN and iso(data.get('info', {}).get('time', {}).get('created')))
    rows = []
    seen = set()
    excluded = Counter()
    for sid, data in exports.items():
        for message in data.get('messages', []):
            info = message.get('info') or {}
            if info.get('role') != 'assistant':
                continue
            completed = info.get('time', {}).get('completed')
            if not completed:
                excluded['incomplete_messages'] += 1
                continue
            key = (sid, info.get('id'))
            if key in seen:
                excluded['duplicate_messages'] += 1
                continue
            seen.add(key)
            created = info.get('time', {}).get('created')
            completion_time, experiment_start = instant(completed), instant(START)
            if completion_time and experiment_start and completion_time < experiment_start:
                excluded['before_experiment'] += 1
                continue
            phase = phase_at(completed, cutoff, report_start)
            if phase == 'report_overhead':
                excluded['report_overhead_messages'] += 1
                continue
            tools = []
            categories: set[str] = set()
            request_ids = values(info, REQUEST_KEYS)
            for part in message.get('parts', []):
                request_ids.update(values(part.get('metadata', {}), REQUEST_KEYS))
                if part.get('type') != 'tool':
                    continue
                state = part.get('state') or {}
                name = str(part.get('tool', 'unknown'))
                args = state.get('input') or {}
                candidates = tool_categories(name, args)
                categories.update(candidates)
                tools.append({'name': safe(name), 'status': safe(state.get('status')),
                              'call_id': safe(part.get('callID')), 'categories': candidates,
                              'time': iso(state.get('time', {}).get('start')),
                              'search': search_attempts(name, args, state.get('output'))})
            uncertain = False
            basis = 'explicit_child_session_role'
            if phase != 'core_experiment':
                group = phase
                basis = 'completion_after_snapshot_before_HTML_report_request'
            elif sid != MAIN:
                group = ROLES[sid]
            else:
                group = next((g for g in PRIORITY if g in categories), None)
                basis = 'actual_tool_calls_deterministic_priority'
                if not group:
                    preceding = [(stamp, role) for stamp, role in markers
                                 if (marker_time := instant(stamp)) is not None
                                 and (message_time := instant(created or completed)) is not None
                                 and marker_time <= message_time]
                    group = preceding[-1][1] if preceding else 'research'
                    basis = 'latest_child_phase_start_fallback'
                    uncertain = True
            rows.append({'time': iso(completed), 'started_at': iso(created), 'event_id': None,
                         'request_id': None, 'request_ids': sorted(x for x in request_ids if safe(x)),
                         'message_id': safe(info.get('id')), 'session_id': sid, 'group': group,
                         'phase': phase, 'type': 'opencode.completed_assistant_message',
                         'source': 'opencode_export', 'provider': safe(info.get('providerID')),
                         'model': safe(info.get('modelID')), 'tokens': normalize_native(info.get('tokens') or {}),
                         'cost': {'opencode_reported_usd': number(info.get('cost')), 'actual_charge_usd': None},
                         'tools': tools, 'attribution': {'basis': basis, 'uncertain': uncertain,
                         'mixed_tool_groups': [g for g in PRIORITY if g in categories],
                         'counted_once': True}})
    return sorted(rows, key=lambda r: (r['time'] or '', r['message_id'] or '')), {
        'report_request_at': report_start, 'excluded_native_messages': dict(excluded),
        'child_phase_markers': [{'time': stamp, 'group': role} for stamp, role in markers]}


def event_session(event: dict[str, Any], correlations: dict[str, str]) -> tuple[str | None, str | None]:
    if event.get('correlationid') in correlations:
        return correlations[event['correlationid']], 'canonical_correlation_uuid'
    data = event.get('data') or {}
    matched = sorted(values(data, SESSION_KEYS) & set(ROLES))
    if len(matched) == 1:
        return matched[0], 'explicit_native_session_key'
    return None, None


def event_relevance(event: dict[str, Any], correlations: dict[str, str]) -> bool:
    sid, _ = event_session(event, correlations)
    if sid:
        return True
    data = event.get('data') or {}
    if values(data, PARENT_KEYS) & set(ROLES):
        return True
    paths = values(data, {'cwd', 'working_directory', 'directory'})
    return any(path == str(ROOT) or path.startswith(str(ROOT) + '/') for path in paths)


def event_row(event: dict[str, Any], correlations: dict[str, str], native: list[dict[str, Any]],
              cutoff: str, report_start: str | None) -> dict[str, Any]:
    data = event.get('data') or {}
    payload = data.get('payload') or {}
    sid, basis = event_session(event, correlations)
    call_id = data.get('tool_call_id') or payload.get('tool_call_id')
    linked = next((row for row in native if row['session_id'] == sid and any(t['call_id'] == call_id for t in row['tools'])), None) if call_id else None
    phase = phase_at(event.get('time'), cutoff, report_start)
    categories = tool_categories(str(data.get('tool_name', '')), data.get('arguments') or payload.get('tool_input') or {})
    group = linked['group'] if linked else (ROLES.get(sid or '') if sid != MAIN else next(iter(categories), 'unassigned_main_event'))
    if phase != 'core_experiment':
        group = phase
    return {'time': iso(event.get('time')), 'event_id': safe(event.get('id')),
            'request_id': safe(data.get('request_id')), 'session_id': sid,
            'correlation_id': safe(event.get('correlationid')), 'message_id': linked['message_id'] if linked else None,
            'group': group or 'unassigned_related_event', 'phase': phase, 'type': safe(event.get('type')),
            'source': 'candystore', 'session_link_basis': basis, 'provider': safe((event.get('actor') or {}).get('provider')),
            'model': safe((event.get('actor') or {}).get('model')), 'tokens': None, 'cost': None,
            'tool': {'name': safe(data.get('tool_name')), 'status': safe(data.get('outcome')),
                     'call_id': safe(call_id), 'categories': categories,
                     'search': search_attempts(str(data.get('tool_name', '')), data.get('arguments') or payload.get('tool_input') or {}, data.get('result'))} if data.get('tool_name') else None,
            'description': 'Lifecycle/tool metadata only; arguments, results and narrative text omitted'}


def dedupe_gateway(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    seen: dict[tuple[Any, ...], str] = {}
    unique = []
    duplicates = []
    for event in sorted(events, key=lambda e: (e.get('time', ''), e.get('id', ''))):
        data = event.get('data') or {}
        prefix = (data.get('provider'), data.get('account_id'))
        keys = [prefix + (kind, data[kind]) for kind in ('usage_id', 'request_id', 'gateway_log_id') if data.get(kind) is not None]
        original = next((seen[key] for key in keys if key in seen), None)
        if original:
            duplicates.append({'event_id': safe(event.get('id')), 'duplicate_of': safe(original),
                               'usage_conflict': next(e for e in unique if e.get('id') == original).get('data', {}).get('usage') != data.get('usage')})
            for key in keys:
                seen[key] = original
            continue
        unique.append(event)
        for key in keys:
            seen[key] = str(event.get('id'))
    return unique, duplicates


def gateway_rows(events: list[dict[str, Any]], native: list[dict[str, Any]], correlations: dict[str, str],
                 cutoff: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    messages = {row['message_id']: row for row in native}
    requests = {request: row for row in native for request in row['request_ids']}
    result = []
    outside = []
    for event in events:
        data = event.get('data') or {}
        stamp = data.get('occurred_at', event.get('time'))
        sid, basis = event_session(event, correlations)
        message_links = [messages[value] for value in values(data, MESSAGE_KEYS) if value in messages]
        linked = message_links[0] if len(message_links) == 1 else requests.get(data.get('request_id'))
        if linked:
            if sid and sid != linked['session_id']:
                sid, basis, linked = None, 'conflicting_identity_not_attributed', None
            else:
                sid = linked['session_id']
                basis = 'explicit_message_id' if message_links else 'explicit_request_receipt'
        group = linked['group'] if linked else (ROLES[sid] if sid and sid != MAIN else 'unassigned_linked_session' if sid else 'unmatched_time_only')
        tokens = normalize_gateway(data.get('usage') or {})
        components = [tokens[k] for k in ('input_tokens', 'cache_read_tokens', 'cache_write_tokens')]
        consistent = tokens['normalized_input_tokens'] == sum(x for x in components if x is not None) if all(x is not None for x in components) else None
        row = {'time': iso(stamp), 'event_time': iso(event.get('time')), 'event_id': safe(event.get('id')),
               'request_id': safe(data.get('request_id')), 'usage_id': safe(data.get('usage_id')),
               'gateway_log_id': number(data.get('gateway_log_id')), 'session_id': sid,
               'message_id': linked['message_id'] if linked else None,
               'correlation_id': safe(event.get('correlationid')), 'group': group,
               'phase': 'core_experiment' if within(stamp, START, cutoff) else 'outside_core_occurrence_window',
               'type': 'bloodbank.llm.usage.recorded', 'source': 'candystore_gateway_ledger',
               'attributed_to_experiment': sid is not None, 'session_link_basis': basis,
               'provider': safe(data.get('provider')), 'model': safe(data.get('native_model')),
               'account_id': safe(data.get('account_id')), 'billing_class': safe(data.get('billing_class')),
               'requested_model': safe(data.get('requested_model')), 'returned_model': safe(data.get('returned_model')),
               'route': safe(data.get('route')), 'outcome': safe(data.get('outcome')),
               'status_code': number(data.get('status_code')), 'latency_ms': number(data.get('latency_ms')),
               'tokens': tokens, 'input_components_reconcile': consistent,
               'cost': {'actual_charge_usd': None, 'availability': 'No monetary amount in canonical usage.recorded schema'}}
        (result if within(stamp, START, cutoff) else outside).append(row)
    return result, outside


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {'rows': len(rows), 'tokens': {}, 'known_opencode_reported_cost_usd': 0,
                              'actual_charge_usd': None}
    for key in TOKEN_KEYS:
        numbers = [row['tokens'].get(key) for row in rows if row.get('tokens') and row['tokens'].get(key) is not None]
        result['tokens'][key] = sum(numbers) if numbers else None
        result.setdefault('missing_token_fields', {})[key] = len(rows) - len(numbers)
    costs = [row.get('cost', {}).get('opencode_reported_usd') for row in rows]
    result['known_opencode_reported_cost_usd'] = sum(x for x in costs if x is not None)
    result['rows_missing_opencode_cost'] = sum(x is None for x in costs)
    return result


def grouped(rows: list[dict[str, Any]], fields: list[str]) -> list[dict[str, Any]]:
    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[tuple(row.get(field) for field in fields)].append(row)
    return [{**dict(zip(fields, key)), **aggregate(bucket)}
            for key, bucket in sorted(buckets.items(), key=lambda item: str(item[0]))]


def tts_evidence(root: Path, metrics: dict[str, Any]) -> dict[str, Any]:
    receipt_path = root / 'output/voxcpm2-chapter-01/requests.jsonl'
    receipts: dict[str, dict[str, Any]] = {}
    starts: dict[str, dict[str, Any]] = {}
    line_count = 0
    with receipt_path.open() as stream:
        for line in stream:
            line_count += 1
            record = json.loads(line)
            request = record.get('request_id')
            if not request:
                continue
            if record.get('status') == 'started':
                starts[request] = record
            else:
                receipts[request] = record
    rows: list[dict[str, Any]] = []
    for request, record in receipts.items():
        wav = record.get('wav') or {}
        rows.append({'time': iso(record.get('completed_utc', record.get('timestamp_utc'))),
                     'started_at': iso(record.get('timestamp_utc')), 'request_id': safe(request),
                     'event_id': None, 'session_id': None, 'group': record.get('phase'),
                     'phase': 'local_tts', 'type': 'local_tts.receipt', 'source': 'requests.jsonl',
                     'provider': 'self_hosted', 'model': 'VoxCPM2', 'speaker': safe(record.get('speaker')),
                     'status': safe(record.get('status')), 'input_text_tokens': number(record.get('input_text_tokens')),
                     'wall_seconds': number(record.get('wall_seconds')), 'audio_seconds': number(wav.get('duration_seconds'))})
    return {'source': str(receipt_path), 'metrics_source': 'output/voxcpm2-chapter-01/metrics.json',
            'receipt_lines': line_count, 'unique_started_requests': len(starts),
            'unique_terminal_requests': len(receipts), 'unfinished_request_ids': sorted(set(starts) - set(receipts)),
            'totals': metrics['totals'], 'by_phase': metrics['by_phase'], 'by_speaker': metrics['by_speaker'],
            'costs': metrics['costs'], 'token_label': metrics['token_label'],
            'receipt_totals_match_metrics': len(receipts) == metrics['totals']['requests'] and
            sum(row['input_text_tokens'] or 0 for row in rows) == metrics['totals']['input_text_tokens'],
            'ledger': sorted(rows, key=lambda row: row['time'] or '')}


def research_summary(native: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    native_keys = set()
    for row in native:
        if row['phase'] != 'core_experiment':
            continue
        for tool in row['tools']:
            if tool['search']:
                native_keys.add((row['session_id'], tool['call_id']))
                attempts.append({'session_id': row['session_id'], 'message_id': row['message_id'],
                                 'call_id': tool['call_id'], 'time': tool['time'] or row['time'],
                                 'source': 'opencode_export', 'status': tool['status'], **tool['search']})
    supplemental = {}
    for row in events:
        tool = row.get('tool')
        if row['phase'] != 'core_experiment' or not tool or not tool['search']:
            continue
        key = (row['session_id'], tool['call_id'])
        if key not in native_keys and row['type'] == 'bloodbank.agent.tool.completed':
            supplemental[key] = {'session_id': row['session_id'], 'event_id': row['event_id'],
                                 'call_id': tool['call_id'], 'time': row['time'], 'source': 'candystore',
                                 'status': tool['status'], **tool['search']}
    attempts.extend(supplemental.values())
    return {'ledger': attempts, 'invocations_by_operation': dict(Counter({op: sum(a['invocations'] for a in attempts if a['operation'] == op) for op in ('search', 'fetch')})),
            'completed_tool_calls': sum(a['status'] in ('completed', 'success') for a in attempts),
            'failed_tool_calls': sum(a['status'] in ('error', 'failed', 'failure') for a in attempts),
            'wrapper_recorded_upstream_attempts': sum(a['wrapper_recorded_upstream_attempts'] or 0 for a in attempts) if any(a['upstream_attempts_known'] for a in attempts) else None,
            'calls_without_upstream_attempt_receipts': sum(not a['upstream_attempts_known'] for a in attempts),
            'actual_search_charge_usd': None,
            'definition': 'Native websearch/webfetch calls or literal web-search search/fetch executions. Skills, grep, task research prompts and hypothetical mentions are not search calls. Tool completion is not proof of upstream billing.'}


def reconcile(native: list[dict[str, Any]], snapshot: dict[str, Any], gateway: list[dict[str, Any]]) -> dict[str, Any]:
    core = [row for row in native if row['phase'] == 'core_experiment']
    totals = aggregate(core)
    compared = TOKEN_KEYS[:-1]
    delta = {key: (totals['tokens'][key] or 0) - snapshot['totals'][key] for key in compared}
    delta['completed_assistant_messages'] = len(core) - snapshot['totals']['completed_assistant_messages']
    per_session = []
    for expected in snapshot['sessions']:
        rows = [row for row in core if row['session_id'] == expected['session_id']]
        actual = aggregate(rows)
        differences = {key: (actual['tokens'][key] or 0) - expected[key] for key in compared}
        differences['completed_assistant_messages'] = len(rows) - expected['completed_assistant_messages']
        per_session.append({'session_id': expected['session_id'], 'expected_messages': expected['completed_assistant_messages'],
                            'actual_messages': len(rows), 'delta': differences, 'exact': not any(differences.values())})
    attributed = [row for row in gateway if row['attributed_to_experiment']]
    linked = [row for row in attributed if row['message_id']]
    return {'snapshot_source': 'usage-final-snapshot.json', 'snapshot_captured_at_utc': snapshot['captured_at_utc'],
            'snapshot_totals': snapshot['totals'], 'core_delta_from_snapshot': delta,
            'exact_snapshot_reconciliation': not any(delta.values()), 'by_session': per_session,
            'deterministically_attributed_gateway_rows': len(attributed), 'message_linked_gateway_rows': len(linked),
            'native_messages_without_gateway_message_receipt': len({row['message_id'] for row in core} - {row['message_id'] for row in linked}),
            'unmatched_gateway_rows_not_charged': len(gateway) - len(attributed),
            'gateway_native_token_comparison': [{'message_id': row['message_id'], 'event_id': row['event_id'],
                'delta': {key: (row['tokens'][key] or 0) - (next(n for n in core if n['message_id'] == row['message_id'])['tokens'][key] or 0)
                          for key in TOKEN_KEYS if key != 'reported_total_tokens'}} for row in linked if any(n['message_id'] == row['message_id'] for n in core)],
            'cost_availability': {'actual_experiment_llm_charge_usd': None,
                                  'reason': 'usage.recorded contains no monetary cost; no time-only gateway attribution or fabricated cache economics',
                                  'local_tts_provider_charge_usd': 0}}


def collect_report(root: Path = ROOT, base_url: str = 'http://127.0.0.1:8683',
                   max_pages: int = 50, budget_seconds: float = 180) -> dict[str, Any]:
    snapshot = json.loads((root / 'usage-final-snapshot.json').read_text())
    metrics = json.loads((root / 'output/voxcpm2-chapter-01/metrics.json').read_text())
    cutoff = snapshot['captured_at_utc']
    conversion = verify_conversion(Path.home() / '.agents/hooks/bloodbank')
    exports = {}
    export_errors = {}
    for sid in ROLES:
        try:
            exports[sid] = export_session(sid, timeout=30)
        except (OSError, ValueError, RuntimeError) as error:
            export_errors[sid] = type(error).__name__
    native, phase_info = native_rows(exports, cutoff)
    session_info = {sid: data.get('info', {}) for sid, data in exports.items()}
    del exports
    correlations = {canonical_correlation(sid): sid for sid in ROLES}
    client = CandyClient(base_url, max_pages=max_pages, budget_seconds=budget_seconds)
    bound = {'from': START, 'to': cutoff}
    candidates = {}
    for sid in ROLES:
        for event in client.collect('canonical_session:' + sid, bound | {'correlationid': canonical_correlation(sid)}):
            candidates[event['id']] = event
    for term in [*ROLES, 'BookJangler']:
        for event in client.collect('native_or_cwd_search:' + term, bound | {'q': term}):
            candidates[event['id']] = event
    cutoff_time = instant(cutoff)
    if cutoff_time is None:
        raise ValueError('Final snapshot cutoff timestamp is invalid')
    lag_end = (cutoff_time + dt.timedelta(minutes=10)).isoformat()
    gateway_events = client.collect('settled_gateway_usage_with_10min_envelope_lag',
                                   {'from': START, 'to': lag_end, 'type': 'bloodbank.llm.usage.recorded'})
    for event in gateway_events:
        candidates[event['id']] = event
    report_start = phase_info['report_request_at']
    later = client.collect('main_post_snapshot_before_report',
                           {'from': cutoff, 'to': report_start, 'correlationid': canonical_correlation(MAIN)}) if report_start else []
    relevant = [event for event in candidates.values() if event.get('type') != 'bloodbank.llm.usage.recorded'
                and event_relevance(event, correlations) and within(event.get('time'), START, cutoff)]
    verified_types = set()
    for event in [*relevant, *gateway_events]:
        if event.get('type') not in verified_types:
            client.verify_raw(event)
            verified_types.add(event.get('type'))
    events = [event_row(event, correlations, native, cutoff, report_start) for event in relevant]
    report_time = instant(report_start)
    later_events = [event_row(event, correlations, native, cutoff, report_start) for event in later
                    if (event_time := instant(event.get('time'))) is not None
                    and event_time > cutoff_time and (report_time is None or event_time < report_time)]
    gateway_unique, gateway_duplicates = dedupe_gateway(gateway_events)
    gateway, gateway_outside = gateway_rows(gateway_unique, native, correlations, cutoff)
    core_native = [row for row in native if row['phase'] == 'core_experiment']
    sessions = []
    for sid, role in ROLES.items():
        own_events = [row for row in events if row['session_id'] == sid]
        own_native = [row for row in core_native if row['session_id'] == sid]
        native_calls = {tool['call_id'] for row in own_native for tool in row['tools'] if tool['call_id']}
        completed_calls = {row['tool']['call_id'] for row in own_events if row['tool'] and row['type'] == 'bloodbank.agent.tool.completed'}
        started_calls = {row['tool']['call_id'] for row in own_events if row['tool'] and row['type'] == 'bloodbank.agent.tool.called'}
        info = session_info.get(sid, {})
        sessions.append({'session_id': sid, 'role': role, 'correlation_id': canonical_correlation(sid),
                         'parent_session_id': safe(info.get('parentID')), 'created_at': iso(info.get('time', {}).get('created')),
                         'native_export_available': sid in session_info, 'native_export_error': export_errors.get(sid),
                         'completed_core_messages': len(own_native), 'usage': aggregate(own_native),
                         'candystore_events': len(own_events), 'event_type_histogram': dict(sorted(Counter(r['type'] for r in own_events).items())),
                         'first_event_at': min((r['time'] for r in own_events), default=None),
                         'last_event_at': max((r['time'] for r in own_events), default=None),
                         'native_tool_calls': len(native_calls), 'called_event_unique_calls': len(started_calls),
                         'completed_event_unique_calls': len(completed_calls),
                         'native_tools_missing_completed_events': sorted(native_calls - completed_calls),
                         'event_completions_without_native_core_call': sorted(completed_calls - native_calls),
                         'canonical_uuid_verified_in_events': any(e.get('correlationid') == canonical_correlation(sid) for e in relevant),
                         'explicit_native_id_verified_in_events': any(sid in values(e.get('data'), SESSION_KEYS) for e in relevant)})
    tts = tts_evidence(root, metrics)
    attributable = [row for row in gateway if row['attributed_to_experiment']]
    unmatched = [row for row in gateway if not row['attributed_to_experiment']]
    return {'schema_version': 1, 'collected_at_utc': dt.datetime.now(dt.UTC).isoformat(),
            'coverage': {'experiment_start_utc': START, 'core_cutoff_utc': cutoff,
                         'gateway_envelope_query_end_utc': lag_end, 'settlement_lag_allowance_minutes': 10,
                         'query_count': len(client.query_log), 'http_request_count': client.requests,
                         'query_pages': sum(len(q['pages']) for q in client.query_log),
                         'all_queries_exhausted': all(q['exhausted'] for q in client.query_log),
                         'queries': client.query_log, 'raw_endpoint_verifications': client.raw_log,
                         'candidate_unique_event_count': len(candidates), 'core_relevant_event_count': len(events),
                         'core_event_type_histogram': dict(sorted(Counter(row['type'] for row in events).items())),
                         'candidate_event_type_histogram': dict(sorted(Counter(e['type'] for e in candidates.values()).items())),
                         'post_snapshot_event_count': len(later_events), 'canonical_session_conversion': conversion,
                         'session_coverage': sessions, 'project_filter': 'Unregistered project: no registered-slug filter; exact native/correlation keys and verified cwd paths',
                         'pagination_contract_source': '/home/delorenj/code/33GOD/candystore/candystore/main.py:104 and query.py:376',
                         'pagination_limitation': 'Live API uses offset, no cursor/has_more; fixed bounds and ID dedup. Concurrent historical backfill can change offsets.',
                         'collection_mode': 'One finite historical collection, no context/latest sampling, no polling, GET only'},
            'sessions': sessions,
            'phases': {'core_experiment': {'from': START, 'to': cutoff, 'boundary': 'Completed assistant messages <= final snapshot timestamp'},
                       'post_snapshot_confirmation': {'from_exclusive': cutoff, 'to_exclusive': report_start,
                                                      'definition': 'Original main-session completion/reaction exchange before HTML report request; not core'},
                       'report_overhead': {'from': report_start, 'included_in_core': False,
                                           'definition': 'Report-request exchange and subsequent report workers excluded'},
                       'group_definitions': {'research': 'CLI exploration and cast research child sessions; main actual searches',
                                             'source_preparation': 'PDF/OCR/chapter extraction and source preparation child',
                                             'pipeline_engineering': 'Renderer implementation/tests and experiment usage capture tooling',
                                             'audio_quality_engineering': 'QA implementation, analysis and audio checks',
                                             'rendering_orchestration': 'Render worker, synthesis/assembly invocation, receipt inspections and playback'},
                       'main_mixed_call_priority': PRIORITY, 'main_attribution': 'One completed message counted once. Explicit calls first, latest child phase start fallback is marked uncertain. No token splitting across calls.',
                       **phase_info},
            'llm_usage': {'opencode': {'core_totals': aggregate(core_native),
                                      'by_group_provider_model': grouped(core_native, ['group', 'provider', 'model']),
                                      'post_snapshot_totals': aggregate([r for r in native if r['phase'] != 'core_experiment']),
                                      'ledger': native},
                          'gateway': {'source': 'bloodbank.llm.usage.recorded',
                                      'schema_source': '/home/delorenj/code/33GOD/bloodbank/schemas/bloodbank/llm/usage.recorded.json',
                                      'authoritative_for_settled_usage': True, 'core_occurrence_rows': len(gateway),
                                      'attributed_totals': aggregate(attributable),
                                      'attributed_by_group_provider_model': grouped(attributable, ['group', 'provider', 'model']),
                                      'unmatched_rows_not_charged': len(unmatched),
                                      'unmatched_window_diagnostics_NOT_experiment_totals': grouped(unmatched, ['provider', 'model', 'account_id', 'billing_class']),
                                      'rows_with_session_key': sum(bool((e.get('data') or {}).get('session_id')) for e in gateway_unique),
                                      'rows_with_message_keys': sum(bool(values(e.get('data'), MESSAGE_KEYS)) for e in gateway_unique),
                                      'duplicate_events_excluded': gateway_duplicates,
                                      'ledger': gateway, 'outside_core_occurrence_rows_not_charged': gateway_outside},
                          'token_semantics': 'OpenCode input is uncached; normalized input=input+cache read+cache write. Gateway input_tokens already includes cache; do not add cache twice. Reported total is used as supplied; reasoning is not added again. Native and gateway ledgers are alternate observations, not additive charges.'},
            'events': {'core_count': len(events), 'type_histogram': dict(sorted(Counter(r['type'] for r in events).items())),
                       'ledger': sorted(events + later_events, key=lambda row: (row['time'] or '', row['event_id'] or '')),
                       'research_tools': research_summary(native, events)},
            'tts': tts, 'reconciliation': reconcile(native, snapshot, gateway),
            'caveats': ['Exhausting stored events proves retrieval coverage, not that every native/provider action emitted an event.',
                        'No raw prompts, transcripts, tool inputs/outputs, environment values, credentials, proof hashes or audio fingerprints persisted.',
                        'Unknown gateway session/message/request linkage stays time-only and is never assigned or charged to this experiment.',
                        'Upstream model/account facts in unmatched records describe concurrent traffic only, not the BookJangler model route.',
                        'Gateway event schema has usage and subscription/metered class but no monetary charge amount. Pricing is outside this collector.',
                        'Subscription utilization percentages and cache discounts cannot be inferred from token counts.',
                        'Search invocation completion is not upstream success or a billable receipt; retries are known only with structured attempt receipts.',
                        'Post-snapshot completion/listening exchange is separately recorded; all report work is excluded from core.',
                        'Finite 10-minute envelope-lag allowance can miss later backfilled settled events; no claims of unbounded completeness.']}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8683')
    parser.add_argument('--max-pages', type=int, default=50)
    parser.add_argument('--budget-seconds', type=float, default=180)
    args = parser.parse_args()
    if args.max_pages <= 0 or args.budget_seconds <= 0:
        parser.error('Bounds must be positive')
    report = collect_report(base_url=args.base_url, max_pages=args.max_pages, budget_seconds=args.budget_seconds)
    (ROOT / 'report-ledger.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    chronological = [*report['events']['ledger'], *report['llm_usage']['opencode']['ledger'],
                     *report['llm_usage']['gateway']['ledger'], *report['tts']['ledger']]
    chronological.sort(key=lambda row: (row.get('time') or '', row.get('event_id') or row.get('message_id') or row.get('request_id') or ''))
    with (ROOT / 'report-ledger-events.jsonl').open('w') as stream:
        for row in chronological:
            stream.write(json.dumps(row, sort_keys=True) + '\n')
    print(json.dumps({'core_events': report['events']['core_count'],
                      'gateway_window_rows': report['llm_usage']['gateway']['core_occurrence_rows'],
                      'attributed_gateway_rows': report['reconciliation']['deterministically_attributed_gateway_rows'],
                      'snapshot_reconciles': report['reconciliation']['exact_snapshot_reconciliation'],
                      'queries_exhausted': report['coverage']['all_queries_exhausted'],
                      'tts_requests': report['tts']['totals']['requests']}))


if __name__ == '__main__':
    main()
