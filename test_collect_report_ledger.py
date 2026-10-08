import json
import unittest
from unittest.mock import patch

from collect_report_ledger import (
    MAIN,
    ROLES,
    START,
    CandyClient,
    aggregate,
    canonical_correlation,
    dedupe_gateway,
    event_relevance,
    event_session,
    gateway_rows,
    grouped,
    native_rows,
    normalize_gateway,
    normalize_native,
    phase_at,
    reconcile,
    safe,
    search_attempts,
    within,
)

CUTOFF = '2026-10-07T01:24:57.720432+00:00'
CHILD = 'ses_eec37da22ffelzgiBtIjf1MSHN'


def message(message_id, completed, tools=None):
    return {'info': {'id': message_id, 'role': 'assistant',
                     'time': {'created': completed, 'completed': completed},
                     'providerID': 'automaticai', 'modelID': 'sol-6.1', 'cost': 0,
                     'tokens': {'input': 70, 'output': 10, 'reasoning': 0,
                                'cache': {'read': 20, 'write': 0}, 'total': 100}},
            'parts': tools or []}


def tool(name, args, call='call-1', status='completed', output=''):
    return {'type': 'tool', 'tool': name, 'callID': call,
            'state': {'input': args, 'status': status, 'output': output}}


def event(event_id, request='req-1', session=None, stamp='2026-10-07T00:00:00Z'):
    return {'id': event_id, 'type': 'bloodbank.llm.usage.recorded', 'time': stamp,
            'correlationid': 'unrelated-correlation',
            'data': {'request_id': request, 'usage_id': 'usage-' + event_id,
                     'gateway_log_id': int(event_id[-1]) if event_id[-1].isdigit() else 99,
                     'occurred_at': stamp, 'provider': 'openai', 'account_id': 'personal',
                     'session_id': session, 'native_model': 'gpt-6.1-sol',
                     'billing_class': 'subscription',
                     'usage': {'input_tokens': 90, 'uncached_input_tokens': 70,
                               'cache_read_input_tokens': 20, 'cache_write_input_tokens': 0,
                               'output_tokens': 10, 'reasoning_output_tokens': 0}}}


class PaginationTests(unittest.TestCase):
    def test_cursor_pagination_deduplicates(self):
        pages = [
            {'events': [{'id': 'one'}, {'id': 'two'}], 'total': 3,
             'has_more': True, 'next_cursor': 'next'},
            {'events': [{'id': 'two'}, {'id': 'three'}], 'total': 3,
             'has_more': False},
        ]
        seen = []

        def fetch(path, params):
            seen.append(params)
            return pages.pop(0)

        client = CandyClient('unused', page_size=2, fetch=fetch)
        rows = client.collect('test', {'from': START, 'to': CUTOFF})
        self.assertEqual([row['id'] for row in rows], ['one', 'two', 'three'])
        self.assertEqual(seen[1]['cursor'], 'next')
        self.assertEqual(client.query_log[0]['duplicate_rows'], 1)
        self.assertTrue(client.query_log[0]['exhausted'])
        self.assertEqual(client.requests, 2)

    def test_offset_pagination_ignores_capped_total(self):
        pages = [
            {'events': [{'id': 'one'}, {'id': 'two'}], 'limit': 2,
             'total': 2, 'total_capped': True},
            {'events': [{'id': 'three'}], 'limit': 2,
             'total': 2, 'total_capped': True},
        ]
        seen = []

        def fetch(path, params):
            seen.append(params)
            return pages.pop(0)

        client = CandyClient('unused', page_size=2, fetch=fetch)
        self.assertEqual(len(client.collect('test', {})), 3)
        self.assertEqual(seen[1]['offset'], 2)
        self.assertIsNone(client.query_log[0]['pages'][0]['has_more'])
        self.assertEqual(client.query_log[0]['pagination'], 'offset')
        self.assertTrue(client.query_log[0]['exhausted'])

    def test_exact_offset_total_stops_without_extra_page(self):
        client = CandyClient('unused', page_size=2, fetch=lambda p, q: {
            'events': [{'id': 'one'}, {'id': 'two'}], 'limit': 2,
            'total': 2, 'total_capped': False})
        client.collect('test', {})
        self.assertEqual(client.requests, 1)
        self.assertTrue(client.query_log[0]['exhausted'])

    def test_page_budget_and_repeated_cursor_are_visible(self):
        page = {'events': [{'id': 'one'}], 'has_more': True, 'next_cursor': 'same'}
        client = CandyClient('unused', max_pages=4, fetch=lambda p, q: page)
        client.collect('test', {})
        self.assertFalse(client.query_log[0]['exhausted'])
        self.assertEqual(client.requests, 2)
        self.assertTrue(client.query_log[0]['errors'])
        bounded = CandyClient('unused', max_pages=1, fetch=lambda p, q: page)
        bounded.collect('test', {})
        self.assertEqual(bounded.query_log[0]['errors'], ['Maximum page budget reached'])

    def test_deadline_does_not_poll(self):
        client = CandyClient('unused', fetch=lambda p, q: {})
        with patch('collect_report_ledger.time.monotonic', return_value=client.deadline + 1):
            client.collect('test', {})
        self.assertEqual(client.requests, 0)
        self.assertFalse(client.query_log[0]['exhausted'])


