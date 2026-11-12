"""Replace identifiers in therapy transcripts with coded labels. See PROJECT.md."""

import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
import urllib.request
from collections import Counter
from pathlib import Path

ZOOM_HEADER = re.compile(r"^\[(.+?)\]\s+(\d{1,2}):(\d{2}):(\d{2})\s*$")

EXIT_OK = 0
EXIT_INPUT_PROBLEM = 1
EXIT_MODEL_UNAVAILABLE = 2
EXIT_MODEL_UNREADABLE = 3
EXIT_SAFETY_CHECK_FAILED = 4

OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5:3b"

# An opener with an empty proxy list ignores any proxy configured on this Mac,
# so requests can only ever go straight to the local Ollama address above.
OLLAMA = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# Small chunks keep each request well inside the model's context window. The
# overlap gives a name that sits on a chunk boundary a second chance to be seen.
CHUNK_CHARS = 1500
OVERLAP_LINES = 2
MODEL_TIMEOUT_SECONDS = 300

ENTITY_TYPES = ("person", "location", "school", "workplace")

def entity_schema(types):
    """Describe the only reply Ollama will accept: a list of identifiers.

    Enforcing this format means the model has no way to hand back rewritten
    transcript text.
    """
    return {
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "type": {"type": "string", "enum": list(types)},
                    },
                    "required": ["text", "type"],
                },
            }
        },
        "required": ["entities"],
    }


def example_reply(entities):
    return json.dumps(
        {"entities": [{"text": text, "type": kind} for text, kind in entities]},
        ensure_ascii=False,
    )


# Each chunk is put to the model twice. A small model asked for everything at
# once overlooks most people's names; asked about people alone it finds them.
# Each question is (instructions, example text, example reply, allowed types).
PEOPLE_QUESTION = (
    "You find the names of people in therapy session transcripts. List every "
    "person's name that appears in the text: first names, surnames and full "
    "names, including a name that follows a title such as Mr., Mrs., Dr., Coach "
    "or Principal. Copy each name exactly as it is written. Do not list words "
    "like mom, dad, sister or doctor. If there are no names, return an empty list.",
    "And Tomás, how is he? My cousin Bianca saw him with Mr. Petrov. Coach "
    "Lindqvist said he missed practice.",
    example_reply([("Tomás", "person"), ("Bianca", "person"),
                   ("Petrov", "person"), ("Lindqvist", "person")]),
    ("person",),
)
EVERYTHING_QUESTION = (
    "You find identifying details in therapy session transcripts. List every "
    "person, location, school and workplace named in the text. Copy each one "
    "exactly as it is written.\n"
    "person: names of people, whether a first name, a surname or a full name. "
    "Not words like mom, sister or doctor.\n"
    "location: cities, towns, neighbourhoods, counties, states, streets.\n"
    "school: schools, colleges, universities.\n"
    "workplace: employers, companies, hospitals, clinics, agencies.\n"
    "Do not list dates, numbers or ordinary words. If there is nothing to "
    "list, return an empty list.",
    "My brother Tomás got a job at Riverside Hardware in Tacoma. "
    "His daughter goes to Lincoln High.",
    example_reply([("Tomás", "person"), ("Riverside Hardware", "workplace"),
                   ("Tacoma", "location"), ("Lincoln High", "school")]),
    ENTITY_TYPES,
)
QUESTIONS = (PEOPLE_QUESTION, EVERYTHING_QUESTION)

# A second attempt uses a different seed, because repeating the first seed
# would only reproduce the same unreadable answer.
ATTEMPT_SEEDS = (42, 43)
MAX_REPLY_TOKENS = 600

OUTPUT_DIR = Path("output")
CLINICIANS_FILE = Path("clinicians.txt")
EXTRA_NAMES_FILE = Path("names.txt")

# A capitalised word straight after one of these is a person's name, which
# gives a dependable way to catch "Coach Ramirez" without relying on the model.
PERSON_TITLES = (
    "Mr", "Mrs", "Ms", "Dr", "Prof", "Professor", "Coach", "Principal", "Pastor",
    "Officer", "Judge", "Nurse", "Aunt", "Uncle", "Grandma", "Grandpa",
)
TITLES = {title.casefold() for title in PERSON_TITLES} | {"miss"}

