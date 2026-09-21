from copy import deepcopy
from datetime import timedelta
import json

import pytest
import google_address_identity as identity
import google_geocoding as geo
import google_final_validation as google
import client_runtime as runtime
from test_google_geocoding import payload, point, NOW, resolver


def item(address, *, place_id="a", lat=31.2, types=None, partial=False):
    return payload(lat=lat, formatted_address="\u4e0a\u6d77\u5e02"+address,
                   place_id=place_id, types=types or ["street_address"], partial_match=partial)["results"][0]


def choose(address, candidates):
    return geo.select_location({"status":"OK", "results":candidates}, "China", "Shanghai", address)


@pytest.mark.parametrize("partial", [True, False])
def test_partial_flag_is_not_identity(partial):
    result = choose("\u6d4b\u8bd5\u8def238\u53f7", [item("\u6d4b\u8bd5\u8def238\u53f7", partial=partial)])
    assert result["identity_evidence"]["identity_status"] == "matched"
    assert result["identity_evidence"]["route_endpoint_status"] == "not_measured"
    assert result["identity_evidence"]["entrance_status"] == "not_verified"


@pytest.mark.parametrize("requested,returned,reason", [
    ("\u6d4b\u8bd5\u8def1500\u5f04", "\u6d4b\u8bd5\u8def1500\u53f7", "lane_mismatch"),
    ("\u6d4b\u8bd5\u8def23\u53f7", "\u6d4b\u8bd5\u8def123\u53f7", "street_number_mismatch"),
    ("\u793a\u4f8b\u82b1\u56ed\u4e1c\u95e8", "\u793a\u4f8b\u82b1\u56ed\u897f\u95e8", "gate_unconfirmed"),
    ("\u793a\u4f8b\u82b1\u56ed\u5317\u95e8", "\u793a\u4f8b\u82b1\u56ed", "gate_unconfirmed"),
    ("\u793a\u4f8b\u82b1\u56ed\u5bf9\u9762", "\u793a\u4f8b\u82b1\u56ed", "road_side_unconfirmed"),
    ("\u793a\u4f8b\u82b1\u56ed", "\u5176\u4ed6\u82b1\u56ed", "landmark_unconfirmed"),
])
def test_nonpartial_cannot_hide_identity_loss(requested, returned, reason):
    with pytest.raises(google.ValidationUnavailable) as error:
        choose(requested, [item(returned)])
    assert reason in error.value.details["reason_counts"]


def test_bus_stop_not_replaced_with_intersection():
    address = "\u6d4b\u8bd5\u8def\u793a\u4f8b\u8def\u516c\u4ea4\u7ad9"
    with pytest.raises(google.ValidationUnavailable) as error:
        choose(address, [item("\u6d4b\u8bd5\u8def\u793a\u4f8b\u8def", types=["intersection"], partial=True)])
    assert "bus_platform_unconfirmed" in error.value.details["reason_counts"]
    good = item("\u6d4b\u8bd5\u8def\u793a\u4f8b\u8def", types=["transit_station"], partial=True)
    assert choose(address, [good])["google_place_id"] == "a"


def test_nearby_opposite_platforms_still_ambiguous():
    address = "\u6d4b\u8bd5\u8def\u793a\u4f8b\u8def\u516c\u4ea4\u7ad9"
    candidates = [item(address, types=["transit_station"], place_id=str(i), lat=31.2+i*0.0001) for i in range(2)]
    with pytest.raises(google.ValidationUnavailable, match="ambiguous"):
        choose(address, candidates)


def test_unrelated_far_candidate_does_not_block_exact_identity():
    address = "\u6d4b\u8bd5\u8def238\u53f7"
    good = item(address)
    wrong = item("\u6d4b\u8bd5\u8def888\u53f7", place_id="wrong", lat=31.25)
    assert choose(address, [wrong, good])["google_place_id"] == "a"
    assert choose(address, [good, wrong])["google_place_id"] == "a"


def test_duplicate_provider_representation_not_ambiguous():
    good = item("\u6d4b\u8bd5\u8def238\u53f7")
    assert choose("\u6d4b\u8bd5\u8def238\u53f7", [good, deepcopy(good)])["google_place_id"] == "a"


