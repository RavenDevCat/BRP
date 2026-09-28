import json
import pytest
from google_final_validation import ValidationUnavailable
from google_pickup_intents import selected_address
from test_google_pickup_fallback import resolver, candidate
from test_google_geocoding import point
import client_runtime as runtime

ORIGINAL='\u94f6\u90fd\u540d\u5885 \u5317\u95e8 / \u5357\u95e8'
SELECTED='\u94f6\u90fd\u540d\u5885\u5357\u95e8'

def save(path, **changes):
    entry={'schema':1,'original_address':ORIGINAL,'selected_address':SELECTED,
           'revision':1,'operator':'chat-confirmed operator','reason':'South gate selected',
           'confirmed_at':'2026-09-28T00:00:00+00:00',**changes}
    key=runtime.geocode_cache_key('China','Shanghai',ORIGINAL)
    path.write_text(json.dumps({key:entry}))

def test_selected_name_does_not_claim_operator_confirmed_coordinates(resolver):
    save(resolver.path.parent/'google_pickup_intents.json')
    resolver.fallback.cache_lookup=lambda p: {
        **candidate(SELECTED),'geocode_level':'\u5174\u8da3\u70b9',
        'pickup_resolution_status':'reference_only'}
    resolver.lookup=lambda *a: pytest.fail('Explicit selected name is resolved through its provider evidence')
    result=resolver.resolve(point(ORIGINAL))
    assert result['address']==ORIGINAL
    assert result['requested_address']==SELECTED
    assert result['location_resolution']['source']=='verified_amap_fallback'
    assert result['location_resolution']['address_selection']['revision']==1
    assert 'pickup_override_revision' not in result
    assert resolver.api_calls==0

def test_selected_name_cannot_turn_wrong_gate_into_a_match(resolver):
    from amap_geocode_quality import GeocodePrecisionError
    save(resolver.path.parent/'google_pickup_intents.json')
    resolver.fallback.cache_lookup=lambda p: candidate('\u94f6\u90fd\u540d\u5885\u5317\u95e8')
    def fail(*args):
        raise GeocodePrecisionError('No precise south gate')
    resolver.fallback.lookup=fail
    with pytest.raises(ValidationUnavailable):
        resolver.resolve(point(ORIGINAL))

@pytest.mark.parametrize('change',[{'revision':True},{'operator':''},{'schema':2},{'original_address':'different'}])
def test_invalid_intent_is_not_ignored(tmp_path, change):
    save(tmp_path/'google_pickup_intents.json',**change)
    with pytest.raises(ValidationUnavailable,match='google_pickup_intent_invalid'):
        selected_address(point(ORIGINAL),tmp_path)

def test_input_fields_are_not_trusted_as_a_selection(tmp_path):
    original={**point(ORIGINAL),'selected_address':SELECTED,'operator':'invented'}
    result,evidence=selected_address(original,tmp_path)
    assert result==original and evidence is None
