# The Scrubber — Project Brief

Read this before writing any code for this project.

## What it does

Takes therapy session transcripts and replaces every identifier with a coded label, so the transcript can be analysed without revealing who anyone is. Runs fully offline on a Mac.

The client's requirement, in full: names, dates, locations, phone numbers, ages, and school or workplace names are replaced with coded labels. The same person keeps the same label through the entire transcript. Output is a plain text file.

## Input format

Zoom caption export. A header line, then what the speaker said:

```
[Speaker Name] HH:MM:SS
text of what they said...
```

Use exactly this regex for header lines — it matches the client's analysis engine, so scrubbed files feed straight into it:

```python
ZOOM_HEADER = re.compile(r"^\[(.+?)\]\s+(\d{1,2}):(\d{2}):(\d{2})\s*$")
```

## Labels

| What | Label |
|---|---|
| Clinician speakers (names listed in `clinicians.txt`) | `CLIN-1`, `CLIN-2` |
| Other speakers | `CLIENT-1`, `CLIENT-2` |
| Other people mentioned in speech | `PERSON-1`, `PERSON-2` |
| Dates | `[DATE]` |
| Phone numbers | `[PHONE]` |
| Ages | `[AGE]` |
| Emails | `[EMAIL]` |
| Street addresses | `[ADDRESS]` |
| Cities, towns, neighbourhoods, counties, states, streets | `[LOCATION]` |
| Schools, colleges, universities | `[SCHOOL]` |
| Employers, companies, hospitals, clinics, agencies | `[WORKPLACE]` |

Header lines keep their format: `[Dana Whitfield] 00:00:04` becomes `[CLIN-1] 00:00:04`.

## Hard rules

1. **Python standard library only.** No `pip install`, no third-party packages of any kind. Talk to Ollama with `urllib`.
2. **Offline.** The only address the script ever contacts is `http://127.0.0.1:11434` (local Ollama). Disable proxies in urllib with `ProxyHandler({})` so traffic can never be routed elsewhere.
3. **The model never writes transcript text.** It only returns a JSON list of identifiers it found — the exact text and a type. The script checks each one appears word for word in the transcript, discards anything that doesn't, and makes every replacement itself.
4. **Nothing outside an identifier changes.** Every other character of the transcript stays exactly as it was.
5. **Same person, same label.** A full name and its first or last name alone all map to the same label within one transcript. Labels restart for each transcript.
6. **Fail closed.** If Ollama isn't running, the model isn't installed, or the model returns unreadable output twice in a row, write nothing for that file. Print a plain-English message and exit with a non-zero code.
7. **Never save the name-to-label mapping to a file.** It's a key that would re-identify everyone. It may only be printed on screen, and only with `--show-map`.
8. **Write output safely.** Write to a temporary file, then rename it into place, so a crash can never leave a half-scrubbed file.
9. **Clean code.** The client reviews the source with AI tools. No dead code, no TODOs, no commented-out blocks. Short docstrings that explain why, not what.

## How detection works

**Rules (regex), applied to speech lines only, never to header lines:**
- Dates: `March 3rd`, `March 3, 2026`, `3rd of March`, `2026-03-15`, and numeric `10/14` or `10/14/2026` — numeric dates only count if month is 1–12 and day is 1–31
- Phone numbers: `(305) 555-0142`, `305-555-0142`, `305.555.0142`, `+1 305 555 0142`
- Ages: `I'm 34`, `61 years old`, `turned 12`, `age 34` — replace only the number, so `61 years old` becomes `[AGE] years old`
- Emails
- Street addresses: `4100 NW 7th Street`, `12 Oak Lane`

**Speaker names** come from the header lines.

**`names.txt`** (optional) lists extra names to always catch, one per line.

**The local model** (`qwen2.5:3b` via Ollama) finds people, locations, schools and workplaces the rules can't.

When two detections overlap, keep the longer one.

## Must NOT be replaced

A month alone (`every March`), a year alone (`back in 2019`), weekdays, `May I ask`, `Will that`, `50/50`, `24/7`, `I'm 5 minutes from`, `my mom`, `God`.

## Files

```
scrubber.py         the tool
clinicians.txt      clinician names, one per line
names.txt           optional extra names, one per line
check.py            test checker
input/              transcripts to scrub
output/             scrubbed results
test_transcripts/   invented test sessions and their answer keys
```

## Command line

```
python3 scrubber.py                    scrub every .txt in input/
python3 scrubber.py FILE_OR_FOLDER     scrub a specific file or folder
python3 scrubber.py --show-map         also print who became which label (screen only)
python3 scrubber.py --no-model         rules only, for development; output named *_RULES_ONLY.txt
python3 scrubber.py --model NAME       use a different Ollama model
```

Output: `output/<name>_scrubbed.txt`

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Input problem (no files found, unreadable file) |
| 2 | Ollama not running, or model not installed |
| 3 | Model returned unreadable output twice |
| 4 | Safety check failed (a name survived, or header lines changed) |

## Testing

```
python3 check.py output/session_01_scrubbed.txt test_transcripts/session_01_key.json
```

Every `must_remove` item must be gone. Every `must_keep` phrase must survive word for word.