PLACE_LABELS = {"location": "[LOCATION]", "school": "[SCHOOL]", "workplace": "[WORKPLACE]"}

# Capitalised words the model sometimes reports that are not identifiers.
# "May" and "Will" are here so "May I ask" and "Will that" survive; a person
# with one of these names is caught from a header line or names.txt instead.
NOT_IDENTIFIERS = {
    "mom", "mum", "mommy", "mama", "mother", "dad", "daddy", "papa", "father",
    "god", "lord", "client", "therapist", "counselor", "doctor", "may", "will",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "june", "july", "august",
    "september", "october", "november", "december",
}

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
    rf"|(?:[Hh]e|[Ss]he|[Ww]ho) (?:is|was)|[Yy]ou{APOSTROPHE}re|[Yy]ou (?:are|were)"
    r"|(?!(?:It|That|This|There|Here|What|Which|Everything|Nothing|Something)\b)"
    rf"[A-Z][\w'’-]+ (?:is|was))"
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


class StopRun(Exception):
    """A problem that must end the run without writing output: fail closed."""

    def __init__(self, exit_code, message):
        super().__init__(message)
        self.exit_code = exit_code
        self.message = message


def ask_ollama(path, payload=None, timeout=10):
    """Send one request to local Ollama; the only place this script touches a network."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        OLLAMA_URL + path, data=data, headers={"Content-Type": "application/json"}
    )
    with OLLAMA.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def check_model_ready(model):
    """Confirm Ollama is up and has the model before any transcript is touched."""
    try:
        installed = {entry["name"] for entry in ask_ollama("/api/tags")["models"]}
    except (OSError, ValueError, KeyError, TypeError):
        raise StopRun(EXIT_MODEL_UNAVAILABLE, (
            "Ollama is not running, so nothing was scrubbed. "
            "Open the Ollama app (llama icon in the menu bar) and run this again."
        )) from None
    if model not in installed and f"{model}:latest" not in installed:
        raise StopRun(EXIT_MODEL_UNAVAILABLE, (
            f"Ollama is running but the model '{model}' is not installed, so nothing "
            f"was scrubbed. Install it with:  ollama pull {model}"
        ))


def chunk_speech(blocks):
    """Group the speech lines into overlapping chunks; header lines are never sent."""
    lines = [line for _, speech in blocks for line in speech.splitlines() if line.strip()]
    chunks, current, fresh_lines = [], [], 0
    for line in lines:
        current.append(line)
        fresh_lines += 1
        if sum(map(len, current)) >= CHUNK_CHARS:
            chunks.append("\n".join(current))
            current = current[-OVERLAP_LINES:]
            fresh_lines = 0
    if fresh_lines:
        chunks.append("\n".join(current))
    return chunks


def read_entities(model, question, chunk, seed):
    """Put one question to the model about a chunk; returns (text, type) pairs.

    Temperature 0 and a fixed seed make the same transcript give the same
    result on every run. The reply length is capped so a model stuck repeating
    itself fails fast. Raises ValueError, KeyError or TypeError on an unusable reply.
    """
    instructions, example_text, example_answer, types = question
    reply = ask_ollama("/api/chat", {
        "model": model,
        "stream": False,
        "format": entity_schema(types),
        "options": {"temperature": 0, "seed": seed, "num_ctx": 4096,
                    "num_predict": MAX_REPLY_TOKENS},
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": example_text},
            {"role": "assistant", "content": example_answer},
            {"role": "user", "content": chunk},
        ],
    }, timeout=MODEL_TIMEOUT_SECONDS)
    entities = json.loads(reply["message"]["content"])["entities"]
    return [(entity["text"], entity["type"]) for entity in entities
            if isinstance(entity["text"], str) and entity["type"] in types]


def find_model_entities(model, question, chunk):
    """Get the model's answer to one question, allowing one retry before giving up."""
    for seed in ATTEMPT_SEEDS:
        try:
            return read_entities(model, question, chunk, seed)
        except OSError:
            raise StopRun(EXIT_MODEL_UNAVAILABLE, (
                "Ollama stopped responding part-way through, so nothing was written "
                "for this file. Check the Ollama app is open and run this again."
            )) from None
        except (ValueError, KeyError, TypeError):
            continue
    raise StopRun(EXIT_MODEL_UNREADABLE, (
        "The model gave an unreadable answer twice in a row, so nothing was written "
        "for this file. Run it again, or try a different model with --model."
    ))