def test_full_road_not_accepted_even_if_name_matches():
    with pytest.raises(google.ValidationUnavailable) as error:
        choose("\u6d4b\u8bd5\u8def", [item("\u6d4b\u8bd5\u8def", types=["route"])])
    assert "coarse_place" in error.value.details["reason_counts"]


def test_wrong_city_rejected():
    value = item("\u6d4b\u8bd5\u8def238\u53f7", lat=22.5)
    value["formatted_address"] = "\u6df1\u5733\u5e02\u6d4b\u8bd5\u8def238\u53f7"
    with pytest.raises(google.ValidationUnavailable):
        choose("\u6d4b\u8bd5\u8def238\u53f7", [value])


def test_district_not_discarded_during_normalization():
    requested = "\u4e0a\u6d77\u5e02\u5f90\u6c47\u533a\u6d4b\u8bd5\u8def238\u53f7"
    with pytest.raises(google.ValidationUnavailable) as error:
        choose(requested, [item("\u957f\u5b81\u533a\u6d4b\u8bd5\u8def238\u53f7")])
    assert "district_mismatch" in error.value.details["reason_counts"]


def test_side_and_gate_can_be_adjacent():
    spec = identity.parse_address("\u793a\u4f8b\u82b1\u56ed\u5317\u95e8\u5bf9\u9762")
    assert spec.gates == {"\u5317\u95e8"} and spec.sides == ("\u5bf9\u9762",)


def test_query_refinement_preserves_gate_number_and_side():
    address = "\u6d4b\u8bd5\u8def1500\u5f04\u5317\u95e8\u5bf9\u9762"
    variants = identity.query_variants(address, "Shanghai", {"results":[]})
    assert len(variants) <= 2
    assert all(address in v for v in variants)


def test_bounded_refinements_and_persistent_failure_cache(resolver):
    queries = []
    def lookup(*args):
        queries.append(args[2]); args[-1]()
        return {"status":"ZERO_RESULTS"}
    resolver.lookup = lookup
    with pytest.raises(google.ValidationUnavailable):
        resolver.resolve(point())
    assert 1 <= len(queries) <= 3
    new = geo.GoogleGeocodeResolver("new", lookup=lookup, path=resolver.path, now=lambda: NOW)
    with pytest.raises(google.ValidationUnavailable):
        new.resolve(point())
    assert new.api_calls == 0 and new.negative_cache_hits == 1
    new.now = lambda: NOW + timedelta(minutes=16)
    with pytest.raises(google.ValidationUnavailable):
        new.resolve(point())
    assert new.api_calls >= 1


def test_systemic_failure_not_retried_or_negative_cached(resolver):
    calls = []
    def lookup(*args):
        calls.append(1)
        raise google.ValidationUnavailable("google_budget_cap")
    resolver.lookup = lookup
    with pytest.raises(google.ValidationUnavailable, match="budget_cap"):
        resolver.resolve(point())
    assert calls == [1] and not resolver.path.exists()


def test_query_cannot_validate_itself(resolver):
    resolver.lookup = lambda *args: payload(formatted_address="Shanghai")
    with pytest.raises(google.ValidationUnavailable):
        resolver.resolve(point())


def test_old_policy_is_not_reused(resolver):
    resolver.resolve(point())
    entries = json.loads(resolver.path.read_text())
    next(iter(entries.values()))["policy"] = "google-geocode-v1"
    resolver.path.write_text(json.dumps(entries))
    resolver.resolve(point())
    assert resolver.api_calls == 2


def test_raw_candidates_retained_across_refinements(resolver):
    good = payload()["results"][0]
    other = {**deepcopy(good), "place_id":"b"}
    other["geometry"]["location"]["lat"] += .01
    values = iter([{"status":"OK", "results":[good, other]}, {"status":"OK", "results":[good]}])
    resolver.lookup = lambda *args: next(values)
    with pytest.raises(google.ValidationUnavailable, match="ambiguous"):
        resolver.resolve(point())
