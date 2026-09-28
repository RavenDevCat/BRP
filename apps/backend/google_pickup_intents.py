"""Operator-selected address intent, separate from provider coordinate evidence."""
from copy import deepcopy
import os
from pathlib import Path
from json_cache_store import load_json_object


def selected_address(point, cache_dir):
    import client_runtime as runtime
    path = Path(os.environ.get('BRP_GOOGLE_PICKUP_INTENTS_PATH') or
                Path(cache_dir)/'google_pickup_intents.json')
    address = str(point.get('address') or '').strip()
    key = runtime.geocode_cache_key(point.get('country', 'China'), point.get('city', ''), address)
    entry = load_json_object(path).get(key)
    if not isinstance(entry, dict):
        return point, None
    required = ('selected_address', 'operator', 'reason', 'confirmed_at')
    if (entry.get('schema') != 1 or entry.get('original_address') != address
            or type(entry.get('revision')) is not int or entry['revision'] < 1
            or any(not isinstance(entry.get(k), str) or not entry[k].strip() for k in required)
            or len(entry['selected_address']) > 500):
        from google_final_validation import ValidationUnavailable
        raise ValidationUnavailable('google_pickup_intent_invalid', details={'address': address})
    evidence = {k: deepcopy(entry[k]) for k in (*required, 'revision')}
    # A name/entrance decision is not approval of any coordinates supplied by a
    # file or cache. The normal provider identity and route checks still run.
    return {**point, 'requested_address': entry['selected_address']}, evidence
