from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/backend'))
from runtime_store_sqlite import SqliteRuntimeStore
from direct_school_review import route_coverage


@pytest.mark.parametrize('complete', [True, False, 1, None])
def test_history_projection_preserves_coverage_without_geometry(tmp_path, complete):
    store = SqliteRuntimeStore(tmp_path / 'runtime.sqlite')
    evidence = {'complete': complete, 'provider': 'amap', 'source': 'amap_continuous_waypoint_legs',
                'issues': [{'code': 'provider_distance_disagreement'}],
                'leg_durations_s': [60], 'leg_distances_m': [100], 'point_count': 2,
                'duration_s': 60, 'distance_m': 100,
                'geometry_segments': [[[120, 30], [121, 31]]] * 1000}
    result = {'status': 'partial', 'summary': {'address_count': 3, 'route_count': 4},
              'routes': [{'status': 'resolved'}, {'status': 'failed', 'route_evidence': evidence},
                         {'status': 'failed'}]}
    record = {'job_id': 'one', 'owner_email': 'owner@example.com', 'status': 'succeeded',
              'metadata': {'job_kind': 'direct_school_analysis'}, 'result': result}
    store.upsert_job(record)
    before = deepcopy(store.get_job('one'))
    projected = store.get_job_result_summaries(['one', 'missing', 'one'])
    assert set(projected) == {'one'}
    assert route_coverage(projected['one']) == route_coverage(result)
    assert projected['one']['summary'] == result['summary']
    assert 'geometry' not in json.dumps(projected)
    assert store.get_job('one') == before
    assert store.get_job_result_summaries([]) == {}


def test_history_empty_job_and_parameterized_ids(tmp_path):
    store = SqliteRuntimeStore(tmp_path / 'runtime.sqlite')
    store.upsert_job({'job_id': "job'1", 'owner_email': 'a@example.com', 'status': 'queued', 'metadata': {}})
    result = store.get_job_result_summaries(["job'1", "' OR 1=1 --"])
    assert set(result) == {"job'1"}
    assert result["job'1"] == {'status': None, 'summary': {}, 'routes': []}


def test_history_lists_only_visible_direct_jobs_without_detail_load(monkeypatch):
    import backend_service as backend
    class Store:
        def list_jobs(self, user_email='', include_all=False):
            assert user_email == 'a@example.com' and not include_all
            return [{'job_id': 'direct', 'metadata': {'job_kind': 'direct_school_analysis'}},
                    {'job_id': 'audit', 'metadata': {}}]
        def get_job_result_summaries(self, ids):
            assert ids == ['direct']
            return {'direct': {'status': 'complete', 'summary': {'address_count': 2}, 'routes': []}}
        def get_job(self, _):
            raise AssertionError('History must not load full job details')
    monkeypatch.setattr(backend, 'JOB_STORE', Store())
    result = backend._direct_school_jobs(user_email='a@example.com', include_all=False)
    assert [r['job_id'] for r in result] == ['direct']
    assert result[0]['result_summary']['address_count'] == 2
