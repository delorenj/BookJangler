import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import TypedDict


DEFAULT_OCR_DIR = Path("/home/delorenj/.claude/tmp/opencode/bookjangler-ocr")
FIRST_PAGE = 8
LAST_PAGE = 31
EXPECTED_QUOTE_COUNT = 117
QUOTE_PATTERN = re.compile(r"“([^“”]*)”")
WORD_PATTERN = re.compile(r"\b\w+(?:[’'-]\w+)*\b")
JOIN_PREVIOUS = {10, 12, 13, 14, 15, 17, 19, 21, 22, 23, 24, 25, 26, 27, 31}
FOLIOS = {
    8: "3",
    9: "4",
    10: "5:",
    11: "6",
    12: "7",
    13: "8",
    14: "9",
    15: "10",
    16: "in",
    17: "2",
    18: "eo:",
    19: "14",
    20: "aS!",
    21: "16",
    22: "17",
    23: "18",
    24: "19",
    25: "20",
    26: "21",
    27: "22",
    28: "23",
    29: "24",
    30: "25",
    31: "26",
}
CORRECTIONS = {
    8: (("Ir was", "It was"),),
    9: (
        ("carthen", "earthen"),
        ("betore", "before"),
        ("ycars", "years"),
        ("floor, Every", "floor. Every"),
    ),
    10: (("‘Now", "Now"),),
    11: (
        ("‘The", "The"),
        ("‘Tea", "Tea"),
        ("“Tt", "“It"),
        ("“Ie will", "“It will"),
    ),
    12: (("“Tt", "“It"),),
    13: (("over his checks", "over his cheeks"),),
    14: (("would vass", "would pass"),),
    16: (
        ("to sce", "to see"),
        ("re-\n\nlied.\n\nE Well", "replied.\n\nWell"),
    ),
    17: (("‘Then", "Then"),),
    18: (
        ("“T cannot", "“I cannot"),
        ("“Tt", "“It"),
        ("“Ihave", "“I have"),
    ),
    19: (
        ("I starvel”", "I starve!”"),
        ("“I£", "“If"),
        ("in a field,", "in a field."),
    ),
    20: (
        ("‘This", "This"),
        ("‘The", "The"),
        ("difficully", "difficulty"),
        ("yave", "gave"),
        ("shouted roughly.", "shouted roughly,"),
        ("bride.\ngroom", "bride-\ngroom"),
    ),
    21: (
        ("kill youl”", "kill you!”"),
        ("‘Then", "Then"),
        ("him.\n“I am a poor man,”", "him.\n\n“I am a poor man,”"),
    ),
    22: (("collecung", "collecting"),),
    23: (
        ("“J am", "“I am"),
        ("“T am", "“I am"),
        ("woman ?”", "woman?”"),
    ),
    24: (("cnough", "enough"),),
    25: (
        ("her kind, She", "her kind. She"),
        ("need, Only", "need. Only"),
        ("anyone, Take", "anyone. Take"),
        ("another word,", "another word."),
    ),
    26: (("mouthi", "mouth"), ("grufly", "gruffly")),
    29: (("‘Two", "Two"), ("the table, I", "the table. I")),
    31: (("‘Then", "Then"),),
}
REFERENCE_TEXT = (
    "The morning light falls across the room, and a gentle breeze moves "
    "the curtains beside the open window."
)
VOICES = {
    "narrator": {
        "description": "Warm mature woman, measured literary delivery in clear American English.",
        "reference_text": REFERENCE_TEXT,
    },
    "wang_lung": {
        "description": "Adult male, earnest light baritone; rural dignity with nervous hesitation.",
        "reference_text": REFERENCE_TEXT,
    },
    "father": {
        "description": "Elderly male, dry weathered voice, grumbling but clear.",
        "reference_text": REFERENCE_TEXT,
    },
    "uncle": {
        "description": "Middle-aged male, jovial and sly, easy conversational warmth.",
        "reference_text": REFERENCE_TEXT,
    },
    "vendor": {
        "description": "Adult male, brisk street call; neutral collective voice for unnamed vendors.",
        "reference_text": REFERENCE_TEXT,
    },
    "barber": {
        "description": "Adult male, amused nimble delivery in a clear middle register.",
        "reference_text": REFERENCE_TEXT,
    },
    "waiter": {
        "description": "Adolescent boy, brisk impatient delivery with a light tenor.",
        "reference_text": REFERENCE_TEXT,
    },
    "beggar": {
        "description": "Adult male, weary thin voice, pleading without exaggeration.",
        "reference_text": REFERENCE_TEXT,
    },
    "gatekeeper": {
        "description": "Adult male, booming rough baritone, self-important and forceful.",
        "reference_text": REFERENCE_TEXT,
    },
    "old_mistress": {
        "description": "Elderly woman, thin imperious voice, deliberate cultured delivery.",
        "reference_text": REFERENCE_TEXT,
    },
    "o_lan": {
        "description": (
            "Twenty-year-old woman, grounded plain restrained middle-low register; "
            "neither loud nor soft, not ill-tempered."
        ),
        "reference_text": REFERENCE_TEXT,
    },
}
FACTUAL_CAST = {
    "narrator": {
        "evidence": "Narration and unattributed general saying; performer gender and accent are not specified.",
        "pdf_pages": [8, 31],
    },
    "wang_lung": {
        "evidence": (
            "Farmer on his marriage day; dark slender body; mutters, hesitates, "
            "and feels nervous in the great house. Exact age is not stated."
        ),
        "pdf_pages": [8, 12, 20, 23],
    },
    "father": {
        "evidence": "Old father with a gasping cough; complains, grumbles, and bawls through the door.",
        "pdf_pages": [8, 11, 12],
    },
    "uncle": {
        "evidence": "Wang Lung's uncle, described as jovial and sly, fond of jokes; earlier speech is recalled.",
        "pdf_pages": [10, 29, 30],
    },
    "vendor": {
        "evidence": "Plural unnamed peach vendors cry out; no individual speaker or age is identified.",
        "pdf_pages": [16],
    },
    "barber": {
        "evidence": "Professional barber, a joker; grimaces, laughs, and bargains about the extra charge.",
        "pdf_pages": [17, 18],
    },
    "waiter": {
        "evidence": "Waiting boy, later called a small boy; impatient, impudent, and demanding sharply.",
        "pdf_pages": [18, 19],
    },
    "beggar": {
        "evidence": "Passing beggar whines for cash and says he is starving; exact age is not stated.",
        "pdf_pages": [19],
    },
    "gatekeeper": {
        "evidence": "Tall gateman with a mole; shouts roughly, laughs loudly, roars, and demands money.",
        "pdf_pages": [20, 21],
    },
    "old_mistress": {
        "evidence": "Very old lady; thin wrinkled face; speaks gravely and impatiently, with sudden anger.",
        "pdf_pages": [22, 23, 24, 25],
    },
    "o_lan": {
        "age": 20,
        "evidence": (
            "Old Mistress explicitly says she is twenty. Narration describes her voice as "
            "not loud, not soft, plain, and not ill-tempered; she answers slowly and submissively."
        ),
        "pdf_pages": [24, 25, 29],
    },
}
CASTING = {
    "designation": "creative",
    "description_use": "Voice design only; spoken chapter segments contain no casting instructions.",
    "reference_text_use": "Neutral original 18-word design sample, targeting about eight seconds; never chapter content.",
    "choices": {
        "narrator": "Mature woman with literary American English; also voices the unattributed general saying.",
        "wang_lung": "Adult light baritone, earnest dignity with situational nervousness; includes quoted thoughts and imagined speech.",
        "father": "Elderly weathered male voice with clear grumbling; includes recalled speech.",
        "uncle": "Middle-aged jovial, sly male voice; includes recalled speech.",
        "vendor": "One neutral adult male street-call voice represents the collective unnamed peach vendors.",
        "barber": "Adult male middle register, amused and nimble rather than caricatured.",
        "waiter": "Adolescent light tenor; the text identifies a boy but gives no exact age.",
        "beggar": "Adult thin weary male voice, restrained pleading.",
        "gatekeeper": "Adult rough booming baritone, self-important delivery.",
        "old_mistress": "Elderly thin cultured female voice, imperious and deliberate.",
        "o_lan": "Restrained grounded middle-low female register; plain, neither loud nor soft, not ill-tempered.",
    },
}
UNCERTAINTIES = [
    {
        "pdf_pages": [15],
        "text": "It is better to live alone than to marry a woman who has been slave in a great house.",
        "decision": "Unidentified general saying stays with the narrator, not an invented townsfolk character.",
    },
    {
        "pdf_pages": [18],
        "text": "terror, How / before, How",
        "decision": "Retained commas as supplied; higher-resolution OCR did not resolve their unusual placement.",
    },
    {
        "pdf_pages": [20],
        "text": "sell some thing",
        "decision": "Retained the two words; re-OCR also reads them separately and the intended spacing is uncertain.",
    },
    {
        "pdf_pages": [25],
        "text": "So far as I knew she is virgin.",
        "decision": "Retained knew, confirmed by higher-resolution OCR; no grammatical modernization to know.",
    },
    {
        "pdf_pages": [11, 20, 26],
        "text": "Single versus doubled em dashes at interrupted speech.",
        "decision": "Preserved the supplied OCR dash counts; higher-resolution OCR disagrees on some dash lengths.",
    },
]
QUOTE_TURNS_PATH = Path(__file__).resolve().parent / "quote-turns.json"


