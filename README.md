# BookJangler

An experiment in turning one chapter of a novel into a multi-voice audiobook on local hardware: Chapter One of Pearl S. Buck's *The Good Earth*, read by VoxCPM2 on an RTX 3090 through [voxxy](https://github.com/delorenj/voxxy).

The reusable version of this pipeline is the `ebook-to-audiobook` skill in [delorenj/skills](https://github.com/delorenj/skills/tree/main/ebook-to-audiobook). This repo is the pilot it was distilled from.

## Result

- **Audio:** 50:20 for Chapter One, 11 designed voices (narrator plus 10 speaking characters), 307 local TTS requests (11 voice designs, 296 chapter chunks), about 1,461 s of synthesis.
- **QA:** spot-checked with local faster-whisper-large-v3-turbo on CPU.
- **LLM usage:** 202 calls, 17,277,714 tokens (13,552,862 uncached input, 3,530,240 cache read, 194,612 output).
- **Cost:** $0 actually charged (subscription route; every matched gateway row settled at 0). $29.40 at standard API rates, as a comparison only. GPU energy roughly $0.01–$0.02, assumed rather than measured.
- **Report:** <https://book.delo.sh/good-earth/>, broken down by phase with the full CandyStore event ledger.

## Not in this repo

*The Good Earth* was published in 1931 and enters the US public domain on 2027-01-01. Until then the PDF, the extracted chapter text (`chapter.txt`, `chapter.json`), the reviewed dialogue table (`quote-turns.json`) and every audio file stay local. See `.gitignore`.

## Pipeline

| Script | Does |
| --- | --- |
| `prepare_source.py` | OCR pages to verified chapter text and a speaker-attributed segment manifest |
| `render_chapter.py` | Voice design, chunked synthesis, assembly and mastering against the VoxCPM2 sidecar |
| `verify_audio.py` | Whisper spot-check of rendered chunks |
| `capture_usage.py` | OpenCode per-message token usage, read from its database |
| `collect_report_ledger.py` | CandyStore event ledger for the experiment window |
| `gateway_attribution.py` | Matches OpenCode messages to AutomaticAI gateway rows |
| `build_report_data.py` | Joins ledger, pricing and render metrics into `report-data.json` |
| `render_usage_report.py` | Renders the self-contained `usage-report.html` |

Refresh the report:

```sh
python3 collect_report_ledger.py
python3 gateway_attribution.py
python3 build_report_data.py
python3 render_usage_report.py --data report-data.json --out usage-report.html
```

Tests: `python3 -B -m pytest -q -p no:cacheprovider`
