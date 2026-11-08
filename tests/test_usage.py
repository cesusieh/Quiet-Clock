import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridge'))
from quiet_island.usage import quota_summary, claude_quota_summary, published_claude_usage


def test_quota_is_remaining_account_allowance_not_context_or_weekly_limit():
    response = {'rateLimitsByLimitId': {'codex': {
        'primary': {'usedPercent': 36, 'windowDurationMins': 300, 'resetsAt': 2000},
        'secondary': {'usedPercent': 13, 'windowDurationMins': 10080, 'resetsAt': 9000},
        'accountId': 'DO-NOT-RETAIN'}}, 'accountId': 'DO-NOT-RETAIN'}
    assert quota_summary(response, now=1000) == {'kind': 'quota', 'remaining': 64,
        'window_minutes': 300, 'resets_at': 2000, 'updated_at': 1000}


@pytest.mark.parametrize('used,remaining', [(0, 100), (100, 0), (12.5, 87.5)])
def test_full_empty_and_fractional_allowance(used, remaining):
    assert quota_summary({'rateLimits': {'primary': {'usedPercent': used,
        'windowDurationMins': 300, 'resetsAt': 2000}}}, now=1000)['remaining'] == remaining


@pytest.mark.parametrize('response', [None, {}, {'rateLimits': []}, {'rateLimits': 'bad'},
    {'rateLimitsByLimitId': ['bad']}, {'rateLimits': {'primary': {'usedPercent': -1}}},
    {'rateLimits': {'primary': {'usedPercent': True, 'windowDurationMins': 300, 'resetsAt': 2000}}}])
def test_missing_or_invalid_data_never_becomes_a_percentage(response):
    assert quota_summary(response, now=1000) is None


def test_claude_uses_five_hour_quota_without_confusing_context_or_weekly_usage():
    payload = {'rate_limits': {'five_hour': {'used_percentage': 23.5, 'resets_at': 2000},
        'seven_day': {'used_percentage': 80, 'resets_at': 9000}},
        'context_window': {'remaining_percentage': 99}, 'session_id': 'PRIVATE',
        'transcript_path': 'PRIVATE', 'workspace': {'current_dir': 'PRIVATE'}}
    expected = {'kind': 'quota', 'remaining': 76.5, 'window_minutes': 300,
        'resets_at': 2000, 'updated_at': 1000}
    assert claude_quota_summary(payload, now=1000) == expected
    assert published_claude_usage({'tool': 'claude', 'usage': expected}, now=1001) == {**expected, 'updated_at': 1001}


def test_claude_without_rate_headers_stays_unknown_even_with_context_data():
    assert claude_quota_summary({'context_window': {'remaining_percentage': 90}}) is None
    assert published_claude_usage({'tool': 'claude', 'usage': None}, now=1000) == {'kind': 'unavailable', 'updated_at': 1000}
    with pytest.raises(ValueError):
        published_claude_usage({'tool': 'claude', 'usage': {'kind': 'quota', 'remaining': 101}})




def test_context_metrics_are_validated_and_not_account_quota():
    from quiet_island.usage import claude_context_summary
    assert claude_context_summary({'context_window': {'remaining_percentage': 82},
        'rate_limits': {'five_hour': {'used_percentage': 75}}}, now=1000) == {
            'kind': 'context', 'remaining': 82, 'updated_at': 1000}
    for value in [None, True, -1, 101, float('nan'), '82']:
        assert claude_context_summary({'context_window': {'remaining_percentage': value}}) is None


def test_codex_context_reads_last_call_not_accumulated_tokens(tmp_path):
    import json
    from quiet_island.usage import codex_context_file
    path = tmp_path / 'session.jsonl'
    def record(used):
        return json.dumps({'type':'event_msg','payload':{'type':'token_count','info':{
            'model_context_window':1000,'last_token_usage':{'total_tokens':used},
            'total_token_usage':{'total_tokens':9000}}}})+'\n'
    path.write_text(record(200)+json.dumps({'type':'response_item','payload':{'text':'PRIVATE'}})+'\n'+record(350))
    value = codex_context_file(str(path), root=tmp_path)
    assert value['kind'] == 'context' and value['remaining'] == 65
    assert 'PRIVATE' not in str(value)
    path.write_text(record(1200))
    assert codex_context_file(str(path), root=tmp_path)['remaining'] == 0
    assert codex_context_file(str(path), root=tmp_path / 'other') is None
    path.write_text('invalid\n')
    assert codex_context_file(str(path), root=tmp_path) is None