class ScopeTests(unittest.TestCase):
    def test_canonical_known_uuid(self):
        self.assertEqual(canonical_correlation(MAIN), '6033e910-c1e8-56c5-97bd-b48a6cd0b37b')

    def test_boundary_is_inclusive_and_report_is_excluded(self):
        self.assertTrue(within(CUTOFF, START, CUTOFF))
        self.assertFalse(within('2026-10-07T01:24:57.720433Z', START, CUTOFF))
        self.assertFalse(within(None, START, CUTOFF))
        self.assertEqual(phase_at(CUTOFF, CUTOFF, '2026-10-07T12:00:00Z'), 'core_experiment')
        self.assertEqual(phase_at('2026-10-07T02:00:00Z', CUTOFF, '2026-10-07T12:00:00Z'), 'post_snapshot_confirmation')
        self.assertEqual(phase_at('2026-10-07T12:00:00Z', CUTOFF, '2026-10-07T12:00:00Z'), 'report_overhead')

    def test_session_filter_does_not_accept_time_only_or_prompt_mention(self):
        correlations = {canonical_correlation(sid): sid for sid in ROLES}
        unrelated = event('event-1')
        unrelated['data']['prompt'] = MAIN + ' /home/delorenj/code/BookJangler'
        self.assertFalse(event_relevance(unrelated, correlations))
        self.assertEqual(event_session(unrelated, correlations), (None, None))
        unrelated['data']['session_id'] = CHILD
        self.assertEqual(event_session(unrelated, correlations)[0], CHILD)
        self.assertTrue(event_relevance(unrelated, correlations))
        unrelated['data']['session_id'] = None
        unrelated['data']['cwd'] = '/home/delorenj/code/BookJangler-other'
        self.assertFalse(event_relevance(unrelated, correlations))
        unrelated['data']['cwd'] = '/home/delorenj/code/BookJangler/output'
        self.assertTrue(event_relevance(unrelated, correlations))

    def test_native_rows_dedupe_and_separate_post_snapshot(self):
        core = message('msg-core', CUTOFF)
        post = message('msg-post', '2026-10-07T01:30:00Z')
        report = message('msg-report', '2026-10-07T12:00:01Z')
        user = {'info': {'id': 'user-report', 'role': 'user',
                         'time': {'created': '2026-10-07T12:00:00Z'}},
                'parts': [{'type': 'text', 'text': 'An HTML usage report please'}]}
        rows, phases = native_rows({MAIN: {'info': {}, 'messages': [core, core, post, user, report]}}, CUTOFF)
        self.assertEqual([row['message_id'] for row in rows], ['msg-core', 'msg-post'])
        self.assertEqual(rows[1]['group'], 'post_snapshot_confirmation')
        self.assertEqual(phases['excluded_native_messages']['report_overhead_messages'], 1)
        self.assertNotIn('An HTML', json.dumps(rows))


