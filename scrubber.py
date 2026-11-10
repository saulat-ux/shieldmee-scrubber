"""Replace identifiers in therapy transcripts with coded labels. See PROJECT.md."""

import argparse
import os
import re
import sys
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path

ZOOM_HEADER = re.compile(r"^\[(.+?)\]\s+(\d{1,2}):(\d{2}):(\d{2})\s*$")

EXIT_OK = 0
EXIT_INPUT_PROBLEM = 1
EXIT_MODEL_UNAVAILABLE = 2

OUTPUT_DIR = Path("output")
CLINICIANS_FILE = Path("clinicians.txt")
EXTRA_NAMES_FILE = Path("names.txt")

TITLES = {"dr", "mr", "mrs", "ms", "miss", "prof"}

# Captions wrap long sentences, so an identifier may continue on the next line.
# GAP allows one line break between words, but never a blank line.
GAP = r"(?:[ \t]+|[ \t]*\r?\n[ \t]*)"
APOSTROPHE = "['’]"

MONTH = (
    r"(?:January|February|March|April|May|June|July|August|September|October"
    r"|November|December|(?:Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\.?)(?!\w)"
)
DAY = r"(?:3[01]|[12]\d|0?[1-9])(?:st|nd|rd|th)?(?!\w)"
YEAR = r"(?:19|20)\d{2}(?!\w)"

STREET_WORD = rf"(?:[A-Z][\w'’-]*|\d+(?:st|nd|rd|th))"
STREET_TYPE = (
    r"(?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Dr|Boulevard|Blvd|Court|Ct"
    r"|Way|Place|Pl|Terrace|Circle|Highway|Hwy|Parkway|Trail)(?!\w)"
)

# A number after "I'm" or "turned" is an age unless one of these follows it,
# which is how "I'm 5 minutes from" and "I'm 100 percent sure" are left alone.
NOT_AN_AGE_WORD = (
    r"seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?"
    r"|miles?|blocks?|feet|foot|inch(?:es)?|pounds?|lbs?|kilos?|percent|times"
    rf"|of|out|for|to|am|pm|a\.m|p\.m|o{APOSTROPHE}clock"
)
AGE_NUMBER = rf"(\d{{1,3}})(?![\w/:%'’-]|\.\d|{GAP}(?:{NOT_AN_AGE_WORD})\b)"
AGE_LEAD_IN = (
    rf"(?:I{APOSTROPHE}m|I am|I was|(?:[Hh]e|[Ss]he|[Ww]ho){APOSTROPHE}s"
    rf"|(?:[Hh]e|[Ss]he|[Ww]ho) (?:is|was)|[Yy]ou{APOSTROPHE}re|[Yy]ou (?:are|were))"
    rf"(?:{GAP}(?:just|only|almost|nearly|about|already))?"
)

# Each rule is (label, pattern). A pattern with a capture group replaces only
# that group, which is how "61 years old" becomes "[AGE] years old".
RULES = [
    ("[EMAIL]", re.compile(r"[\w.%+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("[PHONE]", re.compile(
        r"(?<![\w+])(?:\+?1[ .-]?)?(?:\(\d{3}\) ?|\d{3}[ .-])\d{3}[ .-]\d{4}(?!\w)"
    )),
    ("[PHONE]", re.compile(r"(?<![\w-])(?:\d{3}[.-]\d{4}|\d{10})(?!\w)")),
    ("[ADDRESS]", re.compile(
        rf"(?<![\w/])\d{{1,6}}[A-Za-z]?(?:{GAP}{STREET_WORD}){{1,4}}{GAP}{STREET_TYPE}"
    )),
    ("[DATE]", re.compile(rf"\b{MONTH}{GAP}{DAY}(?:,?{GAP}{YEAR})?")),
    ("[DATE]", re.compile(rf"(?<![\w/]){DAY}{GAP}(?:of{GAP})?{MONTH}(?:,?{GAP}{YEAR})?")),
    ("[DATE]", re.compile(r"\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b")),
    ("[DATE]", re.compile(
        r"(?<![\w/.])(?:0?[1-9]|1[0-2])/(?:3[01]|[12]\d|0?[1-9])(?:/(?:\d{4}|\d{2}))?(?![\w/])"
    )),
    ("[AGE]", re.compile(rf"\b{AGE_LEAD_IN}{GAP}{AGE_NUMBER}")),
    ("[AGE]", re.compile(rf"\b(?:[Tt]urn(?:ed|ing|s)?|[Aa]ged?|[Aa]ge of){GAP}{AGE_NUMBER}")),
    ("[AGE]", re.compile(
        rf"(?<![\w/])(\d{{1,3}})(?:{GAP}|-)(?:years?|yrs?|months?)(?:{GAP}|-)old\b"
    )),
]


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


def load_names(path):
    """Read one name per line, in NFC so the names match the transcript text."""
    if not path.is_file():
        return []
    lines = unicodedata.normalize("NFC", path.read_text(encoding="utf-8-sig")).splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def parse(text):
    """Separate header lines from speech, so the two can never be treated alike.

    Returns the speaker name in each header as (start, end, name), and each
    stretch of speech between headers as (offset, speech).
    """
    headers, blocks = [], []
    block_start = position = 0
    for line in text.splitlines(keepends=True):
        match = ZOOM_HEADER.match(line)
        if match:
            headers.append((position + match.start(1), position + match.end(1), match.group(1)))
            if position > block_start:
                blocks.append((block_start, text[block_start:position]))
            block_start = position + len(line)
        position += len(line)
    if position > block_start:
        blocks.append((block_start, text[block_start:position]))
    return headers, blocks


