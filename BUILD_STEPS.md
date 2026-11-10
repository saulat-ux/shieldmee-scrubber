# Building the Scrubber — Step by Step

Your working guide. One step at a time. Don't start the next step until the current one passes its check.

Each step has three parts: what to paste into Cursor, how to check it worked, and a git commit.

---

## How to work with Cursor

**Always start a prompt with `@PROJECT.md`.** That tells Cursor's Claude to read the rules first. Without it, it will forget the hard rules and do things like install packages.

**Read what it writes before accepting.** You don't need to understand every line on day one. But if a part makes no sense, ask: *"Explain this function line by line like I'm new to Python."* You'll be explaining this code to Eduardo on the call.

**Say no when it breaks a rule.** If Cursor suggests `pip install`, spaCy, the `ollama` Python package, or letting the model rewrite text — reject it and remind it: *"Standard library only, per @PROJECT.md."*

**Commit after every passing step.** If a later step breaks something, you can always go back.

```
git add -A
git commit -m "Step N: what you did"
```

**If you're stuck for more than 30 minutes,** copy the error and the code into the chat with me.

---

## Step 1 — Read the transcript

**Paste into Cursor:**

> @PROJECT.md Create scrubber.py with only these parts for now: command-line arguments (an optional path that defaults to input/), finding the .txt files to process, reading each file as UTF-8 and normalising it to NFC with unicodedata, and finding the speaker header lines using exactly the ZOOM_HEADER regex in PROJECT.md. For now, just print each file name and its speakers in the order they first appear. Standard library only. Keep it short, then explain what you wrote.

**Check — run in Terminal:**

```
python3 scrubber.py test_transcripts/session_01.txt
```

**Good looks like:** the file name, then `Dana Whitfield` and `Marisol Peña`. Two speakers, in that order. The ñ in Peña displays correctly.

**Commit:** `git commit -am "Step 1: read transcripts and find speakers"`

---

## Step 2 — The rules

**Paste into Cursor:**

> @PROJECT.md Add the rule-based detection described in PROJECT.md: emails, phone numbers, street addresses, dates (with month and day validation for numeric dates), and ages (replace only the number). Apply rules to speech lines only, never to header lines. For each line, collect every match as a span, resolve overlaps by keeping the longest span, then splice the labels in so nothing else changes. Add the --no-model flag so it writes output/<name>_scrubbed_RULES_ONLY.txt. Do not add the model yet.

**Check:**

```
python3 scrubber.py test_transcripts/session_01.txt --no-model
```

Open `output/session_01_scrubbed_RULES_ONLY.txt`.

**Good looks like:**
- `March 3rd`, `10/14`, `October 9th` → `[DATE]`
- `(305) 555-0142` → `[PHONE]`
- `I'm 34` → `I'm [AGE]`, `61 years old` → `[AGE] years old`, `turned 12` → `turned [AGE]`
- The email → `[EMAIL]`, `4100 NW 7th Street` → `[ADDRESS]`
- **Still there, untouched:** `50/50`, `24/7`, `I'm 5 minutes`, `May I ask`, `Every March`, `2019`

Names will still be there. That's expected — they come in the next two steps.

**Commit:** `git commit -am "Step 2: rule-based detection"`

---

## Step 3 — Speakers and consistent labels

**Paste into Cursor:**

