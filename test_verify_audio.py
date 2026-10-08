from __future__ import annotations

import base64
import contextlib
import io
import itertools
import json
import subprocess
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

import verify_audio as qa

TEMP_ROOT = "/home/delorenj/.claude/tmp/opencode"


def wav_bytes() -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as writer:
        writer.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        writer.writeframes(b"\x00\x00" * 160)
    return stream.getvalue()


def response(path: Path, transcript: str = "hello", **extra: object) -> dict[str, object]:
    return {
        "path": str(path), "status": "ok", "transcript": transcript,
        "model_revision": qa.MODEL_REVISION, "wall_seconds": 0.2,
        "model_load_seconds": 1.0, "started_utc": "2026-10-06T00:00:00+00:00",
        **extra,
    }


def process_for(*rows: dict[str, object], returncode: int = 0) -> Mock:
    process = Mock()
    process.returncode = returncode
    process.communicate.return_value = (
        "".join(json.dumps(row) + "\n" for row in rows).encode(), b"",
    )
    return process


class WordErrorTests(unittest.TestCase):
    def test_casefold_and_punctuation(self):
        self.assertEqual(qa.normalized_words("  WANG, Lung's ‘EARTH’—plough! "), ["wang", "lungs", "earthplough"])
        self.assertEqual(qa.normalized_words("Straße _ 123"), ["strasse", "123"])
        self.assertEqual(qa.normalized_words("... ’ -- \n"), [])
        self.assertEqual(qa.normalized_words(""), [])
        self.assertEqual(qa.word_errors("Hello, WORLD!", "hello world")["wer"], 0)

    def test_each_edit_type(self):
        result = qa.word_errors("one two three", "one four three extra")
        self.assertEqual(result["substitutions"], 1)
        self.assertEqual(result["insertions"], 1)
        self.assertEqual(result["deletions"], 0)
        self.assertEqual(result["wer"], 2 / 3)
        self.assertEqual(result["edits"][0], {"operation": "substitution", "expected": "two", "actual": "four"})
        self.assertEqual(qa.word_errors("one two three", "one three")["deletions"], 1)

    def test_repetition_not_capped_or_normalized_away(self):
        result = qa.word_errors("earth", "earth earth earth earth")
        self.assertEqual(result["wer"], 3)
        self.assertEqual(result["insertions"], 3)
        self.assertEqual(qa.word_errors("plough", "plow")["substitutions"], 1)

    def test_empty_reference_policy(self):
        self.assertEqual(qa.word_errors("", "")["wer"], 0)
        self.assertEqual(qa.word_errors("earth", "")["wer"], 1)
        result = qa.word_errors("", "spoken control")
        self.assertIsNone(result["wer"])
        self.assertEqual(result["insertions"], 2)
        self.assertEqual(result["edit_distance"], 2)

    def test_edit_distance_exhaustive_small_sequences(self):
        sequences = [tuple(p) for length in range(4) for p in itertools.product(("a", "b"), repeat=length)]
        for expected in sequences:
            for actual in sequences:
                costs = list(range(len(actual) + 1))
                for i, word in enumerate(expected, 1):
                    row = [i]
                    for j, other in enumerate(actual, 1):
                        row.append(min(costs[j] + 1, row[j - 1] + 1, costs[j - 1] + (word != other)))
                    costs = row
                result = qa.word_errors(" ".join(expected), " ".join(actual))
                self.assertEqual(result["edit_distance"], costs[-1])
                self.assertEqual(result["edit_distance"], sum(result[k] for k in ("substitutions", "deletions", "insertions")))
                self.assertEqual(result["actual_words"], result["expected_words"] - result["deletions"] + result["insertions"])


class FileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest = self.root / "chapter.json"
        self.sample_file = self.root / "samples.json"
        self.manifest.write_text(json.dumps({"voices": {"narrator": {"reference_text": "Reference words."}}}))

    def write_samples(self, rows: object) -> None:
        self.sample_file.write_text(json.dumps(rows))

    def test_manifest_references_mapping_and_list(self):
        samples, selection = qa.select_samples(self.manifest, self.root, "refs", None, 12)
        self.assertEqual(samples, [qa.Sample(self.root / "refs/narrator.wav", "Reference words.", "narrator", "ref")])
        self.assertEqual(selection["selected_refs"], 1)
        self.manifest.write_text(json.dumps({"voices": [{"id": "narrator", "reference_text": "List reference."}]}))
        self.assertEqual(qa.reference_samples(self.manifest, self.root)[0].expected, "List reference.")

    def test_unsafe_voice_id_rejected(self):
        self.manifest.write_text(json.dumps({"voices": {"../escape": {"reference_text": "No"}}}))
        with self.assertRaises(qa.VerificationError):
            qa.reference_samples(self.manifest, self.root)

    def test_metadata_sample_is_spaced_and_uses_request_item(self):
        directory = self.root / "chunks"
        directory.mkdir()
        for i in range(10):
            path = directory / f"{i:06d}.wav"
            path.write_bytes(wav_bytes())
            path.with_suffix(".json").write_text(json.dumps({"request": {"item": {"text": f"chunk {i}", "speaker": "narrator"}}}))
        samples, selection = qa.select_samples(self.manifest, self.root, "all", None, 3)
        self.assertEqual([sample.expected for sample in samples], ["Reference words.", "chunk 0", "chunk 4", "chunk 9"])
        self.assertEqual(selection["available_chunks"], 10)
        self.assertEqual(selection["selected_chunks"], 3)

    def test_missing_or_unknown_metadata_requires_explicit_samples(self):
        directory = self.root / "chunks"
        directory.mkdir()
        path = directory / "000001.wav"
        path.write_bytes(wav_bytes())
        with self.assertRaisesRegex(qa.VerificationError, "supply --samples"):
            qa.select_samples(self.manifest, self.root, "chunks", None, 12)
        path.with_suffix(".json").write_text(json.dumps({"payload": {"text": "Unknown interface"}}))
        with self.assertRaisesRegex(qa.VerificationError, "supply --samples"):
            qa.select_samples(self.manifest, self.root, "chunks", None, 12)

    def test_explicit_samples_do_not_require_manifest(self):
        self.manifest.unlink()
        absolute = self.root / "absolute.wav"
        self.write_samples([
            {"path": "refs/voice.wav", "expected": "reference", "speaker": "voice", "kind": "ref"},
            {"path": str(absolute), "expected": "", "speaker": "voice", "kind": "chunk"},
            {"path": "chunks/second.wav", "expected": "second", "speaker": "voice", "kind": "chunk"},
        ])
        refs, _ = qa.select_samples(self.manifest, self.root, "refs", self.sample_file, 1)
        chunks, selection = qa.select_samples(self.manifest, self.root, "chunks", self.sample_file, 1)
        self.assertEqual(refs[0].path, self.root / "refs/voice.wav")
        self.assertEqual(chunks[0].path, self.root / "chunks/second.wav")
        self.assertEqual(selection["available_chunks"], 2)

    def test_duplicate_and_bad_schema_rejected(self):
        row = {"path": "a.wav", "expected": "", "speaker": "a", "kind": "chunk"}
        for bad in ({}, [row, row], [{**row, "kind": "chapter"}], [{**row, "expected": None}], [{**row, "path": "a.mp3"}]):
            self.write_samples(bad)
            with self.assertRaises(qa.VerificationError):
                qa.select_samples(self.manifest, self.root, "all", self.sample_file, 12)

    def test_spaced_bounds(self):
        self.assertEqual(qa.spaced(list(range(20)), 4), [0, 6, 13, 19])
        self.assertEqual(qa.spaced([0, 1, 2], 1), [1])
        self.assertEqual(qa.spaced([], 12), [])
        with self.assertRaises(qa.VerificationError):
            qa.spaced([1], 0)

    def test_wav_encoding_and_truncation(self):
        path = self.root / "clip.wav"
        audio = wav_bytes()
        path.write_bytes(audio)
        sample = qa.Sample(path, "hello", "narrator", "chunk")
        record, metadata = qa.wav_record(sample)
        self.assertEqual(set(record), {"path", "wav_b64"})
        self.assertEqual(base64.b64decode(record["wav_b64"]), audio)
        self.assertEqual(metadata["duration_seconds"], 0.01)
        path.write_bytes(audio[:-2])
        with self.assertRaises(qa.VerificationError):
            qa.wav_record(sample)

    def test_report_micro_wer_and_input_preservation(self):
        paths = [self.root / f"{name}.wav" for name in ("a", "b", "c")]
        for path in paths[:2]:
            path.write_bytes(wav_bytes())
        samples = [qa.Sample(path, text, "narrator", "chunk") for path, text in zip(paths, ("one", "one two three", "missing"), strict=True)]
        process = process_for(response(paths[0], "one one one"), response(paths[1], "one two three"))
        with patch("verify_audio.subprocess.Popen", return_value=process) as popen:
            report = qa.verify(samples, 900, {})
        self.assertEqual(report["summary"]["micro_wer"], 0.5)
        self.assertEqual(report["summary"]["expected_words_scored"], 4)
        self.assertEqual(report["summary"]["expected_words_selected"], 5)
        self.assertEqual(report["summary"]["worst_wer_samples"][0]["wer"], 2)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["samples"][2]["status"], "error")
        self.assertIn("diagnostic", report["assessment"])
        self.assertEqual(report["scope"], "selected_samples_only_not_whole_chapter")
        popen.assert_called_once()
        for path in paths[:2]:
            self.assertEqual(path.read_bytes(), wav_bytes())
        self.assertFalse(paths[2].exists())

    def test_cli_writes_only_report_and_returns_success_for_diagnostic_mismatch(self):
        path, out = self.root / "clip.wav", self.root / "qa.json"
        path.write_bytes(wav_bytes())
        self.write_samples([{"path": "clip.wav", "expected": "hello", "speaker": "narrator", "kind": "chunk"}])
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        process = process_for(response(path, "unexpected text"))
        with patch("verify_audio.subprocess.Popen", return_value=process), contextlib.redirect_stdout(io.StringIO()):
            code = qa.main(["--root", str(self.root), "--samples", str(self.sample_file), "--out", str(out)])
        self.assertEqual(code, 0)
        report = json.loads(out.read_text())
        self.assertEqual(report["samples"][0]["wer"], 2)
        self.assertEqual(report["model"]["revision"], qa.MODEL_REVISION)
        for name, data in before.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        self.assertEqual({p.name for p in self.root.iterdir()} - before.keys(), {"qa.json"})

    def test_output_input_collision_rejected_before_docker(self):
        path = self.root / "clip.wav"
        self.write_samples([{"path": "clip.wav", "expected": "hello", "speaker": "narrator", "kind": "chunk"}])
        with patch("verify_audio.subprocess.Popen") as popen, contextlib.redirect_stderr(io.StringIO()):
            for out in (self.sample_file, self.manifest, path.with_suffix(".json"), self.root / "prepared.json", self.root / "chunks/unsampled.json", self.root / "refs/other.json"):
                self.assertEqual(qa.main(["--root", str(self.root), "--samples", str(self.sample_file), "--manifest", str(self.manifest), "--out", str(out)]), 1)
        popen.assert_not_called()

    def test_missing_wav_report_without_docker(self):
        sample = qa.Sample(self.root / "absent.wav", "hello", "narrator", "ref")
        with patch("verify_audio.subprocess.Popen") as popen:
            report = qa.verify([sample], 900, {})
        popen.assert_not_called()
        self.assertEqual(report["status"], "incomplete")
        self.assertIsNone(report["summary"]["micro_wer"])
        self.assertIsNone(report["samples"][0]["actual_transcript"])

    def test_invalid_cli_timeout_and_selection_do_not_launch(self):
        with patch("verify_audio.subprocess.Popen") as popen, contextlib.redirect_stderr(io.StringIO()):
            for value in ("nan", "inf", "0", "-1"):
                self.assertEqual(qa.main(["--timeout", value]), 1)
            self.assertEqual(qa.main(["--max-chunks", "0"]), 1)
            self.assertEqual(qa.main(["--root", str(self.root), "--phase", "chunks"]), 1)
        popen.assert_not_called()