class Segment(TypedDict):
    speaker: str
    text: str
    paragraph: int


class Correction(TypedDict):
    pdf_page: int
    before: str
    after: str
    occurrences: int


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def normalize_space(text: str) -> str:
    return " ".join(text.split())


def remove_dialogue_quotes(text: str) -> str:
    return text.replace("“", "").replace("”", "")


def prepare_paragraphs(ocr_dir: Path) -> tuple[list[str], list[Correction]]:
    require(ocr_dir.is_absolute(), "--ocr-dir must be an absolute path")
    paragraphs: list[str] = []
    corrections: list[Correction] = []
    for page in range(FIRST_PAGE, LAST_PAGE + 1):
        path = ocr_dir / f"page-{page:03d}.txt"
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        require(bool(lines), f"Empty OCR page: {path}")
        require(
            lines[-1].strip() == FOLIOS[page], f"Unexpected folio on PDF page {page}"
        )
        text = "\n".join(lines[:-1]).strip()
        if page == FIRST_PAGE:
            require(text.startswith("CHAPTER ONE\n"), "Missing Chapter One heading")
            text = text.removeprefix("CHAPTER ONE\n").strip()
        if page == LAST_PAGE:
            require(
                text.count("CHAPTER TWO") == 1,
                "Missing or repeated Chapter Two boundary",
            )
            text = text.split("CHAPTER TWO", 1)[0].strip()
        require("CHAPTER" not in text, f"Unexpected chapter heading on PDF page {page}")
        for before, after in CORRECTIONS.get(page, ()):
            occurrences = text.count(before)
            require(
                occurrences > 0,
                f"Missing reviewed correction on PDF page {page}: {before!r}",
            )
            text = text.replace(before, after)
            corrections.append(
                {
                    "pdf_page": page,
                    "before": before,
                    "after": after,
                    "occurrences": occurrences,
                }
            )

        def repair_wrap(match: re.Match[str], current_page: int = page) -> str:
            left, right = match.groups()
            after = (
                f"{left}-{right}"
                if (left, right) == ("moth", "browed")
                else left + right
            )
            corrections.append(
                {
                    "pdf_page": current_page,
                    "before": match.group(),
                    "after": after,
                    "occurrences": 1,
                }
            )
            return after

        text = re.sub(r"([A-Za-z]+)-\n([a-z]+)", repair_wrap, text)
        for match in re.finditer(r"[^\n]*—\n[^\n]*|[^\n]*\n—[^\n]*", text):
            before = match.group()
            after = re.sub(r"(?<=—)\n|\n(?=—)", "", before)
            corrections.append(
                {
                    "pdf_page": page,
                    "before": before,
                    "after": after,
                    "occurrences": 1,
                }
            )
        text = re.sub(r"(?<=—)\n(?=\S)|(?<=\S)\n(?=—)", "", text)
        blocks = [
            normalize_space(block)
            for block in re.split(r"\n\s*\n", text)
            if block.strip()
        ]
        require(bool(blocks), f"No chapter text on PDF page {page}")
        if page in JOIN_PREVIOUS:
            require(bool(paragraphs), f"Missing paragraph before PDF page {page}")
            paragraphs[-1] += " " + blocks.pop(0)
        paragraphs.extend(blocks)
    require(
        paragraphs[0].startswith("It was Wang Lung’s marriage day."),
        "Unexpected chapter opening",
    )
    require(
        paragraphs[-1].endswith(
            "He gave a hoarse laugh into the darkness and seized her."
        ),
        "Unexpected chapter ending",
    )
    return paragraphs, corrections