> @PROJECT.md Add speaker labelling. Load clinicians.txt and names.txt if they exist (one name per line, skip blank lines and lines starting with #). Label speakers who match a clinician name as CLIN-1, CLIN-2, and everyone else as CLIENT-1, CLIENT-2, in order of first appearance. Replace the name inside each header line but keep the timestamp. Register each speaker's full name plus its first and last name parts — only parts with 3 or more letters that start with a capital, and skip titles like Dr, Mr, Mrs, Ms — so every mention in speech gets that speaker's label. Names from names.txt become PERSON-1, PERSON-2 unless they match a speaker. Build one regex from all registered names, longest first, case-sensitive, with word boundaries, and add those matches to the same span list as the rules.

**Before you run it,** put this in `clinicians.txt`:

```
# One clinician name per line. These speakers get CLIN labels.
Dana Whitfield
```

**Check:**

```
python3 scrubber.py test_transcripts/session_01.txt --no-model
```

**Good looks like:**
- Headers read `[CLIN-1] 00:00:04` and `[CLIENT-1] 00:00:09`
- `Hi Marisol` → `Hi CLIENT-1`
- `Thank you, Dana.` → `Thank you, CLIN-1.`
- Timestamps unchanged

`Lucía`, `Greg`, `Mateo`, `Kendall` and the workplaces will still be there. That's what the model is for.

**Commit:** `git commit -am "Step 3: speaker labels"`

---

## Step 4 — Talk to the model

**Make sure Ollama is running** (llama icon in your menu bar).

**Paste into Cursor:**

> @PROJECT.md Add the Ollama layer using urllib only, with proxies disabled, talking to http://127.0.0.1:11434. First, a health check on /api/tags: if Ollama isn't reachable or the model isn't installed, print a plain-English fix and exit with code 2. Then split the speech text into chunks of about 1500 characters with 2 lines of overlap, and send each chunk to /api/chat with stream false, temperature 0, seed 42, num_ctx 4096, and a JSON schema format that returns {"entities": [{"text": ..., "type": ...}]} where type is person, location, school, or workplace. Include one short example in the prompt. If the response isn't valid JSON, retry once, then exit with code 3. Validate every entity before using it: trim leading lowercase words like "my sister", require at least one capital letter, skip a small stoplist (mom, dad, god, client, therapist, weekdays), and require the text to appear word for word in that chunk. Discard anything that fails. New people become PERSON-n, unless they match a name part already registered.

**Check:**

```
python3 scrubber.py test_transcripts/session_01.txt
```

Open `output/session_01_scrubbed.txt`.

**Good looks like:** `Lucía`, `Greg Hanley`, `Mateo` become `PERSON-` labels. `Mr. Hanley` and `Greg's cousin` use the same label as `Greg Hanley`. `Kendall` and `Fort Lauderdale` become `[LOCATION]`. `Baptist Health` and `Publix` become `[WORKPLACE]`. The two schools become `[SCHOOL]`.

The model won't be perfect. Note what it misses — that's what Step 6 measures.

**Fail-closed check:** quit Ollama from the menu bar icon, then run it again. You should see a clear message, and **no new file in output/**. Reopen Ollama afterwards.

**Commit:** `git commit -am "Step 4: model layer"`

---

## Step 5 — Safety net and output

**Paste into Cursor:**

> @PROJECT.md Add the safety checks. After scrubbing a file, confirm that no registered person name of 3 or more letters remains anywhere in the output, and that the number of header lines is the same as in the input. If either check fails, write nothing and exit with code 4. Write output safely: write to a hidden temporary file in output/, then os.replace it into place. Print a one-line summary per file with counts by category. Add --show-map, which prints each original name and its label to the screen only, with a note that it is never saved.

**Check:**

```
python3 scrubber.py test_transcripts/session_01.txt --show-map
```

**Good looks like:** a summary line with counts, and the map printed on screen. Then look in `output/` — there must be **no** file containing the map.

**Commit:** `git commit -am "Step 5: safety checks"`

---

## Step 6 — The checker

**Paste into Cursor:**

> @PROJECT.md Create check.py. It takes two arguments: a scrubbed output file and an answer key JSON. For every item in must_remove, search the output with word boundaries, case-sensitive, and report it as caught or MISSED, grouped by category. For every phrase in must_keep, report it as kept or DAMAGED. Print a total at the end. Exit with code 1 if anything is missed or damaged, otherwise 0. Standard library only.

**Check:**

```
python3 check.py output/session_01_scrubbed.txt test_transcripts/session_01_key.json
```

**Good looks like:** every identifier caught, every phrase kept.

If something's missed, that's the real work starting. Bring the results here and we'll work out whether it's a rule to add or a prompt to improve.

**Commit:** `git commit -am "Step 6: checker"`

---

## After Step 6

When Eduardo's three transcripts arrive, put them in `test_transcripts/`, write a key for each, and run them through the same check. Those three passing is what the November checkpoint is.