class DockerTests(unittest.TestCase):
    def test_one_cpu_process_and_audio_only_protocol(self):
        records = [{"path": "a", "wav_b64": "YQ=="}, {"path": "b", "wav_b64": "Yg=="}]
        process = process_for(response(Path("b"), "second"), response(Path("a"), "first"))
        with patch("verify_audio.subprocess.Popen", return_value=process) as popen:
            results, batch = qa.transcribe_records(records, 900)
        self.assertEqual(results["a"]["transcript"], "first")
        self.assertEqual(results["b"]["transcript"], "second")
        self.assertFalse(batch["timed_out"])
        popen.assert_called_once()
        command = popen.call_args.args[0]
        self.assertEqual(command[:3], ["docker", "exec", "-i"])
        self.assertIn("HF_HUB_OFFLINE=1", command)
        self.assertIn("CUDA_VISIBLE_DEVICES=", command)
        self.assertIn("/usr/bin/timeout", command)
        self.assertIn("895.0s", command)
        self.assertEqual(command[-1], qa.ASR_CODE)
        process.communicate.assert_called_once()
        self.assertEqual(process.communicate.call_args.kwargs["timeout"], 900)
        sent = [json.loads(line) for line in process.communicate.call_args.kwargs["input"].splitlines()]
        self.assertEqual(sent, records)

    def test_timeout_kills_client_reaps_and_retains_partial_results(self):
        process = process_for(response(Path("a")))
        partial = process.communicate.return_value
        process.communicate.side_effect = [subprocess.TimeoutExpired("docker", 900), partial]
        process.returncode = -9
        with patch("verify_audio.subprocess.Popen", return_value=process):
            results, batch = qa.transcribe_records([{"path": "a", "wav_b64": ""}, {"path": "b", "wav_b64": ""}], 900)
        process.kill.assert_called_once()
        self.assertEqual(process.communicate.call_count, 2)
        self.assertEqual(results["a"]["status"], "ok")
        self.assertEqual(results["b"]["status"], "timeout")
        self.assertTrue(batch["timed_out"])

    def test_container_deadline_and_launch_failure(self):
        with patch("verify_audio.subprocess.Popen", return_value=process_for(returncode=124)):
            results, batch = qa.transcribe_records([{"path": "a", "wav_b64": ""}], 900)
        self.assertTrue(batch["timed_out"])
        self.assertEqual(results["a"]["status"], "timeout")
        with patch("verify_audio.subprocess.Popen", side_effect=FileNotFoundError("docker unavailable")):
            results, batch = qa.transcribe_records([{"path": "a", "wav_b64": ""}], 900)
        self.assertIn("docker unavailable", batch["error"])
        self.assertEqual(results["a"]["status"], "error")

    def test_response_protocol_validation(self):
        good = response(Path("a"))
        for row in ({**good, "model_revision": "bad"}, {**good, "transcript": None}, {**good, "wall_seconds": float("nan")}, {**good, "status": "other"}):
            results, errors = qa.parse_records(json.dumps(row).encode(), {"a"})
            self.assertFalse(results)
            self.assertTrue(errors)
        output = (json.dumps(good) + "\n" + json.dumps(good) + "\nnot json\n" + json.dumps(response(Path("unknown")))).encode()
        results, errors = qa.parse_records(output, {"a"})
        self.assertEqual(results["a"]["status"], "error")
        self.assertEqual(len(errors), 3)

    def test_embedded_worker_loads_once_and_uses_no_reference_prompt(self):
        constructor = Mock()
        model = constructor.return_value
        word = types.SimpleNamespace(word=" heard", start=0.0, end=0.1, probability=0.9)
        segment = types.SimpleNamespace(text=" heard", start=0.0, end=0.1, words=[word])
        model.transcribe.side_effect = lambda *args, **kwargs: (iter([segment]), types.SimpleNamespace(duration=0.1))
        module = types.ModuleType("faster_whisper")
        setattr(module, "WhisperModel", constructor)
        records = [{"path": name, "wav_b64": base64.b64encode(wav_bytes()).decode()} for name in ("a", "b")]
        stdout = io.StringIO()
        with patch.dict(sys.modules, {"faster_whisper": module}), patch("pathlib.Path") as path, patch("sys.stdin", io.StringIO("".join(json.dumps(r) + "\n" for r in records))), contextlib.redirect_stdout(stdout):
            (path.return_value / "anything").is_file.return_value = True
            exec(qa.ASR_CODE, {})
        constructor.assert_called_once_with(qa.MODEL_PATH, device="cpu", compute_type="int8", cpu_threads=2, num_workers=1, local_files_only=True)
        self.assertEqual(model.transcribe.call_count, 2)
        for call in model.transcribe.call_args_list:
            self.assertIsInstance(call.args[0], io.BytesIO)
            self.assertEqual(call.args[0].getvalue(), wav_bytes())
            self.assertFalse(call.kwargs["vad_filter"])
            self.assertEqual(call.kwargs["repetition_penalty"], 1.0)
            self.assertEqual(call.kwargs["no_repeat_ngram_size"], 0)
            self.assertFalse(call.kwargs["condition_on_previous_text"])
            self.assertNotIn("initial_prompt", call.kwargs)
            self.assertNotIn("prefix", call.kwargs)
            self.assertNotIn("hotwords", call.kwargs)
        rows = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual([row["path"] for row in rows], ["a", "b"])
        self.assertTrue(all(row["transcript"] == "heard" for row in rows))

    def test_embedded_worker_model_load_failure_reports_every_clip(self):
        constructor = Mock(side_effect=RuntimeError("cached model failed"))
        module = types.ModuleType("faster_whisper")
        setattr(module, "WhisperModel", constructor)
        records = [{"path": name, "wav_b64": "YQ=="} for name in ("a", "b")]
        stdout = io.StringIO()
        with patch.dict(sys.modules, {"faster_whisper": module}), patch("pathlib.Path") as path, patch("sys.stdin", io.StringIO("".join(json.dumps(r) + "\n" for r in records))), contextlib.redirect_stdout(stdout):
            (path.return_value / "anything").is_file.return_value = True
            with self.assertRaises(SystemExit) as exited:
                exec(qa.ASR_CODE, {})
        self.assertEqual(exited.exception.code, 1)
        constructor.assert_called_once()
        rows = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual([row["path"] for row in rows], ["a", "b"])
        self.assertTrue(all(row["status"] == "error" and "cached model failed" in row["error"] for row in rows))

    def test_nonzero_exit_is_not_a_complete_report_even_with_transcripts(self):
        sample = qa.Sample(Path("/unused.wav"), "hello", "voice", "chunk")
        process = process_for(response(sample.path), returncode=1)
        with patch("verify_audio.wav_record", return_value=({"path": str(sample.path), "wav_b64": "YQ=="}, {})), patch("verify_audio.subprocess.Popen", return_value=process):
            report = qa.verify([sample], 900, {})
        self.assertEqual(report["samples"][0]["status"], "ok")
        self.assertEqual(report["status"], "incomplete")

    def test_embedded_worker_continues_after_clip_error(self):
        constructor = Mock()
        module = types.ModuleType("faster_whisper")
        setattr(module, "WhisperModel", constructor)
        constructor.return_value.transcribe.return_value = (iter([]), types.SimpleNamespace(duration=0.1))
        records = [{"path": "bad", "wav_b64": "!"}, {"path": "good", "wav_b64": "YQ=="}]
        stdout = io.StringIO()
        with patch.dict(sys.modules, {"faster_whisper": module}), patch("pathlib.Path") as path, patch("sys.stdin", io.StringIO("".join(json.dumps(r) + "\n" for r in records))), contextlib.redirect_stdout(stdout):
            (path.return_value / "anything").is_file.return_value = True
            with self.assertRaises(SystemExit) as exited:
                exec(qa.ASR_CODE, {})
        self.assertEqual(exited.exception.code, 1)
        rows = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual([row["status"] for row in rows], ["error", "ok"])
        self.assertEqual(rows[1]["transcript"], "")


if __name__ == "__main__":
    unittest.main()