def name_parts(name):
    """Split a name into the parts worth matching alone.

    Short parts, lowercase parts and titles are left out because they would
    match ordinary words: "Dr" alone must not become a label.
    """
    words = [word.strip(".,") for word in name.split()]
    return [word for word in words
            if len(word) >= 3 and word[0].isupper() and word.casefold() not in TITLES]


class Registry:
    """The name-to-label key for one transcript. It lives in memory only, never on disk."""

    def __init__(self):
        self.labels = {}
        self.counts = Counter()

    def new_label(self, prefix):
        self.counts[prefix] += 1
        return f"{prefix}-{self.counts[prefix]}"

    def register(self, name, label):
        """Point a full name and its parts at one label; the first owner of a part keeps it."""
        for form in [name, *name_parts(name)]:
            self.labels.setdefault(form, label)

    def add_speaker(self, name, is_clinician):
        if name not in self.labels:
            self.register(name, self.new_label("CLIN" if is_clinician else "CLIENT"))

    def add_person(self, name):
        """Reuse the label of anyone already known by this name or one of its parts."""
        known = [self.labels[form] for form in [name, *name_parts(name)] if form in self.labels]
        self.register(name, known[0] if known else self.new_label("PERSON"))


def build_registry(headers, clinicians, extra_names):
    """Label speakers in order of first appearance, then the extra names."""
    clinician_keys = {" ".join(name.split()).casefold() for name in clinicians}
    registry = Registry()
    for _, _, speaker in headers:
        registry.add_speaker(speaker, " ".join(speaker.split()).casefold() in clinician_keys)
    for name in extra_names:
        registry.add_person(name)
    return registry


def find_header_spans(headers, registry):
    """Replace only the name inside each header, so the brackets and timestamp stay."""
    return [(start, end, registry.labels[name]) for start, end, name in headers]


def find_name_spans(blocks, registry):
    """Find every registered name in speech, trying longer names first.

    Matching is case-sensitive with word boundaries, so "Will" the person is
    found but "will" the verb and "Willow" are not.
    """
    if not registry.labels:
        return []
    longest_first = sorted(registry.labels, key=len, reverse=True)
    alternatives = "|".join(GAP.join(map(re.escape, name.split())) for name in longest_first)
    pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)")
    spans = []
    for offset, speech in blocks:
        for match in pattern.finditer(speech):
            name = " ".join(match.group().split())
            spans.append((offset + match.start(), offset + match.end(), registry.labels[name]))
    return spans


def find_rule_spans(blocks):
    """Collect (start, end, label) for every rule match, as positions in the full text."""
    spans = []
    for offset, speech in blocks:
        for label, pattern in RULES:
            for match in pattern.finditer(speech):
                start, end = match.span(pattern.groups)
                spans.append((offset + start, offset + end, label))
    return spans


def keep_longest(spans):
    """Where detections overlap, keep the longer one so no identifier is half-replaced."""
    kept = []
    for span in sorted(spans, key=lambda span: (span[0] - span[1], span[0])):
        if all(span[1] <= other[0] or span[0] >= other[1] for other in kept):
            kept.append(span)
    return sorted(kept)


def apply_spans(text, spans):
    """Splice labels into the text, leaving every character outside a span untouched.

    A line break inside a replaced identifier is kept in front of its label, so
    the transcript keeps the same number of lines.
    """
    pieces = []
    position = 0
    for start, end, label in spans:
        line_breaks = "".join(re.findall(r"\r?\n", text[start:end]))
        pieces += [text[position:start], line_breaks, label]
        position = end
    pieces.append(text[position:])
    return "".join(pieces)


def write_safely(path, text):
    """Write to a hidden temporary file and rename it, so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", dir=path.parent,
        prefix=".", suffix=".tmp", delete=False,
    ) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(text)
        except BaseException:
            temporary.unlink()
            raise
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description="Scrub identifiers from Zoom transcripts.")
    parser.add_argument("path", nargs="?", default="input", type=Path,
                        help="a transcript file or a folder of .txt files (default: input/)")
    parser.add_argument("--no-model", action="store_true",
                        help="rules only, for development; output is named *_RULES_ONLY.txt")
    args = parser.parse_args()

    if not args.no_model:
        print("The model layer is not built yet, so a full scrub is not possible. "
              "Run again with --no-model to apply the rules only.")
        return EXIT_MODEL_UNAVAILABLE

    clinicians = load_names(CLINICIANS_FILE)
    extra_names = load_names(EXTRA_NAMES_FILE)
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
        headers, blocks = parse(text)
        registry = build_registry(headers, clinicians, extra_names)
        spans = keep_longest(
            find_header_spans(headers, registry)
            + find_name_spans(blocks, registry)
            + find_rule_spans(blocks)
        )
        output_path = OUTPUT_DIR / f"{transcript.stem}_scrubbed_RULES_ONLY.txt"
        write_safely(output_path, apply_spans(text, spans))

        counts = Counter(label.rstrip("-0123456789") for _, _, label in spans)
        summary = ", ".join(f"{count} {label}" for label, count in sorted(counts.items()))
        print(f"{transcript.name}: {summary or 'nothing replaced'} -> {output_path}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