def clean_entity(text, entity_type):
    """Cut a model answer down to the identifier itself.

    "my sister Lucía" becomes "Lucía" and "Greg's cousin" becomes "Greg", so
    the ordinary words around a name are never replaced along with it.
    """
    words = text.split()
    while words and words[0][0].islower():
        words.pop(0)
    while words and words[-1][0].islower():
        words.pop()
    if entity_type == "person":
        while words and words[0].strip(".").casefold() in TITLES:
            words.pop(0)
    cleaned = " ".join(words).strip(".,;:!?\"'’()")
    return re.sub(rf"{APOSTROPHE}s$", "", cleaned)


def is_usable(entity, chunk_text):
    """Accept a model answer only if it is really in the chunk and looks like a name.

    The word-for-word check is what stops an invented or reworded answer from
    ever reaching the transcript.
    """
    return (
        len(entity) >= 2
        and any(character.isupper() for character in entity)
        and entity.casefold() not in NOT_IDENTIFIERS
        and entity in chunk_text
    )


def detect_with_model(model, blocks, registry):
    """Ask the model about every chunk and keep only the answers that pass the checks.

    People are added to the registry so they get consistent PERSON labels.
    Places, schools and workplaces are returned as {text: label}.
    """
    answers = []
    for chunk in chunk_speech(blocks):
        chunk_text = " ".join(chunk.split())
        for question in QUESTIONS:
            for raw_text, entity_type in find_model_entities(model, question, chunk):
                entity = clean_entity(raw_text, entity_type)
                if is_usable(entity, chunk_text):
                    answers.append((entity, entity_type, question))
    people, places = settle_types(answers)
    speech_text = " ".join(" ".join(speech.split()) for _, speech in blocks)
    for name in join_adjacent_names(people, speech_text) + people:
        registry.add_person(name)
    return places


def settle_types(answers):
    """Decide what each answer is when the two questions disagree.

    The people question can only say "person", so it calls a college a person
    too; there the everything question's school or workplace is believed. But
    once the everything question has itself called something a person, it
    stays a person.
    """
    sure_people = {entity for entity, entity_type, question in answers
                   if entity_type == "person" and question is EVERYTHING_QUESTION}
    places = {}
    for entity, entity_type, _ in answers:
        if entity_type != "person" and entity not in sure_people:
            places.setdefault(entity, PLACE_LABELS[entity_type])
    people = [entity for entity, entity_type, _ in answers
              if entity_type == "person" and entity not in places]
    return list(dict.fromkeys(people)), places


def join_adjacent_names(people, speech_text):
    """Rebuild full names the model reported in pieces.

    If "Tyler" and "Brandt" were found separately and "Tyler Brandt" appears
    in the speech, registering the full name first gives both one label.
    """
    return [f"{first} {last}" for first in people for last in people
            if first != last and re.search(rf"(?<!\w){re.escape(f'{first} {last}')}(?!\w)", speech_text)]


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


def add_titled_names(blocks, registry):
    """Register every name that follows a title, such as "Mr. Hanley" or "Coach Ramirez"."""
    titles = "|".join(PERSON_TITLES)
    pattern = re.compile(rf"\b(?:{titles})\.?{GAP}([A-Z][\w'’-]+)")
    for _, speech in blocks:
        for match in pattern.finditer(speech):
            name = re.sub(rf"{APOSTROPHE}s$", "", match.group(1))
            if name.casefold() not in NOT_IDENTIFIERS | TITLES:
                registry.add_person(name)


