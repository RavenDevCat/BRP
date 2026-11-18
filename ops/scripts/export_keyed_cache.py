"""Offline rollback: export one quiesced keyed cache to a new JSON file."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"apps"))
from brp_storage.keyed_cache import export

if __name__=="__main__":
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("legacy_path",type=Path)
    cli.add_argument("new_json",type=Path)
    args=cli.parse_args()
    export(args.legacy_path,args.new_json)