class UsageTests(unittest.TestCase):
    def test_normalized_input_does_not_double_count_cache(self):
        native = normalize_native({'input': 70, 'cache': {'read': 20, 'write': 0},
                                   'output': 10, 'reasoning': 0, 'total': 100})
        gateway = normalize_gateway(event('event-1')['data']['usage'])
        self.assertEqual(native['normalized_input_tokens'], 90)
        self.assertEqual(gateway['normalized_input_tokens'], 90)
        self.assertEqual(native['reported_total_tokens'], 100)
        self.assertIsNone(gateway['reported_total_tokens'])
        self.assertIsNone(normalize_native({})['normalized_input_tokens'])

    def test_gateway_request_dedup_and_conflict(self):
        one = event('event-1')
        duplicate = event('event-2')
        duplicate['data']['usage']['output_tokens'] = 11
        unique, duplicates = dedupe_gateway([one, duplicate, event('event-3', 'req-3')])
        self.assertEqual(len(unique), 2)
        self.assertEqual(duplicates[0]['duplicate_of'], 'event-1')
        self.assertTrue(duplicates[0]['usage_conflict'])

    def test_time_only_gateway_never_attributed(self):
        rows, outside = gateway_rows([event('event-1'), event('event-2', 'req-2', CHILD),
                                     event('event-3', 'req-3', CHILD, '2026-10-07T02:00:00Z')],
                                    [], {}, CUTOFF)
        self.assertFalse(rows[0]['attributed_to_experiment'])
        self.assertEqual(rows[0]['group'], 'unmatched_time_only')
        self.assertTrue(rows[1]['attributed_to_experiment'])
        self.assertEqual(rows[1]['group'], 'source_preparation')
        self.assertEqual(len(outside), 1)
        self.assertIsNone(rows[1]['cost']['actual_charge_usd'])

    def test_gateway_deterministic_message_receipt(self):
        rows, _ = native_rows({CHILD: {'info': {}, 'messages': [message('msg-one', '2026-10-07T00:00:00Z')]}}, CUTOFF)
        receipt = event('event-1')
        receipt['data']['messageID'] = 'msg-one'
        gateway, _ = gateway_rows([receipt], rows, {}, CUTOFF)
        self.assertEqual(gateway[0]['session_id'], CHILD)
        self.assertEqual(gateway[0]['session_link_basis'], 'explicit_message_id')

    def test_mixed_main_group_single_count_and_exact_child_role(self):
        mixed = message('msg-mixed', '2026-10-07T00:00:00Z', [
            tool('bash', {'command': 'web-search search "test"'}, 'call-search'),
            tool('read', {'filePath': '/tmp/verify_audio.py'}, 'call-qa')])
        rows, _ = native_rows({MAIN: {'info': {}, 'messages': [mixed]},
                               CHILD: {'info': {'time': {'created': START}},
                                       'messages': [message('msg-source', '2026-10-07T00:01:00Z')]}}, CUTOFF)
        self.assertEqual(rows[0]['group'], 'audio_quality_engineering')
        self.assertEqual(rows[0]['attribution']['mixed_tool_groups'], ['audio_quality_engineering', 'research'])
        self.assertEqual(rows[1]['group'], 'source_preparation')
        totals = aggregate(rows)
        groups = grouped(rows, ['group', 'provider', 'model'])
        self.assertEqual(totals['tokens']['reported_total_tokens'], 200)
        self.assertEqual(sum(g['tokens']['reported_total_tokens'] for g in groups), 200)
        self.assertEqual(sum(g['rows'] for g in groups), 2)

    def test_snapshot_reconciliation(self):
        rows, _ = native_rows({CHILD: {'info': {}, 'messages': [message('msg-one', '2026-10-07T00:00:00Z')]}}, CUTOFF)
        totals = {key: value for key, value in rows[0]['tokens'].items() if key != 'normalized_input_tokens'}
        totals['completed_assistant_messages'] = 1
        snapshot = {'captured_at_utc': CUTOFF, 'totals': totals,
                    'sessions': [{'session_id': CHILD, **totals}]}
        result = reconcile(rows, snapshot, [])
        self.assertTrue(result['exact_snapshot_reconciliation'])
        self.assertEqual(result['native_messages_without_gateway_message_receipt'], 1)


class RedactionAndResearchTests(unittest.TestCase):
    def test_identifiers_omit_secrets_hashes_and_free_text(self):
        self.assertIsNone(safe('sk-example-credential'))
        self.assertIsNone(safe('op://vault/item/token'))
        self.assertIsNone(safe('a' * 64))
        self.assertIsNone(safe('Novel paragraph words here'))
        self.assertEqual(safe('gpt-6.1-sol'), 'gpt-6.1-sol')
        self.assertEqual(safe(MAIN), MAIN)

    def test_wrapper_receipts_include_winner_not_just_fallback_attempts(self):
        result = search_attempts('bash', {'command': 'web-search search "a" --json'},
                                 '{"provider":"brave","attempts":[{"provider":"tavily","error":"failed"}]}')
        assert result is not None
        self.assertEqual(result['wrapper_recorded_upstream_attempts'], 2)
        self.assertEqual(result['wrapper_provider_attempts_by_provider'], {'tavily': 1, 'brave': 1})
        self.assertIsNone(result['underlying_http_requests_including_retries'])

    def test_combined_json_receipts_and_text_output(self):
        result = search_attempts('bash', {'command': 'web-search search "a"; web-search search "b"'},
                                 'prefix\n{"provider":"tavily"}\n{"results_by_provider":{"tavily":{},"brave":{}}}')
        assert result is not None
        self.assertEqual(result['wrapper_recorded_upstream_attempts'], 3)
        textual = search_attempts('bash', {'command': 'web-search search "a"'},
                                   'web-search: "a" via brave (2 results)\nnote: tavily: failed')
        assert textual is not None
        self.assertEqual(textual['wrapper_recorded_upstream_attempts'], 2)

    def test_search_invocations_not_generic_mentions(self):
        self.assertIsNone(search_attempts('skill', {'name': 'web-search'}, ''))
        self.assertIsNone(search_attempts('grep', {'pattern': 'web-search search'}, ''))
        self.assertIsNone(search_attempts('task', {'prompt': 'research this'}, ''))
        invocation = search_attempts('bash', {'command': 'web-search search "a" && web-search search "b"'}, '')
        assert invocation is not None
        self.assertEqual(invocation['invocations'], 2)
        attempt = search_attempts('websearch', {}, '{"attempts":[{},{}]}')
        assert attempt is not None
        self.assertEqual(attempt['wrapper_recorded_upstream_attempts'], 2)
        self.assertTrue(attempt['upstream_attempts_known'])


if __name__ == '__main__':
    unittest.main()
