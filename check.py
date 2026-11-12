"""Check a scrubbed transcript against its answer key. See PROJECT.md."""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INPUT_PROBLEM = 2


def read_text(path):
    """Read in NFC, the same form the scrubber uses, so accented names compare equal."""
    return unicodedata.normalize("NFC", Path(path).read_text(encoding="utf-8-sig"))


def is_present(phrase, text):
    """Look for the phrase as whole words, case-sensitive.

    Any run of spaces or line breaks counts as one space, because a phrase may
    wrap onto the next caption line without having been changed.
    """
    pattern = r"\s+".join(map(re.escape, phrase.split()))
    return re.search(rf"(?<!\w){pattern}(?!\w)", text) is not None


def main():
    parser = argparse.ArgumentParser(description="Check a scrubbed file against an answer key.")
    parser.add_argument("scrubbed", help="the scrubbed output file")
    parser.add_argument("key", help="the answer key JSON for that transcript")
    args = parser.parse_args()

    try:
        scrubbed = read_text(args.scrubbed)
        key = json.loads(read_text(args.key))
        must_remove, must_keep = key["must_remove"], key["must_keep"]
    except (OSError, ValueError, KeyError) as error:
        print(f"Could not read the files to check: {error}")
        return EXIT_INPUT_PROBLEM

    missed = damaged = 0
    print("Must be removed:")
    for category, items in must_remove.items():
        print(f"  {category}")
        for item in items:
            survived = is_present(item, scrubbed)
            missed += survived
            print(f"    {'MISSED' if survived else 'caught'}  {item}")

    print("Must be kept:")
    for phrase in must_keep:
        kept = is_present(phrase, scrubbed)
        damaged += not kept
        print(f"    {'kept   ' if kept else 'DAMAGED'}  {phrase}")

    removed_total = sum(len(items) for items in must_remove.values())
    print(f"Total: {removed_total - missed} of {removed_total} identifiers caught, "
          f"{len(must_keep) - damaged} of {len(must_keep)} phrases kept.")
    return EXIT_FAILED if missed or damaged else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