def load_quote_turns(path: Path = QUOTE_TURNS_PATH) -> list[tuple[str, str]]:
    turns = json.loads(path.read_text(encoding="utf-8"))
    require(
        isinstance(turns, list)
        and all(
            isinstance(turn, list)
            and len(turn) == 2
            and all(isinstance(value, str) for value in turn)
            for turn in turns
        ),
        f"Quote turns must be [speaker, text] pairs: {path}",
    )
    return [(speaker, text) for speaker, text in turns]


def split_segments(
    paragraphs: list[str], quote_turns: list[tuple[str, str]]
) -> list[Segment]:
    chapter = "\n\n".join(paragraphs)
    matches = list(QUOTE_PATTERN.finditer(chapter))
    require(
        len(quote_turns) == EXPECTED_QUOTE_COUNT, "Internal speaker map count changed"
    )
    require(
        len(matches)
        == chapter.count("“")
        == chapter.count("”")
        == EXPECTED_QUOTE_COUNT,
        f"Unexpected dialogue quote count: {len(matches)}; expected {EXPECTED_QUOTE_COUNT}",
    )
    for index, (match, (speaker, expected_text)) in enumerate(
        zip(matches, quote_turns, strict=True), 1
    ):
        require(speaker in VOICES, f"Unknown speaker in quote {index}: {speaker}")
        require(
            match.group(1) == expected_text,
            f"Reviewed quote {index} ({speaker}) changed: {match.group(1)!r}; expected {expected_text!r}",
        )
    segments: list[Segment] = []
    turn_index = 0
    for paragraph_number, paragraph in enumerate(paragraphs, 1):
        cursor = 0
        for match in QUOTE_PATTERN.finditer(paragraph):
            narration = paragraph[cursor : match.start()].strip()
            if narration:
                segments.append(
                    {
                        "speaker": "narrator",
                        "text": narration,
                        "paragraph": paragraph_number,
                    }
                )
            speaker, _ = quote_turns[turn_index]
            segments.append(
                {
                    "speaker": speaker,
                    "text": match.group(1),
                    "paragraph": paragraph_number,
                }
            )
            turn_index += 1
            cursor = match.end()
        narration = paragraph[cursor:].strip()
        if narration:
            segments.append(
                {
                    "speaker": "narrator",
                    "text": narration,
                    "paragraph": paragraph_number,
                }
            )
    require(
        turn_index == EXPECTED_QUOTE_COUNT,
        "A quote crosses an unreviewed paragraph boundary",
    )
    verify_segments(paragraphs, segments)
    return segments


