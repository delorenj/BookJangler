from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import random
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from typing import Any
from unittest.mock import patch

import render_chapter as rc

REVISION = "a" * 40


def pcm_wav(frames: int = 100, rate: int = 1000, channels: int = 1, width: int = 2) -> bytes:
    stream = io.BytesIO()
    sample = (140 if width == 1 else 1000).to_bytes(width, "little", signed=width != 1)
    with wave.open(stream, "wb") as writer:
        writer.setparams((channels, width, rate, 0, "NONE", "not compressed"))
        writer.writeframes(sample * channels * frames)
    return stream.getvalue()


def synth_result(audio: bytes | None = None, **overrides):
    if audio is None:
        audio = pcm_wav()
    info = rc.wav_metadata(audio)
    response = {
        "engine": "voxcpm", "wav_b64": base64.b64encode(audio).decode("ascii"),
        "sample_rate": info["sample_rate"], "bytes": len(audio),
        "duration_s": info["duration_seconds"], **overrides,
    }
    return subprocess.CompletedProcess(rc.SYNTH_COMMAND, 0, rc.canonical(response), b"")


class ChapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.manifest_path = self.directory / "chapter.json"
        self.root = self.directory / "output"
        self.root.mkdir()
        (self.directory / "source.pdf").write_bytes(b"%PDF-1.4\n")
        self.manifest: dict[str, Any] = {
            "title": "An experiment", "chapter": 1,
            "source": {"pdf": "source.pdf", "pdf_pages": [3, 4], "note": "Authored elsewhere"},
            "voices": {
                "narrator": {
                    "description": "A calm warm narrator",
                    "reference_text": "The morning light fills the quiet room as a gentle breeze moves across the garden and the day begins anew.",
                },
                "farmer": {
                    "description": "An older measured man",
                    "reference_text": "A few clouds drift above the trees while the soft afternoon light falls upon the road beside the open fields.",
                },
            },
            "segments": [
                {"speaker": "narrator", "text": "A quiet morning. Another sentence.", "paragraph": 1},
                {"speaker": "farmer", "text": "We have much to do today.", "paragraph": 1},
                {"speaker": "narrator", "text": "And so the day began.", "paragraph": 2},
            ],
        }
        rc.write_json(self.manifest_path, self.manifest)
        self.run_patch = patch.object(rc.subprocess, "run", side_effect=AssertionError("Unmocked subprocess"))
        self.run_mock = self.run_patch.start()
        self.addCleanup(self.run_patch.stop)

    def plan(self):
        def count(texts):
            return {"model_revision": REVISION, "input_text_tokens": [7 + i for i in range(len(texts))]}
        with patch.object(rc, "tokenize_texts", side_effect=count):
            return rc.prepare(self.manifest_path, self.root)

    def spec(self, plan=None, phase="design", index=0, reference=None):
        plan = plan or self.plan()
        item = plan["designs" if phase == "design" else "chunks"][index]
        ref_hash = hashlib.sha256(reference).hexdigest() if reference is not None else None
        return rc.request_spec(plan, item, phase, ref_hash)

    def generate(self, spec, reference=None):
        self.run_mock.side_effect = None
        self.run_mock.return_value = synth_result()
        return rc.generate_request(self.root, spec, reference)

    def fake_master(self, raw, mastered, mp3, rate):
        self.assertEqual(rc.wav_metadata(raw)["sample_rate"], rate)
        mastered.write_bytes(raw.read_bytes())
        mp3.write_bytes(b"ID3mock-mp3")

    def test_validation_defaults_and_text_preservation(self):
        self.manifest["segments"][0]["text"] = '  She said, “Stay.”\nThen\tleft.  '
        result = rc.validate_manifest(self.manifest, self.directory)
        self.assertEqual(result["settings"], {"cfg": 2.0, "steps": 12})
        self.assertEqual(result["segments"][0]["text"], 'She said, “Stay.” Then left.')
        self.assertEqual(result["source"]["note"], "Authored elsewhere")
        self.assertNotIn("settings", self.manifest)

    def test_validation_rejects_bad_manifests(self):
        changes = [
            lambda m: m.update(title=" "),
            lambda m: m.update(chapter=True),
            lambda m: m.update(source={"pdf": "missing.pdf", "pdf_pages": [1]}),
            lambda m: m["source"].update(pdf_pages=[0]),
            lambda m: m["source"].update(pdf_pages=[True]),
            lambda m: m["source"].update(pdf_pages=[4, 3]),
            lambda m: m["source"].update(pdf_pages=[3, 3]),
            lambda m: m.update(settings={"cfg": float("nan")}),
            lambda m: m.update(settings={"cfg": True}),
            lambda m: m.update(settings={"cfg": 0.5}),
            lambda m: m.update(settings={"steps": 51}),
            lambda m: m.update(settings={"steps": 12.0}),
            lambda m: m.update(settings={"engine": "elevenlabs"}),
            lambda m: m.update(voices={"../escape": m["voices"]["narrator"]}),
            lambda m: m["voices"]["narrator"].update(description="(Already wrapped)"),
            lambda m: m["voices"]["narrator"].update(reference_text=""),
            lambda m: m["segments"][0].update(speaker="stranger"),
            lambda m: m["segments"][0].update(text=""),
            lambda m: m["segments"][0].update(paragraph=True),
            lambda m: m["segments"][0].update(paragraph=-1),
            lambda m: m["segments"][1].update(paragraph=0),
            lambda m: m.update(segments=[]),
        ]
        for change in changes:
            manifest = copy.deepcopy(self.manifest)
            change(manifest)
            with self.subTest(manifest=manifest), self.assertRaises(rc.ChapterError):
                rc.validate_manifest(manifest, self.directory)
        for value in ([], None, "bad"):
            with self.subTest(value=value), self.assertRaises(rc.ChapterError):
                rc.validate_manifest(value, self.directory)

    def test_chunk_complete_sentence_exception_and_coverage(self):
        sentence = "word " * 99 + "end."
        self.assertGreater(len(sentence), 450)
        self.assertLessEqual(len(sentence), 650)
        chunks = rc.chunk_text("First. " + sentence + " Last.")
        self.assertEqual(chunks, ["First.", sentence, "Last."])
        long_sentence = "word " * 300 + "end."
        chunks = rc.chunk_text(long_sentence)
        self.assertEqual(" ".join(chunks), rc.normalized(long_sentence))
        self.assertTrue(all(len(chunk) <= 450 for chunk in chunks))
        self.assertEqual(" ".join(chunks).split(), long_sentence.split())

    def test_chunk_punctuation_and_quote_coverage(self):
        text = '  “Hello there!”\nHe said, “Stay.”  A long\tending. '
        chunks = rc.chunk_text(text, target=20, sentence_max=40)
        self.assertEqual(" ".join(chunks), rc.normalized(text))
        self.assertEqual(chunks[0], '“Hello there!”')
        clause = "alpha " * 55 + "; " + "beta " * 170 + "end."
        chunks = rc.chunk_text(clause)
        self.assertTrue(chunks[0].endswith(";"))
        self.assertEqual(" ".join(chunks), rc.normalized(clause))

    def test_chunk_never_splits_word_and_rejects_impossible_word(self):
        word = "x" * 600
        self.assertEqual(rc.chunk_text(word), [word])
        chunks = rc.chunk_text("short " + word + " end.")
        self.assertIn(word, " ".join(chunks).split())
        self.assertEqual(" ".join(chunks), "short " + word + " end.")
        for text in ("", "x" * 651):
            with self.assertRaises(rc.ChapterError):
                rc.chunk_text(text)

    def test_chunk_randomized_invariants(self):
        generator = random.Random(1729)
        for _ in range(100):
            words = ["x" * generator.randint(1, 20) for _ in range(generator.randint(1, 400))]
            for index in range(0, len(words), 25):
                words[index] += generator.choice((".", ",", ";", "!", "?"))
            text = "\n  ".join(words)
            chunks = rc.chunk_text(text)
            self.assertEqual(" ".join(chunks), rc.normalized(text))
            self.assertTrue(all(len(chunk) <= 650 for chunk in chunks))
            self.assertEqual(" ".join(chunks).split(), words)

    def test_prepare_counts_all_full_designs_and_chunks_preserves_turns(self):
        seen = []
        def tokenizer(texts):
            seen.extend(texts)
            return {"model_revision": REVISION, "input_text_tokens": [21, 22, 23, 24, 25]}
        with patch.object(rc, "tokenize_texts", side_effect=tokenizer):
            plan = rc.prepare(self.manifest_path, self.root)
        self.assertEqual(seen[:2], [
            f"({self.manifest['voices'][speaker]['description']}){self.manifest['voices'][speaker]['reference_text']}"
            for speaker in ("farmer", "narrator")
        ])
        self.assertEqual(seen[2:], [segment["text"] for segment in self.manifest["segments"]])
        self.assertEqual(plan["planned_input_text_tokens"], {"design": 43, "chapter": 72})
        self.assertEqual([item["segment"] for item in plan["chunks"]], [0, 1, 2])
        self.assertEqual(rc.read_json(self.root / "prepared.json"), plan)
        self.assertEqual(rc.request_records(self.root), [])
        self.run_mock.assert_not_called()

    def test_prepare_and_load_reject_manifest_or_model_changes(self):
        plan = self.plan()
        with patch.object(rc, "build_plan", return_value=plan):
            self.assertEqual(rc.load_plan(self.manifest_path, self.root), plan)
        for changed in (
            {**plan, "model_revision": "b" * 40},
            {**plan, "planned_input_text_tokens": {"design": 99, "chapter": 99}},
        ):
            with patch.object(rc, "build_plan", return_value=changed):
                with self.assertRaises(rc.ChapterError):
                    rc.prepare(self.manifest_path, self.root)
                with self.assertRaises(rc.ChapterError):
                    rc.load_plan(self.manifest_path, self.root)
        self.manifest["segments"][0]["text"] = "Changed text."
        rc.write_json(self.manifest_path, self.manifest)
        with patch.object(rc, "tokenize_texts", return_value={
            "model_revision": REVISION, "input_text_tokens": [7, 8, 9, 10, 11],
        }), self.assertRaises(rc.ChapterError):
            rc.load_plan(self.manifest_path, self.root)

    def test_tokenizer_batch_command_and_exact_integer_counts(self):
        response = {"model_revision": REVISION, "input_text_tokens": [9, 11]}
        self.run_mock.side_effect = None
        self.run_mock.return_value = subprocess.CompletedProcess([], 0, rc.canonical(response), b"")
        texts = ["(Calm voice)All of the full text.", "你好\nworld"]
        self.assertEqual(rc.tokenize_texts(texts), response)
        args, kwargs = self.run_mock.call_args
        self.assertEqual(args[0], rc.TOKENIZER_COMMAND)
        self.assertEqual(json.loads(kwargs["input"]), texts)
        self.assertTrue(kwargs["check"])
        self.assertEqual(kwargs["timeout"], 120)
        self.assertIn("mask_multichar_chinese_tokens", rc.TOKENIZER_CODE)
        self.assertIn("local_files_only=True", rc.TOKENIZER_CODE)
        self.assertNotIn("VoxCPM.from_pretrained", rc.TOKENIZER_CODE)
        self.assertNotIn('ids["input_ids"]', rc.TOKENIZER_CODE)
        for response in (
            {"model_revision": REVISION, "input_text_tokens": [9]},
            {"model_revision": REVISION, "input_text_tokens": [9, True]},
            {"model_revision": "bad", "input_text_tokens": [9, 11]},
        ):
            self.run_mock.return_value = subprocess.CompletedProcess([], 0, rc.canonical(response), b"")
            with self.assertRaises(rc.ChapterError):
                rc.tokenize_texts(texts)

    def test_wav_metadata_and_response_validation(self):
        audio = pcm_wav(frames=123, rate=1000, channels=2)
        metadata = rc.wav_metadata(audio)
        self.assertEqual(metadata["frames"], 123)
        self.assertEqual(metadata["duration_seconds"], 0.123)
        self.assertEqual(metadata["channels"], 2)
        self.assertEqual(metadata["sample_width"], 2)
        self.assertEqual(metadata["sha256"], hashlib.sha256(audio).hexdigest())
        path = self.directory / "audio.wav"
        path.write_bytes(audio)
        self.assertEqual(rc.wav_metadata(path), metadata)
        response = json.loads(synth_result(audio).stdout)
        self.assertEqual(rc.decode_response(response), (audio, metadata))
        for overrides in (
            {"engine": "elevenlabs"}, {"wav_b64": "!not base64!"}, {"sample_rate": 16000},
            {"sample_rate": True}, {"bytes": 0}, {"duration_s": 2}, {"wav_b64": None},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(rc.ChapterError):
                rc.decode_response({**response, **overrides})
        for broken in (b"not a wav", audio[:-1], pcm_wav(frames=0)):
            with self.subTest(broken=broken[:12]), self.assertRaises(rc.ChapterError):
                rc.wav_metadata(broken)
        truncated = bytearray(audio[:-2])
        truncated[4:8] = (len(truncated) - 8).to_bytes(4, "little")
        with self.assertRaises(rc.ChapterError):
            rc.wav_metadata(bytes(truncated))

    def test_fingerprint_covers_exact_text_reference_settings_revision_and_turn(self):
        spec = self.spec()
        self.assertEqual(rc.digest(spec), rc.digest(copy.deepcopy(spec)))
        for change in (
            lambda value: value["item"].update(text=value["item"]["text"] + "."),
            lambda value: value["settings"].update(cfg=3.0),
            lambda value: value["settings"].update(steps=13),
            lambda value: value.update(model_revision="b" * 40),
            lambda value: value.update(reference_sha256="f" * 64),
            lambda value: value["item"].update(paragraph=3),
        ):
            altered = copy.deepcopy(spec)
            change(altered)
            self.assertNotEqual(rc.digest(spec), rc.digest(altered))

    def test_request_success_receipt_and_cache_reuse_does_not_count_new_call(self):
        spec = self.spec()
        with patch.object(rc.time, "perf_counter", side_effect=[1.0, 3.5]):
            metadata = self.generate(spec)
        records = rc.request_records(self.root)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "success")
        self.assertEqual(records[0]["wall_seconds"], 2.5)
        self.assertEqual(records[0]["request_fingerprint"], rc.digest(spec))
        self.assertEqual(records[0]["wav"], metadata["wav"])
        self.assertEqual(records[0]["phase"], "design")
        self.assertIn("+00:00", records[0]["timestamp_utc"])
        args, kwargs = self.run_mock.call_args
        self.assertEqual(args[0], rc.SYNTH_COMMAND)
        self.assertEqual(kwargs["timeout"], 250)
        self.assertNotIn("shell", kwargs)
        payload = json.loads(kwargs["input"])
        self.assertEqual(payload, {"text": spec["item"]["text"], "cfg": 2.0, "steps": 12})
        self.run_mock.reset_mock()
        self.assertEqual(rc.generate_request(self.root, spec), metadata)
        self.run_mock.assert_not_called()
        self.assertEqual(len(rc.request_records(self.root)), 1)

    def test_reference_only_payload_uses_exact_frozen_bytes(self):
        reference = pcm_wav(frames=200)
        spec = self.spec(phase="chapter", reference=reference)
        self.generate(spec, reference)
        payload = json.loads(self.run_mock.call_args.kwargs["input"])
        self.assertEqual(base64.b64decode(payload["reference_audio_b64"]), reference)
        self.assertEqual(set(payload), {"text", "cfg", "steps", "reference_audio_b64"})
        self.assertEqual(rc.request_records(self.root)[0]["reference_sha256"], hashlib.sha256(reference).hexdigest())

    def test_cache_invalidation_blocks_stale_and_corrupt_audio(self):
        spec = self.spec()
        self.generate(spec)
        changed = copy.deepcopy(spec)
        changed["settings"]["steps"] = 13
        self.run_mock.reset_mock()
        with self.assertRaises(rc.ChapterError):
            rc.generate_request(self.root, changed)
        self.run_mock.assert_not_called()
        wav_path, _ = rc.cache_paths(self.root, "design", spec["item"]["id"])
        data = bytearray(wav_path.read_bytes())
        data[-1] ^= 1
        wav_path.write_bytes(data)
        with self.assertRaises(rc.ChapterError):
            rc.generate_request(self.root, spec)
        self.run_mock.assert_not_called()

    def test_cache_reference_hash_and_unknown_receipt_fail(self):
        reference = pcm_wav(frames=200)
        spec = self.spec(phase="chapter", reference=reference)
        self.generate(spec, reference)
        changed = copy.deepcopy(spec)
        changed["reference_sha256"] = "0" * 64
        with self.assertRaises(rc.ChapterError):
            rc.generate_request(self.root, changed, reference)
        (self.root / "requests.jsonl").unlink()
        with self.assertRaises(rc.ChapterError):
            rc.check_cache(self.root, spec)

    def test_incomplete_missing_and_unknown_reference_fail_without_generation(self):
        plan = self.plan()
        spec = self.spec(plan)
        self.generate(spec)
        wav_path, metadata_path = rc.cache_paths(self.root, "design", spec["item"]["id"])
        metadata_path.unlink()
        self.run_mock.reset_mock()
        with self.assertRaises(rc.ChapterError):
            rc.voices(plan, self.root)
        self.run_mock.assert_not_called()
        wav_path.unlink()
        with self.assertRaises(rc.ChapterError):
            rc.voices(plan, self.root, retry_failed=True)
        self.run_mock.assert_not_called()
        (wav_path.parent / "unknown.wav").write_bytes(pcm_wav())
        with self.assertRaises(rc.ChapterError):
            rc.voices(plan, self.root)
        self.run_mock.assert_not_called()

    def test_failures_are_logged_once_and_not_automatically_retried(self):
        spec = self.spec()
        self.run_mock.side_effect = subprocess.TimeoutExpired(rc.SYNTH_COMMAND, 250)
        with patch.object(rc.time, "perf_counter", side_effect=[1.0, 251.0]), self.assertRaises(subprocess.TimeoutExpired):
            rc.generate_request(self.root, spec)
        rows = rc.request_records(self.root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "failure")
        self.assertEqual(rows[0]["wall_seconds"], 250)
        self.assertEqual(rows[0]["input_text_tokens"], spec["item"]["input_text_tokens"])
        self.assertIn("TimeoutExpired", rows[0]["error"])
        self.assertIsNone(rows[0]["wav"])
        self.assertEqual(self.run_mock.call_count, 1)
        with self.assertRaisesRegex(rc.ChapterError, "timeout does not prove cancellation"):
            rc.generate_request(self.root, spec)
        self.assertEqual(self.run_mock.call_count, 1)
        self.run_mock.side_effect = None
        self.run_mock.return_value = synth_result()
        rc.generate_request(self.root, spec, retry_failed=True)
        self.assertEqual([row["status"] for row in rc.request_records(self.root)], ["failure", "success"])

    def test_invalid_engine_and_response_failures_are_logged_before_raising(self):
        spec = self.spec()
        self.run_mock.side_effect = None
        self.run_mock.return_value = synth_result(engine="elevenlabs")
        with self.assertRaises(rc.ChapterError):
            rc.generate_request(self.root, spec)
        rows = rc.request_records(self.root)
        self.assertEqual(rows[0]["status"], "failure")
        self.assertIn("fallback is forbidden", rows[0]["error"])
        wav_path, _ = rc.cache_paths(self.root, "design", spec["item"]["id"])
        self.assertFalse(wav_path.exists())

    def test_unfinished_request_blocks_resume_and_report_counts_it_once(self):
        spec = self.spec()
        self.run_mock.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            rc.generate_request(self.root, spec)
        path = self.root / "requests.jsonl"
        first_line = path.read_bytes().splitlines()[0]
        path.write_bytes(first_line + b"\n")
        with self.assertRaises(rc.ChapterError):
            rc.generate_request(self.root, spec)
        totals = rc.report(self.root)["totals"]
        self.assertEqual(totals["requests"], 1)
        self.assertEqual(totals["unfinished_calls"], 1)
        self.assertFalse(totals["wall_time_complete"])

    def test_assembly_streams_matching_pcm_adds_pauses_and_tiny_fades(self):
        paths = []
        items = [
            {"segment": 0, "paragraph": 1}, {"segment": 0, "paragraph": 1},
            {"segment": 1, "paragraph": 1}, {"segment": 2, "paragraph": 2},
        ]
        for index, item in enumerate(items):
            path = self.directory / f"{index}.wav"
            path.write_bytes(pcm_wav())
            paths.append((item, path))
        output = self.directory / "assembled.wav"
        result = rc.assemble_wav(paths, output)
        self.assertEqual(result["pause_frames"], 400)
        self.assertEqual(result["pause_seconds"], 0.4)
        self.assertEqual(result["wav"]["frames"], 800)
        with wave.open(str(output), "rb") as reader:
            samples = struct.unpack("<800h", reader.readframes(800))
        self.assertEqual(samples[0], 0)
        self.assertEqual(samples[7], 1000)
        self.assertEqual(samples[91], 1000)
        self.assertEqual(samples[99], 0)
        self.assertTrue(all(sample == 0 for sample in samples[200:320]))
        self.assertTrue(all(sample == 0 for sample in samples[420:700]))
        self.assertEqual(samples[-1], 0)
        self.run_mock.assert_not_called()

    def test_assembly_mismatched_rates_channels_widths_fail(self):
        first = self.directory / "first.wav"
        second = self.directory / "second.wav"
        first.write_bytes(pcm_wav())
        for kwargs in ({"rate": 2000}, {"channels": 2}, {"width": 1}):
            second.write_bytes(pcm_wav(**kwargs))
            with self.subTest(kwargs=kwargs), self.assertRaises(rc.ChapterError):
                rc.assemble_wav([
                    ({"segment": 0, "paragraph": 1}, first),
                    ({"segment": 1, "paragraph": 1}, second),
                ], self.directory / "out.wav")

    def test_fades_stream_across_blocks_and_support_pcm_widths(self):
        for width in (1, 2, 3, 4):
            path = self.directory / f"width{width}.wav"
            path.write_bytes(pcm_wav(frames=17000, channels=2, width=width))
            output = self.directory / f"out{width}.wav"
            result = rc.assemble_wav([({"segment": 0, "paragraph": 1}, path)], output)
            self.assertEqual(result["wav"]["frames"], 17000)
            with wave.open(str(output), "rb") as reader:
                first = reader.readframes(1)
                reader.setpos(16999)
                last = reader.readframes(1)
            silence = (b"\x80" if width == 1 else b"\x00" * width) * 2
            self.assertEqual(first, silence)
            self.assertEqual(last, silence)

    def test_mastering_two_pass_loudnorm_and_128k_mp3(self):
        raw, mastered, mp3 = (self.directory / name for name in ("raw.wav", "mastered.wav", "chapter.mp3"))
        raw.write_bytes(pcm_wav())
        measured = {
            "input_i": "-24.0", "input_tp": "-6.0", "input_lra": "2.0",
            "input_thresh": "-34.0", "target_offset": "0.0",
        }
        calls = []
        def run(command, **kwargs):
            calls.append((command, kwargs))
            if len(calls) == 1:
                return subprocess.CompletedProcess(command, 0, b"", b"log\n" + rc.canonical(measured))
            destination = Path(command[-1])
            destination.write_bytes(pcm_wav() if destination.suffix == ".wav" else b"ID3mp3")
            return subprocess.CompletedProcess(command, 0, b"", b"")
        self.run_mock.side_effect = run
        rc.master_audio(raw, mastered, mp3, 1000)
        self.assertEqual(len(calls), 3)
        self.assertIn("loudnorm=I=-20:TP=-3:LRA=7:print_format=json", calls[0][0])
        filter_text = calls[1][0][calls[1][0].index("-af") + 1]
        self.assertIn("loudnorm=I=-20:TP=-3:LRA=7:linear=true", filter_text)
        self.assertIn("measured_I=-24.0", filter_text)
        self.assertIn("128k", calls[2][0])
        self.assertTrue(mastered.exists())
        self.assertTrue(mp3.exists())
        self.assertTrue(all(kwargs["check"] and "shell" not in kwargs for _, kwargs in calls))

    def test_mastering_failures_do_not_get_swallowed(self):
        self.run_mock.side_effect = subprocess.CalledProcessError(1, ["ffmpeg"])
        with self.assertRaises(subprocess.CalledProcessError):
            rc.master_audio(self.directory / "raw.wav", self.directory / "master.wav", self.directory / "a.mp3", 1000)

    def test_render_and_resume_all_chunks_reuses_exact_reference_without_new_requests(self):
        plan = self.plan()
        self.run_mock.side_effect = None
        self.run_mock.return_value = synth_result()
        rc.voices(plan, self.root)
        with patch.object(rc, "master_audio", side_effect=self.fake_master) as master:
            first = rc.render(plan, self.root)
            self.assertEqual(master.call_count, 1)
            self.assertEqual(first["raw"]["pause_frames"], 400)
            self.assertEqual(self.run_mock.call_count, 5)
            reference_payloads = [json.loads(call.kwargs["input"]) for call in self.run_mock.call_args_list[2:]]
            for item, payload in zip(plan["chunks"], reference_payloads, strict=True):
                frozen = (self.root / "refs" / f"{item['speaker']}.wav").read_bytes()
                self.assertEqual(base64.b64decode(payload["reference_audio_b64"]), frozen)
                self.assertNotIn("prompt_text", payload)
            rc.voices(plan, self.root)
            second = rc.render(plan, self.root)
            self.assertEqual(first, second)
            self.assertEqual(master.call_count, 1)
            self.assertEqual(self.run_mock.call_count, 5)
        metrics = rc.report(self.root)
        self.assertEqual(metrics["totals"]["requests"], 5)
        self.assertEqual(metrics["by_phase"]["design"]["requests"], 2)
        self.assertEqual(metrics["by_phase"]["chapter"]["requests"], 3)
        self.assertEqual(metrics["totals"]["input_text_tokens"], 45)
        self.assertEqual(metrics["by_speaker"]["narrator"]["chapter"]["requests"], 2)
        self.assertEqual(metrics["costs"]["observed_paid_provider_charge_usd"], 0)
        self.assertIsNone(metrics["costs"]["energy_usd"])
        self.assertIsNone(metrics["llm_metrics"]["input_tokens"])
        self.assertIn("No actual LLM usage snapshot", metrics["llm_metrics"]["reason"])
        mp3 = self.root / "chapter.mp3"
        mp3.write_bytes(b"corrupt")
        with self.assertRaises(rc.ChapterError):
            rc.render(plan, self.root)
        self.assertEqual(self.run_mock.call_count, 5)

    def test_render_partial_failure_resumes_completed_chunks_only_on_explicit_retry(self):
        plan = self.plan()
        self.run_mock.side_effect = None
        self.run_mock.return_value = synth_result()
        rc.voices(plan, self.root)
        self.run_mock.reset_mock()
        self.run_mock.side_effect = [synth_result(), subprocess.TimeoutExpired(rc.SYNTH_COMMAND, 250)]
        with self.assertRaises(subprocess.TimeoutExpired):
            rc.render(plan, self.root)
        self.assertEqual(self.run_mock.call_count, 2)
        self.run_mock.reset_mock()
        with self.assertRaises(rc.ChapterError):
            rc.render(plan, self.root)
        self.run_mock.assert_not_called()
        self.run_mock.side_effect = None
        self.run_mock.return_value = synth_result()
        with patch.object(rc, "master_audio", side_effect=self.fake_master):
            rc.render(plan, self.root, retry_failed=True)
        self.assertEqual(self.run_mock.call_count, 2)
        records = rc.request_records(self.root)
        self.assertEqual(len(records), 6)
        self.assertEqual(sum(row["status"] == "failure" for row in records), 1)
        self.assertEqual(rc.report(self.root)["by_phase"]["chapter"]["requests"], 4)

    def test_render_missing_frozen_reference_fails_without_design_generation(self):
        plan = self.plan()
        with self.assertRaisesRegex(rc.ChapterError, "run voices first"):
            rc.render(plan, self.root)
        self.run_mock.assert_not_called()

    def test_report_totals_include_design_failures_and_per_speaker_time(self):
        plan = self.plan()
        design = self.spec(plan)
        with patch.object(rc.time, "perf_counter", side_effect=[10.0, 12.0]):
            self.generate(design)
        failed = self.spec(plan, phase="chapter", reference=pcm_wav())
        self.run_mock.side_effect = subprocess.CalledProcessError(22, rc.SYNTH_COMMAND)
        with patch.object(rc.time, "perf_counter", side_effect=[20.0, 23.0]), self.assertRaises(subprocess.CalledProcessError):
            rc.generate_request(self.root, failed, pcm_wav())
        result = rc.report(self.root)
        self.assertEqual(result["totals"]["requests"], 2)
        self.assertEqual(result["totals"]["input_text_tokens"], 16)
        self.assertEqual(result["totals"]["wall_seconds"], 5.0)
        self.assertAlmostEqual(result["totals"]["successful_audio_seconds"], 0.1)
        self.assertEqual(result["by_phase"]["design"]["wall_seconds"], 2.0)
        self.assertEqual(result["failed_calls"]["wall_seconds"], 3.0)
        self.assertEqual(result["failed_calls"]["input_text_tokens"], 9)
        self.assertEqual(result["by_speaker"]["farmer"]["totals"]["successful_audio_seconds"], 0.1)
        self.assertEqual(rc.read_json(self.root / "metrics.json"), result)

    def test_report_accepts_only_actual_llm_snapshot(self):
        snapshot = {"source": "provider ledger snapshot", "input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "cost_usd": None}
        result = rc.report(self.root, snapshot)
        self.assertEqual(result["llm_metrics"]["total_tokens"], 120)
        for value in ({}, {**snapshot, "total_tokens": 121}, {**snapshot, "cost_usd": -1}):
            with self.assertRaises(rc.ChapterError):
                rc.report(self.root, value)

    def test_report_preserves_cumulative_snapshot_without_double_counting_cache(self):
        snapshot = {
            "source": "OpenCode completed assistant-message usage fields",
            "totals": {
                "input_tokens": 100, "output_tokens": 20, "reasoning_tokens": 8,
                "cache_read_tokens": 300, "cache_write_tokens": 40, "reported_total_tokens": 460,
                "messages_missing_total_tokens": 0, "messages_missing_usage": 0,
                "messages_missing_cost": 0, "opencode_reported_cost_usd": 0,
            },
            "actual_llm_charge_usd": None, "sessions": [],
        }
        result = rc.report(self.root, snapshot)
        self.assertEqual(result["llm_metrics"]["total_tokens"], 460)
        self.assertEqual(result["llm_metrics"]["cache_read_tokens"], 300)
        self.assertEqual(result["llm_metrics"]["opencode_reported_cost_usd"], 0)
        self.assertIsNone(result["llm_metrics"]["cost_usd"])
        self.assertEqual(rc.report(self.root)["llm_metrics"], result["llm_metrics"])
        snapshot["totals"]["messages_missing_total_tokens"] = 1
        self.assertIsNone(rc.report(self.root, snapshot)["llm_metrics"]["total_tokens"])
        snapshot["totals"]["messages_missing_usage"] = 1
        self.assertIsNone(rc.report(self.root, snapshot)["llm_metrics"]["input_tokens"])

    def test_wrong_reference_bytes_fail_even_on_cache_reuse(self):
        reference = pcm_wav(frames=200)
        spec = self.spec(phase="chapter", reference=reference)
        self.generate(spec, reference)
        self.run_mock.reset_mock()
        with self.assertRaises(rc.ChapterError):
            rc.generate_request(self.root, spec, pcm_wav(frames=201))
        self.run_mock.assert_not_called()

    def test_request_journal_malformed_or_duplicate_completion_fails(self):
        spec = self.spec()
        self.generate(spec)
        record = rc.request_records(self.root)[0]
        rc.append_request(self.root, record)
        with self.assertRaises(rc.ChapterError):
            rc.request_records(self.root)
        (self.root / "requests.jsonl").write_text("{\n", encoding="utf-8")
        with self.assertRaises(rc.ChapterError):
            rc.request_records(self.root)

    def test_output_lock_prevents_parallel_writers(self):
        with rc.output_lock(self.root):
            with self.assertRaises(rc.ChapterError):
                with rc.output_lock(self.root):
                    self.fail("Concurrent lock should not succeed")
        with rc.output_lock(self.root):
            pass

    def test_cli_generation_error_not_hidden_by_metrics_error(self):
        with patch.object(rc, "load_plan", return_value={}), patch.object(
            rc, "voices", side_effect=subprocess.TimeoutExpired(rc.SYNTH_COMMAND, 250),
        ), patch.object(rc, "report", side_effect=OSError("metrics error")), patch.object(
            rc.sys, "stderr", new_callable=io.StringIO,
        ) as stderr:
            status = rc.main(["voices", "--output", str(self.root), "--manifest", str(self.manifest_path)])
        self.assertEqual(status, 1)
        self.assertIn("TimeoutExpired", stderr.getvalue())
        self.assertIn("Metrics update failed", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