def find_header_spans(headers, registry):
    """Replace only the name inside each header, so the brackets and timestamp stay."""
    return [(start, end, registry.labels[name]) for start, end, name in headers]


def term_pattern(terms):
    """Build one pattern matching any of the terms as whole words, longest first."""
    longest_first = sorted(terms, key=len, reverse=True)
    alternatives = "|".join(GAP.join(map(re.escape, term.split())) for term in longest_first)
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)")


def find_term_spans(blocks, labels):
    """Find every known name or place in speech, trying longer ones first.

    Matching is case-sensitive with word boundaries, so "Will" the person is
    found but "will" the verb and "Willow" are not. A term may continue across
    a line break, because captions wrap mid-name.
    """
    lookup = {" ".join(term.split()): label for term, label in labels.items()}
    if not lookup:
        return []
    pattern = term_pattern(lookup)
    spans = []
    for offset, speech in blocks:
        for match in pattern.finditer(speech):
            term = " ".join(match.group().split())
            spans.append((offset + match.start(), offset + match.end(), lookup[term]))
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


def check_scrubbed(name, original, scrubbed, terms):
    """Refuse to release output that still holds a known identifier or lost a header.

    This is an independent second look at the finished text, header lines
    included, so a mistake in the replacement steps cannot slip out unnoticed.
    """
    known = [term for term in terms if len(term) >= 3]
    survivors = sorted(set(term_pattern(known).findall(scrubbed))) if known else []
    if survivors:
        raise StopRun(EXIT_SAFETY_CHECK_FAILED, (
            f"Safety check failed for {name}: {len(survivors)} known identifier(s) were "
            "still in the result, so nothing was written for this file."
        ))
    if len(parse(scrubbed)[0]) != len(parse(original)[0]):
        raise StopRun(EXIT_SAFETY_CHECK_FAILED, (
            f"Safety check failed for {name}: the number of speaker header lines "
            "changed, so nothing was written for this file."
        ))


def print_map(registry):
    """Show who became which label, on screen only; the key must never reach a file."""
    print("  Name-to-label map (shown on screen only, never saved to a file):")
    by_label = {}
    for name, label in registry.labels.items():
        by_label.setdefault(label, []).append(name)
    for label, names in by_label.items():
        print(f"    {label}: {', '.join(names)}")


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
    """Turn a StopRun raised anywhere into its plain message and exit code."""
    try:
        return run()
    except StopRun as stop:
        print(stop.message)
        return stop.exit_code


def run():
    parser = argparse.ArgumentParser(description="Scrub identifiers from Zoom transcripts.")
    parser.add_argument("path", nargs="?", default="input", type=Path,
                        help="a transcript file or a folder of .txt files (default: input/)")
    parser.add_argument("--no-model", action="store_true",
                        help="rules only, for development; output is named *_RULES_ONLY.txt")
    parser.add_argument("--show-map", action="store_true",
                        help="print who became which label, on screen only")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"the Ollama model to use (default: {DEFAULT_MODEL})")
    args = parser.parse_args()

    if not args.no_model:
        check_model_ready(args.model)

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
        add_titled_names(blocks, registry)
        places = {} if args.no_model else detect_with_model(args.model, blocks, registry)
        terms = {**places, **registry.labels}
        spans = keep_longest(
            find_rule_spans(blocks)
            + find_header_spans(headers, registry)
            + find_term_spans(blocks, terms)
        )
        scrubbed = apply_spans(text, spans)
        check_scrubbed(transcript.name, text, scrubbed, terms)
        suffix = "_scrubbed_RULES_ONLY.txt" if args.no_model else "_scrubbed.txt"
        output_path = OUTPUT_DIR / f"{transcript.stem}{suffix}"
        write_safely(output_path, scrubbed)

        counts = Counter(label.rstrip("-0123456789") for _, _, label in spans)
        summary = ", ".join(f"{count} {label}" for label, count in sorted(counts.items()))
        print(f"{transcript.name}: {summary or 'nothing replaced'} -> {output_path}")
        if args.show_map:
            print_map(registry)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