def verify_segments(paragraphs: list[str], segments: list[Segment]) -> None:
    require(bool(segments), "No spoken segments")
    require(
        {segment["speaker"] for segment in segments} == set(VOICES),
        "Unexpected speaking cast",
    )
    require(
        [segment["paragraph"] for segment in segments]
        == sorted(segment["paragraph"] for segment in segments),
        "Segment paragraph order changed",
    )
    for segment in segments:
        require(bool(segment["text"].strip()), "Empty spoken segment")
        require(
            1 <= segment["paragraph"] <= len(paragraphs), "Invalid paragraph number"
        )
        require(
            "“" not in segment["text"] and "”" not in segment["text"],
            "Dialogue delimiters in spoken text",
        )
    for number, paragraph in enumerate(paragraphs, 1):
        reconstructed = normalize_space(
            " ".join(
                segment["text"]
                for segment in segments
                if segment["paragraph"] == number
            )
        )
        expected = normalize_space(remove_dialogue_quotes(paragraph))
        require(
            reconstructed == expected,
            f"Paragraph {number} lost or gained content during segmentation",
        )
    reconstructed = normalize_space(" ".join(segment["text"] for segment in segments))
    expected = normalize_space(remove_dialogue_quotes("\n\n".join(paragraphs)))
    require(
        reconstructed == expected,
        "Chapter reconstruction differs from normalized source",
    )
    for speaker, voice in VOICES.items():
        require(
            len(voice["description"].split()) < 20,
            f"Voice description too long: {speaker}",
        )
        require(
            15 <= len(voice["reference_text"].split()) <= 20,
            f"Reference sample length invalid: {speaker}",
        )


