from __future__ import annotations

import argparse
import base64
import binascii
import fcntl
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import time
import uuid
import wave
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_OUTPUT = Path("output/voxcpm2-chapter-01")
ENGINE_URL = "http://voxxy-engine-voxcpm:8000/v1/synthesize"
SYNTH_COMMAND = [
    "docker", "exec", "-i", "vox", "curl", "-fsS", "--connect-timeout", "5",
    "--max-time", "240", "-H", "content-type:application/json",
    "--data-binary", "@-", ENGINE_URL,
]
TOKENIZER_CODE = r'''
import importlib.metadata
import json
import os
import re
import runpy
import sys
from pathlib import Path
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
from transformers import LlamaTokenizerFast
root = Path("/cache/huggingface/hub/models--openbmb--VoxCPM2")
revision = (root / "refs/main").read_text().strip()
if not re.fullmatch(r"[0-9a-f]{40}", revision):
    raise ValueError("Invalid VoxCPM2 model revision")
snapshot = root / "snapshots" / revision
utils = importlib.metadata.distribution("voxcpm").locate_file("voxcpm/model/utils.py")
mask = runpy.run_path(str(utils))["mask_multichar_chinese_tokens"]
tokenizer = mask(LlamaTokenizerFast.from_pretrained(str(snapshot), local_files_only=True))
texts = json.load(sys.stdin)
counts = []
for text in texts:
    ids = tokenizer(re.sub(r"\s+", " ", text.replace("\n", " ")))
    if not isinstance(ids, list) or any(type(i) is not int for i in ids):
        raise TypeError("VoxCPM2 tokenizer must return a list of integer IDs")
    counts.append(len(ids))
print(json.dumps({"model_revision": revision, "input_text_tokens": counts}))
'''
TOKENIZER_COMMAND = [
    "docker", "exec", "-i", "voxxy-engine-voxcpm", "/opt/venv/bin/python",
    "-B", "-c", TOKENIZER_CODE,
]
TOKEN_LABEL = "Exact VoxCPM2 input text tokens; not billable, audio, or LLM tokens"


class ChapterError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode())


def atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\n", " ")).strip()


def nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not normalized(value):
        raise ChapterError(f"{field} must be a nonempty string")
    return normalized(value)


def positive_int(value: Any, field: str) -> int:
    if type(value) is not int or value < 1:
        raise ChapterError(f"{field} must be a positive integer")
    return value


