"""Replace identifiers in therapy transcripts with coded labels. See PROJECT.md."""

import argparse
import re
import sys
import unicodedata
from pathlib import Path

ZOOM_HEADER = re.compile(r"^\[(.+?)\]\s+(\d{1,2}):(\d{2}):(\d{2})\s*$")

EXIT_OK = 0
EXIT_INPUT_PROBLEM = 1


def find_transcripts(path):
    """Accept a single file or a folder, so one command covers both uses."""
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(path.glob("*.txt"))
    return []


def read_transcript(path):
    """Return the text in NFC form, so an accented name has one spelling to match.

    Line endings are left as they are (newline="") because the scrubbed file must
    not differ from the original outside identifiers. A leading byte-order mark is
    dropped (utf-8-sig), otherwise it would hide the first header line.
    """
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return unicodedata.normalize("NFC", handle.read())


def find_speakers(text):
    """List speaker names in order of first appearance; labels are numbered in this order."""
    matches = (ZOOM_HEADER.match(line) for line in text.splitlines())
    return list(dict.fromkeys(match.group(1) for match in matches if match))


def main():
    parser = argparse.ArgumentParser(description="Scrub identifiers from Zoom transcripts.")
    parser.add_argument("path", nargs="?", default="input", type=Path,
                        help="a transcript file or a folder of .txt files (default: input/)")
    args = parser.parse_args()

    transcripts = find_transcripts(args.path)
    if not transcripts:
        print(f"No .txt transcripts found at '{args.path}'. Check the path and try again.")
        return EXIT_INPUT_PROBLEM

    for transcript in transcripts:
        try:
            text = read_transcript(transcript)
        except (OSError, UnicodeDecodeError) as error:
            print(f"Could not read '{transcript}': {error}")
            return EXIT_INPUT_PROBLEM
        print(transcript.name)
        for speaker in find_speakers(text):
            print(f"  {speaker}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
