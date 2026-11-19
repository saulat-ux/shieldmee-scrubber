#!/bin/bash
# Double-click this file to scrub every transcript in the input folder.
# It starts Ollama if needed, offers to download the model the first time,
# then runs scrubber.py and opens the output folder.

cd "$(dirname "$0")" || exit 1

finish() {
    echo
    read -n 1 -s -r -p "Press any key to close this window."
    echo
    exit "$1"
}

ollama_running() {
    curl -s --noproxy '*' -m 2 http://127.0.0.1:11434/api/tags >/dev/null
}

if ! python3 -c "" 2>/dev/null; then
    echo "Python is not set up on this Mac yet."
    echo "If a window asks to install \"Command Line Tools\", click Install."
    echo "When that has finished, double-click Scrub Transcripts again."
    finish 1
fi

if ! ollama_running && open -ga Ollama 2>/dev/null; then
    echo "Starting Ollama..."
    for _ in $(seq 1 30); do
        ollama_running && break
        sleep 1
    done
fi

MODEL=$(python3 -B -c "import scrubber; print(scrubber.DEFAULT_MODEL)")
if ollama_running && ! python3 -B -c "import scrubber; scrubber.check_model_ready('$MODEL')" 2>/dev/null; then
    echo "The AI model ($MODEL) has not been downloaded yet."
    echo "It is about 2 GB and only needs downloading once."
    read -r -p "Type y and press Enter to download it now: " answer
    if [ "$answer" != "y" ] && [ "$answer" != "Y" ]; then
        finish 2
    fi
    OLLAMA_CLI=$(command -v ollama || echo /Applications/Ollama.app/Contents/Resources/ollama)
    "$OLLAMA_CLI" pull "$MODEL" || finish 2
    echo
fi

python3 -B scrubber.py
status=$?
if [ "$status" -eq 0 ]; then
    echo
    echo "Done. Opening the output folder."
    open output
fi
finish "$status"
