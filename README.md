# Transcript Scrubber

The Transcript Scrubber removes identifying details from Zoom session transcripts. Names, dates, places, phone numbers, ages, emails, street addresses, schools and workplaces are replaced with coded labels, so a transcript can be analysed without revealing who anyone is.

For example:

    Before:  [Dana Whitfield] 00:00:04
             Hi Marisol, my sister Lucía called me on March 3rd.

    After:   [CLIN-1] 00:00:04
             Hi CLIENT-1, my sister PERSON-1 called me on [DATE].

The same person keeps the same label all the way through a transcript.

Everything happens on this Mac. Transcripts are never uploaded or sent anywhere. The scrubber only talks to the Ollama app on this same computer, and it works with Wi-Fi switched off.


## One-time setup

1. Install Ollama. Download it from https://ollama.com, open it, and follow its steps. When it's running, a llama icon appears in the menu bar at the top of the screen.

2. Add your clinicians' names. Double-click clinicians.txt to open it, and type each clinician's name on its own line, spelled exactly as it appears in Zoom. Save and close it. Speakers listed here are labelled CLIN-1, CLIN-2 and so on; everyone else is labelled CLIENT-1, CLIENT-2 and so on.

3. Open the launcher for the first time. Right-click "Scrub Transcripts" and choose Open. If your Mac says it can't be opened because it comes from an unidentified developer, open System Settings, go to Privacy & Security, scroll down, and click Open Anyway. You only need to do this once.

4. The first run sets the rest up. Your Mac may ask to install "Command Line Tools", Apple's free toolkit that the scrubber needs. Click Install, wait for it to finish, then double-click "Scrub Transcripts" again. Next, the launcher asks to download the AI model. It's about 2 GB, so this takes a few minutes. Type y and press Enter.


## Scrubbing transcripts

1. Copy the Zoom transcript files (.txt) into the input folder.
2. Double-click "Scrub Transcripts".
3. When it finishes, the output folder opens. Each scrubbed file has the original name with _scrubbed added, for example session_01_scrubbed.txt.

A short session takes 10 to 20 seconds; a full-length session may take a few minutes. The original files in the input folder are never changed. Move them somewhere safe, or delete them, once you're done.


## Before you share a scrubbed file

Read it through first. The AI model is small and can occasionally miss a name, especially an unusual one.

If you spot a missed name, double-click names.txt, add the name on its own line, save, and scrub the file again. Any name listed there is always caught from then on.


## If something goes wrong

The window explains what happened in plain English. If a file can't be scrubbed safely, nothing is written for it, so a half-scrubbed file can never end up in the output folder. You may see these messages:

- "Ollama is not running": open the Ollama app and try again. The launcher normally starts it for you.
- "There are no transcripts in the input folder": put the .txt files into the input folder first.
- "The AI model gave an unreadable answer twice in a row": nothing was written for that file. Running it again won't help, so contact your developer.
- "Safety check failed": nothing was written for that file. Contact your developer.


## Good to know

- Labels restart for each transcript. CLIENT-1 in one file is not necessarily the same person as CLIENT-1 in another.
- Relatives who share a surname may get the same label. For example, "Mr. Hanley" and "Greg Hanley" both become PERSON-1. The names are still removed; it only means the two can't be told apart in the scrubbed file.
- If someone is called May or Will, add their name to names.txt. Otherwise it can be mistaken for the ordinary word and left in place.
- The list of who became which label is never saved to a file, because it would be a key to re-identify everyone.
- The AI model used is Qwen 2.5 (3B). It's released under the Qwen Research Licence, which covers non-commercial research and evaluation use. Please check that your use fits those terms.


## Trying it out

The test_transcripts folder holds three invented sessions; none of the people in them are real. To see the scrubber work, copy session_01.txt from there into the input folder and double-click "Scrub Transcripts".


## For developers

The tool is scrubber.py, written in Python using only the standard library. It talks to Ollama at http://127.0.0.1:11434 and nowhere else. Run python3 scrubber.py --help for its options.

check.py compares a scrubbed file with an answer key from test_transcripts:

    python3 check.py output/session_01_scrubbed.txt test_transcripts/session_01_key.json