def validate_manifest(data: Any, directory: Path) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ChapterError("Manifest must be an object")
    result = dict(data)
    result["title"] = nonempty(data.get("title"), "title")
    chapter = data.get("chapter")
    if type(chapter) is int:
        positive_int(chapter, "chapter")
    else:
        nonempty(chapter, "chapter")
    source = data.get("source")
    if not isinstance(source, dict):
        raise ChapterError("source must be an object")
    pdf = nonempty(source.get("pdf"), "source.pdf")
    if not (directory / pdf).is_file():
        raise ChapterError(f"source.pdf does not exist: {pdf}")
    pages = source.get("pdf_pages")
    if not isinstance(pages, list) or not pages:
        raise ChapterError("source.pdf_pages must be a nonempty list of 1-based page numbers")
    for page in pages:
        positive_int(page, "source.pdf_pages entry")
    if pages != sorted(set(pages)):
        raise ChapterError("source.pdf_pages must be unique and ascending")
    settings = data.get("settings", {})
    if not isinstance(settings, dict) or set(settings) - {"cfg", "steps"}:
        raise ChapterError("settings supports only cfg and steps")
    cfg = settings.get("cfg", 2.0)
    steps = settings.get("steps", 12)
    if type(cfg) not in (int, float) or not math.isfinite(cfg) or not 1 <= cfg <= 5:
        raise ChapterError("settings.cfg must be finite and between 1 and 5")
    if type(steps) is not int or not 1 <= steps <= 50:
        raise ChapterError("settings.steps must be an integer between 1 and 50")
    result["settings"] = {"cfg": float(cfg), "steps": steps}
    voices = data.get("voices")
    if not isinstance(voices, dict) or not voices:
        raise ChapterError("voices must be a nonempty object")
    result["voices"] = {}
    for speaker, voice in voices.items():
        if not isinstance(speaker, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", speaker):
            raise ChapterError("Voice IDs must be safe filenames: letters, digits, underscore, hyphen")
        if not isinstance(voice, dict):
            raise ChapterError(f"voices.{speaker} must be an object")
        description = nonempty(voice.get("description"), f"voices.{speaker}.description")
        reference_text = nonempty(voice.get("reference_text"), f"voices.{speaker}.reference_text")
        if "(" in description or ")" in description:
            raise ChapterError("Voice descriptions must not contain parentheses")
        result["voices"][speaker] = {
            **voice, "description": description, "reference_text": reference_text,
        }
    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ChapterError("segments must be a nonempty list")
    result["segments"] = []
    previous_paragraph = -1
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise ChapterError(f"segments[{index}] must be an object")
        speaker = segment.get("speaker")
        if not isinstance(speaker, str) or speaker not in voices:
            raise ChapterError(f"segments[{index}] has an unknown speaker")
        paragraph = segment.get("paragraph")
        if type(paragraph) is not int or paragraph < 0 or paragraph < previous_paragraph:
            raise ChapterError("paragraph must be a nonnegative, nondecreasing integer")
        previous_paragraph = paragraph
        result["segments"].append({
            **segment, "text": nonempty(segment.get("text"), f"segments[{index}].text"),
        })
    return result


def chunk_text(text: str, target: int = 450, sentence_max: int = 650) -> list[str]:
    if not 1 <= target <= sentence_max:
        raise ChapterError("Invalid chunk limits")
    text = normalized(text)
    if not text:
        raise ChapterError("Cannot chunk empty text")
    if any(len(word) > sentence_max for word in text.split()):
        raise ChapterError("A word exceeds the 650-character hard limit")
    sentences = re.split(r'(?<=[.!?。！？])\s+|(?<=[.!?]["\u201d\u2019])\s+', text)
    units: list[str] = []
    for sentence in sentences:
        remaining = sentence
        limit = target if len(sentence) > sentence_max else sentence_max
        while len(remaining) > limit:
            breaks = [match.start() for match in re.finditer(r"\s+", remaining) if match.start() <= target]
            if not breaks:
                breaks = [match.start() for match in re.finditer(r"\s+", remaining) if match.start() <= sentence_max]
            if not breaks:
                if len(remaining) <= sentence_max:
                    break
                raise ChapterError("Cannot split text without breaking a word")
            punctuation = [pos for pos in breaks if remaining[pos - 1] in ",;:—–"]
            end = punctuation[-1] if punctuation else breaks[-1]
            units.append(remaining[:end])
            remaining = remaining[end:].lstrip()
        units.append(remaining)
    chunks: list[str] = []
    current = ""
    for unit in units:
        joined = f"{current} {unit}" if current else unit
        if current and len(joined) > target:
            chunks.append(current)
            current = unit
        else:
            current = joined
    if current:
        chunks.append(current)
    if " ".join(chunks) != text or any(len(chunk) > sentence_max for chunk in chunks):
        raise ChapterError("Chunk coverage invariant failed")
    return chunks


def tokenize_texts(texts: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        TOKENIZER_COMMAND, input=canonical(texts), capture_output=True, check=True, timeout=120,
    )
    result = json.loads(completed.stdout)
    counts = result.get("input_text_tokens")
    if not re.fullmatch(r"[0-9a-f]{40}", str(result.get("model_revision", ""))):
        raise ChapterError("Tokenizer returned an invalid model revision")
    if not isinstance(counts, list) or len(counts) != len(texts) or any(
        type(count) is not int or count < 1 for count in counts
    ):
        raise ChapterError("Tokenizer returned invalid exact input text token counts")
    return result


def build_plan(manifest_path: Path) -> dict[str, Any]:
    manifest = validate_manifest(read_json(manifest_path), manifest_path.parent)
    designs: list[dict[str, Any]] = []
    for speaker, voice in sorted(manifest["voices"].items()):
        designs.append({
            "id": speaker, "speaker": speaker,
            "text": f"({voice['description']}){voice['reference_text']}",
        })
    chunks: list[dict[str, Any]] = []
    for segment_index, segment in enumerate(manifest["segments"]):
        for part, text in enumerate(chunk_text(segment["text"])):
            chunks.append({
                "id": f"{len(chunks) + 1:06d}", "speaker": segment["speaker"],
                "text": text, "paragraph": segment["paragraph"],
                "segment": segment_index, "part": part,
            })
    tokenization = tokenize_texts([item["text"] for item in designs + chunks])
    for item, count in zip(designs + chunks, tokenization["input_text_tokens"], strict=True):
        item["input_text_tokens"] = count
        item["characters"] = len(item["text"])
    plan = {
        "version": 1, "manifest": manifest, "manifest_sha256": digest(manifest),
        "model_revision": tokenization["model_revision"], "token_label": TOKEN_LABEL,
        "designs": designs, "chunks": chunks,
        "planned_input_text_tokens": {
            "design": sum(item["input_text_tokens"] for item in designs),
            "chapter": sum(item["input_text_tokens"] for item in chunks),
        },
    }
    plan["plan_sha256"] = digest(plan)
    return plan


def prepare(manifest_path: Path, root: Path) -> dict[str, Any]:
    plan = build_plan(manifest_path)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "prepared.json"
    if path.exists() and read_json(path) != plan:
        raise ChapterError("Prepared manifest/model changed; use a fresh output directory")
    write_json(path, plan)
    return plan


def load_plan(manifest_path: Path, root: Path) -> dict[str, Any]:
    path = root / "prepared.json"
    if not path.is_file():
        raise ChapterError("Run prepare first")
    stored = read_json(path)
    if stored != build_plan(manifest_path):
        raise ChapterError("Prepared manifest, tokens, or model revision changed; use a fresh output directory")
    return stored


def wav_metadata(source: bytes | Path) -> dict[str, Any]:
    if isinstance(source, bytes):
        stream: Any = io.BytesIO(source)
        size = len(source)
        sha256 = hashlib.sha256(source).hexdigest()
    else:
        stream = source.open("rb")
        size = source.stat().st_size
        sha256 = file_hash(source)
    try:
        header = stream.read(12)
        if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
            raise ChapterError("Audio is not a RIFF WAV")
        if int.from_bytes(header[4:8], "little") + 8 != size:
            raise ChapterError("WAV RIFF size does not match its bytes")
        stream.seek(0)
        with wave.open(stream, "rb") as reader:
            rate, channels, width, frames = (
                reader.getframerate(), reader.getnchannels(),
                reader.getsampwidth(), reader.getnframes(),
            )
            if reader.getcomptype() != "NONE" or width not in (1, 2, 3, 4):
                raise ChapterError("WAV must contain uncompressed integer PCM")
            if rate <= 0 or channels < 1 or frames < 1:
                raise ChapterError("WAV sample metadata is invalid or empty")
            frame_bytes = channels * width
            seen = 0
            while block := reader.readframes(8192):
                if len(block) % frame_bytes:
                    raise ChapterError("WAV has an incomplete PCM frame")
                seen += len(block) // frame_bytes
            if seen != frames:
                raise ChapterError("WAV PCM data is truncated")
        return {
            "duration_seconds": frames / rate, "sample_rate": rate, "channels": channels,
            "sample_width": width, "frames": frames, "bytes": size, "sha256": sha256,
        }
    except (wave.Error, EOFError) as exc:
        raise ChapterError(f"Invalid WAV: {exc}") from exc
    finally:
        stream.close()


def decode_response(response: Any) -> tuple[bytes, dict[str, Any]]:
    if not isinstance(response, dict) or response.get("engine") != "voxcpm":
        raise ChapterError("Synthesis response engine must be voxcpm; fallback is forbidden")
    encoded = response.get("wav_b64")
    if not isinstance(encoded, str):
        raise ChapterError("Synthesis response has no WAV base64 string")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ChapterError("Invalid WAV base64") from exc
    metadata = wav_metadata(data)
    for field, expected in (("bytes", metadata["bytes"]), ("sample_rate", metadata["sample_rate"])):
        if type(response.get(field)) is not int or response[field] != expected:
            raise ChapterError(f"Synthesis {field} disagrees with the WAV")
    duration = response.get("duration_s")
    if duration is not None and (
        type(duration) not in (int, float) or not math.isfinite(duration)
        or abs(duration - metadata["duration_seconds"]) > 1 / metadata["sample_rate"]
    ):
        raise ChapterError("Synthesis duration disagrees with the WAV")
    return data, metadata


def request_spec(
    plan: dict[str, Any], item: dict[str, Any], phase: str, reference_sha256: str | None,
) -> dict[str, Any]:
    return {
        "version": 1, "engine": "voxcpm", "endpoint": ENGINE_URL,
        "phase": phase, "item": item, "reference_sha256": reference_sha256,
        "settings": plan["manifest"]["settings"], "model_revision": plan["model_revision"],
        "conditioning": "reference_audio_only" if reference_sha256 else "voice_design",
    }


def append_request(root: Path, record: dict[str, Any]) -> None:
    with (root / "requests.jsonl").open("ab") as handle:
        handle.write(canonical(record) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def request_records(root: Path) -> list[dict[str, Any]]:
    path = root / "requests.jsonl"
    if not path.exists():
        return []
    records: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                record = json.loads(line)
                request_id = record["request_id"]
                status = record["status"]
                if status not in ("started", "success", "failure"):
                    raise ChapterError("Unknown request status")
                previous = records.get(request_id)
                if previous is not None:
                    if previous["status"] != "started" or status == "started":
                        raise ChapterError("Duplicate request lifecycle event")
                    for field in ("request_fingerprint", "phase", "speaker", "input_text_tokens"):
                        if previous[field] != record[field]:
                            raise ChapterError("Request lifecycle identity changed")
                elif status != "started":
                    raise ChapterError("Request completion has no start record")
                records[request_id] = record
            except (KeyError, TypeError, json.JSONDecodeError, ChapterError) as exc:
                raise ChapterError(f"Invalid requests.jsonl line {line_number}: {exc}") from exc
    return list(records.values())


def cache_paths(root: Path, phase: str, item_id: str) -> tuple[Path, Path]:
    directory = root / ("refs" if phase == "design" else "chunks")
    return directory / f"{item_id}.wav", directory / f"{item_id}.json"


def check_cache(root: Path, spec: dict[str, Any]) -> dict[str, Any] | None:
    wav_path, metadata_path = cache_paths(root, spec["phase"], spec["item"]["id"])
    if not wav_path.exists() and not metadata_path.exists():
        return None
    if not wav_path.is_file() or not metadata_path.is_file():
        raise ChapterError(f"Incomplete/unknown cache: {wav_path}")
    metadata = read_json(metadata_path)
    if metadata.get("request_fingerprint") != digest(spec) or metadata.get("request") != spec:
        raise ChapterError(f"Stale request fingerprint/reference/settings: {wav_path}")
    actual = wav_metadata(wav_path)
    if metadata.get("wav") != actual:
        raise ChapterError(f"Corrupted cached audio or metadata: {wav_path}")
    records = request_records(root)
    receipt = next((row for row in records if row["request_id"] == metadata.get("request_id")), None)
    if not receipt or receipt["status"] != "success" or receipt["wav"] != actual or (
        receipt["request_fingerprint"] != digest(spec)
    ):
        raise ChapterError(f"Cache has no matching successful request receipt: {wav_path}")
    return metadata


def check_inventory(root: Path, phase: str, items: list[dict[str, Any]]) -> None:
    directory = root / ("refs" if phase == "design" else "chunks")
    expected = {f"{item['id']}{suffix}" for item in items for suffix in (".wav", ".json")}
    if directory.exists():
        unknown = [path.name for path in directory.iterdir() if path.name not in expected]
        if unknown:
            raise ChapterError(f"Unknown cache files in {directory}: {', '.join(sorted(unknown))}")


def check_previous_attempts(root: Path, spec: dict[str, Any], retry_failed: bool) -> None:
    for record in request_records(root):
        if record["phase"] != spec["phase"] or record["item_id"] != spec["item"]["id"]:
            continue
        if record["request_fingerprint"] != digest(spec):
            raise ChapterError("Historical request is stale; use a fresh output directory")
        if record["status"] == "success":
            raise ChapterError("Successful request has lost its frozen cache; refusing to regenerate")
        if not retry_failed:
            raise ChapterError(
                "Prior failed/unfinished request; confirm the engine is idle, then explicitly use --retry-failed. "
                "A timeout does not prove cancellation."
            )


def generate_request(
    root: Path, spec: dict[str, Any], reference: bytes | None = None, retry_failed: bool = False,
) -> dict[str, Any]:
    if (hashlib.sha256(reference).hexdigest() if reference is not None else None) != spec["reference_sha256"]:
        raise ChapterError("Reference bytes do not match the frozen reference hash")
    cached = check_cache(root, spec)
    if cached is not None:
        return cached
    check_previous_attempts(root, spec, retry_failed)
    wav_path, metadata_path = cache_paths(root, spec["phase"], spec["item"]["id"])
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"text": spec["item"]["text"], **spec["settings"]}
    if reference is not None:
        payload["reference_audio_b64"] = base64.b64encode(reference).decode("ascii")
    record: dict[str, Any] = {
        "request_id": uuid.uuid4().hex, "timestamp_utc": utc_now(), "phase": spec["phase"],
        "speaker": spec["item"]["speaker"], "item_id": spec["item"]["id"],
        "request_fingerprint": digest(spec), "reference_sha256": spec["reference_sha256"],
        "settings": spec["settings"], "model_revision": spec["model_revision"],
        "input_text_tokens": spec["item"]["input_text_tokens"], "token_label": TOKEN_LABEL,
        "engine": "voxcpm", "status": "started", "success": None,
        "wall_seconds": None, "wav": None, "error": None,
    }
    append_request(root, record)
    start = time.perf_counter()
    try:
        completed = subprocess.run(
            SYNTH_COMMAND, input=canonical(payload), capture_output=True, check=True, timeout=250,
        )
        audio, wav = decode_response(json.loads(completed.stdout))
        record["wav"] = wav
        record["wall_seconds"] = time.perf_counter() - start
        metadata = {
            "request_id": record["request_id"], "request_fingerprint": digest(spec),
            "request": spec, "wav": wav,
        }
        atomic_write(wav_path, audio)
        write_json(metadata_path, metadata)
    except BaseException as exc:
        record.update({
            "status": "failure", "success": False, "wall_seconds": time.perf_counter() - start,
            "completed_utc": utc_now(), "error": f"{type(exc).__name__}: {exc}",
        })
        append_request(root, record)
        raise
    record.update({"status": "success", "success": True, "completed_utc": utc_now()})
    append_request(root, record)
    return metadata


def voices(plan: dict[str, Any], root: Path, retry_failed: bool = False) -> dict[str, Any]:
    check_inventory(root, "design", plan["designs"])
    specs = [request_spec(plan, item, "design", None) for item in plan["designs"]]
    for spec in specs:
        if check_cache(root, spec) is None:
            check_previous_attempts(root, spec, retry_failed)
    return {
        spec["item"]["id"]: generate_request(root, spec, retry_failed=retry_failed)
        for spec in specs
    }


def pause_frames(previous: dict[str, Any], current: dict[str, Any], rate: int) -> int:
    if previous["paragraph"] != current["paragraph"]:
        return round(rate * 0.280)
    if previous["segment"] != current["segment"]:
        return round(rate * 0.120)
    return 0


def fade_pcm(block: bytes, offset: int, total: int, width: int, channels: int, fade: int) -> bytes:
    if fade <= 1 or (offset >= fade and offset + len(block) // (width * channels) <= total - fade):
        return block
    result = bytearray(block)
    frame_size = width * channels
    for local in range(len(block) // frame_size):
        frame = offset + local
        gain = min(1.0, frame / (fade - 1), (total - 1 - frame) / (fade - 1))
        if gain == 1:
            continue
        for channel in range(channels):
            position = local * frame_size + channel * width
            sample = int.from_bytes(block[position:position + width], "little", signed=width != 1)
            if width == 1:
                sample -= 128
            sample = round(sample * gain) + (128 if width == 1 else 0)
            result[position:position + width] = sample.to_bytes(width, "little", signed=width != 1)
    return bytes(result)


def assemble_wav(chunks: list[tuple[dict[str, Any], Path]], destination: Path) -> dict[str, Any]:
    if not chunks:
        raise ChapterError("No chapter chunks to assemble")
    metadata = [wav_metadata(path) for _, path in chunks]
    first = metadata[0]
    format_fields = ("sample_rate", "channels", "sample_width")
    if any(any(item[field] != first[field] for field in format_fields) for item in metadata):
        raise ChapterError("Chunk WAV rates/channels/sample widths do not match")
    rate, channels, width = (first[field] for field in format_fields)
    temporary = destination.with_name(destination.stem + ".part.wav")
    pauses = 0
    with wave.open(str(temporary), "wb") as writer:
        writer.setparams((channels, width, rate, 0, "NONE", "not compressed"))
        for index, ((item, path), info) in enumerate(zip(chunks, metadata, strict=True)):
            if index:
                silence_frames = pause_frames(chunks[index - 1][0], item, rate)
                silence = (b"\x80" if width == 1 else b"\x00" * width) * channels
                writer.writeframesraw(silence * silence_frames)
                pauses += silence_frames
            fade = min(round(rate * 0.008), info["frames"] // 2)
            with wave.open(str(path), "rb") as reader:
                offset = 0
                while block := reader.readframes(8192):
                    writer.writeframesraw(fade_pcm(block, offset, info["frames"], width, channels, fade))
                    offset += len(block) // (width * channels)
    temporary.replace(destination)
    result = wav_metadata(destination)
    expected = sum(item["frames"] for item in metadata) + pauses
    if result["frames"] != expected:
        raise ChapterError("Assembly frame count does not match audio plus pauses")
    return {"wav": result, "pause_frames": pauses, "pause_seconds": pauses / rate, "fade_ms": 8}


def master_audio(raw: Path, mastered: Path, mp3: Path, rate: int) -> None:
    base = ["ffmpeg", "-nostdin", "-hide_banner", "-y"]
    measure = subprocess.run(
        base + ["-i", str(raw.resolve()), "-af", "loudnorm=I=-20:TP=-3:LRA=7:print_format=json",
                "-f", "null", "-"],
        capture_output=True, check=True, timeout=600,
    )
    stderr = measure.stderr.decode("utf-8", errors="replace")
    match = re.search(r'\{\s*"input_i".*?\}', stderr, re.DOTALL)
    if match is None:
        raise ChapterError("ffmpeg loudnorm did not return whole-chapter measurements")
    measured = json.loads(match.group())
    mapping = {
        "measured_I": "input_i", "measured_TP": "input_tp", "measured_LRA": "input_lra",
        "measured_thresh": "input_thresh", "offset": "target_offset",
    }
    values = {}
    for key, field in mapping.items():
        value = float(measured[field])
        if not math.isfinite(value):
            raise ChapterError("Nonfinite loudness measurement (possibly silent audio)")
        values[key] = value
    filter_text = "loudnorm=I=-20:TP=-3:LRA=7:linear=true:" + ":".join(
        f"{key}={value}" for key, value in values.items()
    )
    temporary_wav = mastered.with_name(mastered.stem + ".part.wav")
    temporary_mp3 = mp3.with_name(mp3.stem + ".part.mp3")
    subprocess.run(
        base + ["-i", str(raw.resolve()), "-af", filter_text, "-ar", str(rate),
                "-c:a", "pcm_s16le", str(temporary_wav.resolve())],
        capture_output=True, check=True, timeout=600,
    )
    wav_metadata(temporary_wav)
    temporary_wav.replace(mastered)
    subprocess.run(
        base + ["-i", str(mastered.resolve()), "-c:a", "libmp3lame", "-b:a", "128k",
                str(temporary_mp3.resolve())],
        capture_output=True, check=True, timeout=600,
    )
    if not temporary_mp3.is_file() or not temporary_mp3.stat().st_size:
        raise ChapterError("ffmpeg produced an empty MP3")
    temporary_mp3.replace(mp3)


def render(plan: dict[str, Any], root: Path, retry_failed: bool = False) -> dict[str, Any]:
    check_inventory(root, "design", plan["designs"])
    check_inventory(root, "chapter", plan["chunks"])
    refs = {}
    for item in plan["designs"]:
        spec = request_spec(plan, item, "design", None)
        metadata = check_cache(root, spec)
        if metadata is None:
            raise ChapterError(f"Missing frozen reference for {item['id']}; run voices first")
        path, _ = cache_paths(root, "design", item["id"])
        refs[item["id"]] = path.read_bytes()
        if hashlib.sha256(refs[item["id"]]).hexdigest() != metadata["wav"]["sha256"]:
            raise ChapterError("Frozen reference changed while loading")
    specs = [
        request_spec(plan, item, "chapter", hashlib.sha256(refs[item["speaker"]]).hexdigest())
        for item in plan["chunks"]
    ]
    for spec in specs:
        if check_cache(root, spec) is None:
            check_previous_attempts(root, spec, retry_failed)
    chunks = []
    hashes = []
    for spec in specs:
        metadata = generate_request(root, spec, refs[spec["item"]["speaker"]], retry_failed)
        path, _ = cache_paths(root, "chapter", spec["item"]["id"])
        chunks.append((spec["item"], path))
        hashes.append({"request_fingerprint": digest(spec), "wav_sha256": metadata["wav"]["sha256"]})
    assembly_spec = {
        "chunks": hashes, "plan_sha256": plan["plan_sha256"], "turn_pause_ms": 120,
        "paragraph_pause_ms": 280, "fade_ms": 8,
        "mastering": {"I": -20, "TP": -3, "LRA": 7, "mp3_kbps": 128, "two_pass": True},
    }
    assembly_path = root / "assembly.json"
    raw, mastered, mp3 = (root / name for name in ("chapter.raw.wav", "chapter.wav", "chapter.mp3"))
    if assembly_path.exists():
        saved = read_json(assembly_path)
        if saved["fingerprint"] != digest(assembly_spec):
            raise ChapterError("Stale chapter assembly fingerprint")
        if wav_metadata(raw) != saved["raw"]["wav"] or wav_metadata(mastered) != saved["mastered_wav"]:
            raise ChapterError("Corrupted chapter WAV export")
        if file_hash(mp3) != saved["mp3"]["sha256"] or mp3.stat().st_size != saved["mp3"]["bytes"]:
            raise ChapterError("Corrupted chapter MP3 export")
        return saved
    raw_info = assemble_wav(chunks, raw)
    master_audio(raw, mastered, mp3, raw_info["wav"]["sample_rate"])
    result = {
        "fingerprint": digest(assembly_spec), "spec": assembly_spec, "raw": raw_info,
        "mastered_wav": wav_metadata(mastered),
        "mp3": {"bytes": mp3.stat().st_size, "sha256": file_hash(mp3), "bitrate_kbps": 128},
        "timestamp_utc": utc_now(),
    }
    write_json(assembly_path, result)
    return result


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "requests": len(records),
        "successful_calls": sum(row["status"] == "success" for row in records),
        "failed_calls": sum(row["status"] == "failure" for row in records),
        "unfinished_calls": sum(row["status"] == "started" for row in records),
        "input_text_tokens": sum(row["input_text_tokens"] for row in records),
        "wall_seconds": sum(row["wall_seconds"] or 0 for row in records),
        "successful_audio_seconds": sum(
            row["wav"]["duration_seconds"] for row in records if row["status"] == "success"
        ),
        "wall_time_complete": all(row["wall_seconds"] is not None for row in records),
    }


def llm_metrics(snapshot: Any = None) -> dict[str, Any]:
    if snapshot is None:
        return {
            "input_tokens": None, "output_tokens": None, "total_tokens": None, "cost_usd": None,
            "reason": "No actual LLM usage snapshot supplied; text tokens are not LLM usage and no estimates are made.",
        }
    if isinstance(snapshot, dict) and isinstance(snapshot.get("totals"), dict):
        source = snapshot.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ChapterError("LLM usage snapshot requires an actual usage source")
        totals = snapshot["totals"]
        fields = (
            "input_tokens", "output_tokens", "reasoning_tokens", "cache_read_tokens", "cache_write_tokens",
            "reported_total_tokens", "messages_missing_total_tokens", "messages_missing_usage", "messages_missing_cost",
        )
        for key in fields:
            if type(totals.get(key)) is not int or totals[key] < 0:
                raise ChapterError(f"LLM snapshot totals.{key} must be a nonnegative integer")
        cost = snapshot.get("actual_llm_charge_usd")
        reported_cost = totals.get("opencode_reported_cost_usd")
        for value in (cost, reported_cost):
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ChapterError("LLM snapshot costs must be nonnegative or null")
        complete = totals["messages_missing_usage"] == 0
        return {
            "input_tokens": totals["input_tokens"] if complete else None,
            "output_tokens": totals["output_tokens"] if complete else None,
            "reasoning_tokens": totals["reasoning_tokens"] if complete else None,
            "cache_read_tokens": totals["cache_read_tokens"] if complete else None,
            "cache_write_tokens": totals["cache_write_tokens"] if complete else None,
            "total_tokens": totals["reported_total_tokens"] if complete and totals["messages_missing_total_tokens"] == 0 else None,
            "cost_usd": cost,
            "opencode_reported_cost_usd": reported_cost if totals["messages_missing_cost"] == 0 else None,
            "source": source, "snapshot": snapshot,
            "reason": "Actual supplied cumulative usage snapshot; reported totals, reasoning, and cache are not added or inferred. Missing usage remains null; reported cost is not proof of paid charge.",
        }
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("source"), str) or not snapshot["source"].strip():
        raise ChapterError("LLM usage snapshot requires source and exact input/output/total_tokens")
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        if type(snapshot.get(key)) is not int or snapshot[key] < 0:
            raise ChapterError(f"LLM usage snapshot {key} must be a nonnegative integer")
    if snapshot["total_tokens"] != snapshot["input_tokens"] + snapshot["output_tokens"]:
        raise ChapterError("LLM snapshot token totals disagree")
    cost = snapshot.get("cost_usd")
    if cost is not None and (type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0):
        raise ChapterError("LLM snapshot cost_usd must be nonnegative or null")
    return {**snapshot, "reason": "Actual supplied usage snapshot; not inferred from audiobook text."}


def report(root: Path, snapshot: Any = None) -> dict[str, Any]:
    if (root / "llm-usage.json").is_file() and snapshot is None:
        snapshot = read_json(root / "llm-usage.json")
    llm = llm_metrics(snapshot)
    records = request_records(root)
    result = {
        "timestamp_utc": utc_now(), "token_label": TOKEN_LABEL, "totals": summarize(records),
        "by_phase": {
            phase: summarize([row for row in records if row["phase"] == phase])
            for phase in ("design", "chapter")
        },
        "failed_calls": summarize([row for row in records if row["status"] == "failure"]),
        "by_speaker": {
            speaker: {
                "totals": summarize([row for row in records if row["speaker"] == speaker]),
                **{
                    phase: summarize([row for row in records if row["speaker"] == speaker and row["phase"] == phase])
                    for phase in ("design", "chapter")
                },
            }
            for speaker in sorted({row["speaker"] for row in records})
        },
        "costs": {
            "observed_paid_provider_charge_usd": 0,
            "basis": "Only the self-hosted VoxCPM2 engine endpoint is invoked; no paid provider or fallback.",
            "energy_usd": None, "tools_usd": None, "llm_usd": llm["cost_usd"],
            "exclusions": "Energy, tools, and LLM costs are excluded from the observed synthesis provider charge.",
        },
        "llm_metrics": llm,
        "assembly": read_json(root / "assembly.json") if (root / "assembly.json").is_file() else None,
    }
    if (root / "prepared.json").is_file():
        plan = read_json(root / "prepared.json")
        result["title"] = plan["manifest"]["title"]
        result["chapter"] = plan["manifest"]["chapter"]
        result["planned_input_text_tokens"] = plan["planned_input_text_tokens"]
        result["planned_chunks"] = len(plan["chunks"])
    if snapshot is not None:
        write_json(root / "llm-usage.json", snapshot)
    write_json(root / "metrics.json", result)
    return result


@contextmanager
def output_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ChapterError("Another chapter command owns this output directory") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Standalone, frozen-reference VoxCPM2 chapter renderer")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "voices", "render", "report"):
        command = subparsers.add_parser(name)
        command.add_argument("--manifest", type=Path, default=Path("chapter.json"))
        command.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
        if name in ("voices", "render"):
            command.add_argument(
                "--retry-failed", action="store_true",
                help="Explicitly authorize a new attempt only after confirming the engine is idle",
            )
        if name == "report":
            command.add_argument("--llm-usage-snapshot", type=Path)
    args = parser.parse_args(argv)
    try:
        with output_lock(args.output):
            if args.command == "prepare":
                plan = prepare(args.manifest, args.output)
                result = {
                    "chunks": len(plan["chunks"]), "voices": len(plan["designs"]),
                    "planned_input_text_tokens": plan["planned_input_text_tokens"],
                    "model_revision": plan["model_revision"], "token_label": TOKEN_LABEL,
                }
            elif args.command == "report":
                snapshot = read_json(args.llm_usage_snapshot) if args.llm_usage_snapshot else None
                result = report(args.output, snapshot)
            else:
                plan = load_plan(args.manifest, args.output)
                try:
                    if args.command == "voices":
                        result = voices(plan, args.output, args.retry_failed)
                    else:
                        result = render(plan, args.output, args.retry_failed)
                except BaseException:
                    try:
                        report(args.output)
                    except Exception as report_error:
                        print(f"Metrics update failed: {report_error}", file=sys.stderr)
                    raise
                report(args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ChapterError, OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
