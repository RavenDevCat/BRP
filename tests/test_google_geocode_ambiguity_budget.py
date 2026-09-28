from copy import deepcopy

import pytest

import google_geocoding as geo
import google_final_validation as google
from test_google_geocoding import payload, point, NOW, resolver


def ambiguous_payload():
    first = payload()['results'][0]
    second = deepcopy(first)
    second['place_id'] = 'distinct-platform'
    second['geometry']['location']['lat'] += .01
    return {'status': 'OK', 'results': [first, second]}


def test_ambiguity_stops_refinements_and_is_negative_cached(resolver):
    queries = []

    def lookup(*args):
        queries.append(args[2])
        args[-1]()
        return ambiguous_payload()

    resolver.lookup = lookup
    with pytest.raises(google.ValidationUnavailable, match='google_geocode_ambiguous') as error:
        resolver.resolve(point())
    assert len(queries) == resolver.api_calls == 1
    assert error.value.details['query_attempts'] == 1
    assert len(error.value.details['matching_place_ids']) == 2

    warm = geo.GoogleGeocodeResolver('warm', lookup=lookup, path=resolver.path, now=lambda: NOW)
    with pytest.raises(google.ValidationUnavailable, match='google_geocode_ambiguous'):
        warm.resolve(point())
    assert len(queries) == 1 and warm.api_calls == 0
    assert warm.negative_cache_hits == 1


def test_ambiguity_on_refinement_stops_before_third_query(resolver, monkeypatch):
    monkeypatch.setattr(geo, 'query_variants', lambda *args: ['query-two', 'query-three'])
    queries = []

    def lookup(*args):
        queries.append(args[2])
        args[-1]()
        return {'status': 'ZERO_RESULTS'} if len(queries) == 1 else ambiguous_payload()

    resolver.lookup = lookup
    with pytest.raises(google.ValidationUnavailable, match='google_geocode_ambiguous') as error:
        resolver.resolve(point())
    assert len(queries) == resolver.api_calls == 2
    assert error.value.details['query_attempts'] == 2


def test_unresolved_still_receives_identity_preserving_refinement(resolver, monkeypatch):
    monkeypatch.setattr(geo, 'query_variants', lambda *args: ['query-two', 'query-three'])
    queries = []

    def lookup(*args):
        queries.append(args[2])
        args[-1]()
        return {'status': 'ZERO_RESULTS'} if len(queries) < 3 else payload()

    resolver.lookup = lookup
    resolved = resolver.resolve(point())
    assert resolved['google_geocode_identity']['query_attempts'] == 3
    assert resolver.api_calls == len(queries) == 3