def build_manifest(
    paragraphs: list[str], corrections: list[Correction], segments: list[Segment]
) -> dict[str, object]:
    return {
        "title": "The Good Earth",
        "chapter": 1,
        "source": {
            "pdf": "the-good-earth.pdf",
            "pdf_pages": [8, 31],
            "printed_pages": [3, 26],
            "extraction": (
                "tesseract: supplied page-008.txt through page-031.txt; "
                "selected passages rechecked with Tesseract 5.5.0 --psm 6 on 300 dpi PDF rasterizations"
            ),
            "ocr_review": (
                "All 24 OCR pages read in order; Chapter One heading and folios excluded; "
                "stopped before Chapter Two. Clear OCR typos and confirmed punctuation repaired, "
                "line wraps resolved, 15 page-continuing paragraphs joined, and one omitted "
                "paragraph break restored. All 117 quoted spans independently assigned by textual "
                "context and checked against exact reviewed text. Every paragraph and the complete "
                "chapter reconstruct exactly after removing dialogue delimiters and normalizing "
                "whitespace. Page images could not be viewed directly; uncertain wording and "
                "dash lengths remain unchanged. No original sentences rewritten or omitted."
            ),
            "factual_cast": FACTUAL_CAST,
            "corrections": corrections,
            "paragraph_continuations_at_pdf_pages": sorted(JOIN_PREVIOUS),
            "uncertainties": UNCERTAINTIES,
        },
        "settings": {"cfg": 2.0, "steps": 12},
        "voices": VOICES,
        "casting": CASTING,
        "segments": segments,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare faithful Chapter One text and a reviewed speaking manifest."
    )
    parser.add_argument("--ocr-dir", type=Path, default=DEFAULT_OCR_DIR)
    parser.add_argument("--quote-turns", type=Path, default=QUOTE_TURNS_PATH)
    args = parser.parse_args()
    try:
        paragraphs, corrections = prepare_paragraphs(args.ocr_dir)
        quote_turns = load_quote_turns(args.quote_turns)
        segments = split_segments(paragraphs, quote_turns)
        manifest = build_manifest(paragraphs, corrections, segments)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Source preparation refused: {error}\n")
    chapter = "\n\n".join(paragraphs) + "\n"
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    output_dir = Path(__file__).resolve().parent
    (output_dir / "chapter.txt").write_text(chapter, encoding="utf-8")
    (output_dir / "chapter.json").write_text(manifest_text, encoding="utf-8")
    counts = Counter(segment["speaker"] for segment in segments)
    quote_counts = Counter(speaker for speaker, _ in quote_turns)
    print(
        f"Verified: {len(WORD_PATTERN.findall(chapter))} words, {len(chapter)} characters "
        f"(including final newline), {len(paragraphs)} paragraphs, {len(segments)} segments, "
        f"{EXPECTED_QUOTE_COUNT} quoted turns."
    )
    print(
        "Reconstruction: exact words, punctuation, and order; dialogue delimiters removed, whitespace normalized."
    )
    for speaker in VOICES:
        print(
            f"{speaker}: {counts[speaker]} segments, {quote_counts[speaker]} quoted turns"
        )
    print(
        f"Corrections: {sum(item['occurrences'] for item in corrections)} OCR/wrap repairs; {len(JOIN_PREVIOUS)} page continuations."
    )
    print(
        f"Uncertainties: {len(UNCERTAINTIES)} retained decisions recorded in chapter.json."
    )


if __name__ == "__main__":
    main()
