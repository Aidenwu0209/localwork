from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from scripts.submission_check import _office_check, _ooxml_raw_text, check_submission


class SubmissionCheckTest(unittest.TestCase):
    def make_tree(self, *, duration: float = 180.0) -> tuple[Path, Path]:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name).resolve()
        for item in (
            "README.md", "README.zh.md", "LICENSE", "NOTICE", "docs/licenses.md",
            "docs/model-manifest.md", "docs/benchmarks.md", "docs/verification-log.md",
            "docs/benchmark-evidence/p31/p31-w7900d-20260728T075653Z/SHA256SUMS",
            "docs/benchmark-evidence/p31/p31-w7900d-20260728T075653Z/p31-summary.md",
        ):
            path = root / item
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("English release evidence", encoding="utf-8")
        (root / "docs/verification-log.md").write_text(
            "Human-only boundary: this acceptance does not claim official-repository fork/English PR.",
            encoding="utf-8",
        )
        video = root / "docs/assets/demo/submission.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"synthetic video")
        original = root / "docs/assets/demo/dejaview-p34-six-act-20260802.mp4"
        original.write_bytes(b"accepted synthetic evidence")
        self.original_sha = hashlib.sha256(original.read_bytes()).hexdigest()
        caption = video.with_suffix(".srt")
        caption.write_text(
            "1\n00:00:00,000 --> 00:03:00,000\nACT 1 ACT 2 ACT 3 ACT 4 ACT 5 ACT 6 Radeon ROCm LINK DOWN LOCAL READY Local Metal fallback\n",
            encoding="utf-8",
        )
        self.write_office(root / "docs/submission/DejaView-Project-Specification.docx", "DejaView Track 2 ROCm privacy limitations")
        self.write_office(root / "docs/submission/DejaView-Track2-Presentation.pptx", "DejaView Track 2 ROCm Privacy Radeon Evidence", pptx=True)
        digest = hashlib.sha256(video.read_bytes()).hexdigest()
        manifest = {
            "artifact": "docs/assets/demo/dejaview-p34-six-act-20260802.mp4", "recorded_at": "2026-08-02",
            "duration_seconds": 157.2, "resolution": "1920x1080", "video_codec": "h264", "audio_codec": "aac",
            "size_bytes": original.stat().st_size, "sha256": self.original_sha,
            "submission_artifact": {"artifact": "docs/assets/demo/submission.mp4", "language": "English-primary narration with complete English captions", "duration_seconds": duration, "resolution": "1920x1080", "video_codec": "h264", "audio_codec": "aac", "size_bytes": video.stat().st_size, "sha256": digest, "caption_source": "docs/assets/demo/submission.srt", "caption_sha256": hashlib.sha256(caption.read_bytes()).hexdigest()},
            "capture_method": "isolated synthetic fixture", "fault_injection": "verified LINK DOWN",
            "timeline": [{"start": f"0{(i-1)//2}:{'00' if (i-1)%2 == 0 else '30'}", "end": f"0{i//2}:{'00' if i%2 == 0 else '30'}", "act": i, "evidence": "Radeon disconnect LINK DOWN Local Metal fallback" if i == 6 else "English evidence"} for i in range(1, 7)],
        }
        (root / "docs/assets/demo/p34-video-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (root / "README.md").write_text(
            "\n".join(f"[{p}]({p})" for p in ("docs/submission/PROJECT_SPECIFICATION.md", "docs/submission/DejaView-Project-Specification.docx", "docs/submission/DejaView-Track2-Presentation.pptx", "docs/assets/demo/p34-video-manifest.json", "docs/assets/demo/submission.mp4", "docs/assets/demo/submission.srt", "docs/benchmarks.md", "docs/benchmark-evidence/p31/p31-w7900d-20260728T075653Z/", "docs/licenses.md"))
            + "\nHuman-only step: fork the official competition repository and open the English pull request. This checkout does not claim completion.\n",
            encoding="utf-8",
        )
        (root / "docs/submission/PROJECT_SPECIFICATION.md").write_text("DejaView Track2 ROCm privacy limitations", encoding="utf-8")
        probe = root / "probe.py"
        probe.write_text(f"#!/usr/bin/env python3\nimport json; print(json.dumps({{'format': {{'duration': '{duration}', 'format_name': 'mov,mp4,m4a,3gp,3g2,mj2'}}, 'streams': [{{'codec_type':'video','codec_name':'h264','width':1920,'height':1080}}, {{'codec_type':'audio','codec_name':'aac'}}]}}))\n", encoding="utf-8")
        probe.chmod(probe.stat().st_mode | stat.S_IXUSR)
        subprocess.run(["git", "init", "-q", root], check=True)
        subprocess.run(["git", "-C", root, "add", "-A"], check=True)
        return root, probe

    def tearDown(self) -> None:
        if hasattr(self, "temp"):
            self.temp.cleanup()

    @staticmethod
    def write_office(
        path: Path,
        text: str,
        *,
        pptx: bool = False,
        notes_target: str = "/ppt/notesSlides/notesSlide1.xml",
        notes_type: str = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide",
        slide_type: str = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide",
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("_rels/.rels", "<Relationships/>")
            if pptx:
                archive.writestr("ppt/presentation.xml", f"<p>{text}</p>")
                archive.writestr(
                    "ppt/_rels/presentation.xml.rels",
                    f'<Relationships><Relationship Type="{slide_type}" Target="slides/slide1.xml"/></Relationships>',
                )
                archive.writestr("ppt/slides/slide1.xml", f"<p>{text}</p>")
                archive.writestr(
                    "ppt/slides/_rels/slide1.xml.rels",
                    f'<Relationships><Relationship Type="{notes_type}" Target="{notes_target}"/></Relationships>',
                )
                archive.writestr("ppt/notesSlides/notesSlide1.xml", "<p>[SOURCES] evidence</p>")
            else:
                archive.writestr("word/document.xml", f"<w>{text}</w>")
                archive.writestr("word/styles.xml", "<styles/>")
                archive.writestr("docProps/core.xml", "<core/>")

    def names(self, root: Path, probe: Path) -> set[str]:
        real_run = subprocess.run

        def portable_run(args: list[str], *positional: object, **keywords: object) -> subprocess.CompletedProcess[object]:
            if args and os.fspath(args[0]) == os.fspath(probe):
                args = [sys.executable, os.fspath(probe), *args[1:]]
            return real_run(args, *positional, **keywords)

        with (
            mock.patch("scripts.submission_check.ACCEPTED_ORIGINAL_SHA", self.original_sha),
            mock.patch("scripts.submission_check.subprocess.run", side_effect=portable_run),
        ):
            return {result.name for result in check_submission(root, ffprobe=str(probe)) if not result.ok}

    def test_synthetic_package_passes_with_fake_ffprobe(self) -> None:
        root, probe = self.make_tree()
        self.assertEqual(self.names(root, probe), set())

    def test_duration_below_180_fails_once(self) -> None:
        root, probe = self.make_tree(duration=179.9)
        failures = self.names(root, probe)
        self.assertIn("submission-video-duration", failures)
        self.assertEqual(sum(name == "submission-video-duration" for name in failures), 1)

    def test_hash_mismatch_and_missing_aac_fail(self) -> None:
        root, probe = self.make_tree()
        manifest_path = root / "docs/assets/demo/p34-video-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["submission_artifact"]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        probe.write_text("#!/usr/bin/env python3\nimport json; print(json.dumps({'format': {'duration': '180.0'}, 'streams': [{'codec_type':'video','codec_name':'h264','width':1920,'height':1080}]}))\n", encoding="utf-8")
        self.assertTrue({"submission-video-hash", "submission-video-streams"} <= self.names(root, probe))

    def test_srt_overlap_gap_and_act6_evidence_fail(self) -> None:
        root, probe = self.make_tree()
        srt = root / "docs/assets/demo/submission.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:10,000\nACT 1\n\n2\n00:00:09,000 --> 00:03:00,000\nACT 2 ACT 3 ACT 4 ACT 5\n", encoding="utf-8")
        failures = self.names(root, probe)
        self.assertIn("submission-captions-timing", failures)
        self.assertIn("submission-captions-content", failures)

    def test_srt_gap_fails(self) -> None:
        root, probe = self.make_tree()
        srt = root / "docs/assets/demo/submission.srt"
        srt.write_text(
            "1\n00:00:00,000 --> 00:00:10,000\nACT 1 ACT 2 ACT 3\n\n"
            "2\n00:00:11,000 --> 00:03:00,000\nACT 4 ACT 5 ACT 6 Radeon ROCm LINK DOWN LOCAL READY Local Metal fallback\n",
            encoding="utf-8",
        )
        self.assertIn("submission-captions-timing", self.names(root, probe))

    def test_srt_act_numbers_require_exact_boundaries_and_order(self) -> None:
        root, probe = self.make_tree()
        srt = root / "docs/assets/demo/submission.srt"
        srt.write_text(
            "1\n00:00:00,000 --> 00:03:00,000\n"
            "ACT 10 ACT 20 ACT 30 ACT 40 ACT 50 ACT 60 Radeon ROCm LINK DOWN LOCAL READY Local Metal fallback\n",
            encoding="utf-8",
        )
        manifest_path = root / "docs/assets/demo/p34-video-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["submission_artifact"]["caption_sha256"] = hashlib.sha256(srt.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertIn("submission-captions-content", self.names(root, probe))

    def test_non_mp4_container_fails_even_with_mp4_suffix(self) -> None:
        root, probe = self.make_tree()
        probe.write_text(
            "#!/usr/bin/env python3\nimport json; print(json.dumps({'format': {'duration': '180.0', 'format_name': 'matroska,webm'}, 'streams': [{'codec_type':'video','codec_name':'h264','width':1920,'height':1080}, {'codec_type':'audio','codec_name':'aac'}]}))\n",
            encoding="utf-8",
        )
        self.assertIn("submission-video-container", self.names(root, probe))

    def test_missing_act6_timeline_and_chinese_narration_fail(self) -> None:
        root, probe = self.make_tree()
        manifest_path = root / "docs/assets/demo/p34-video-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["submission_artifact"]["language"] = "Chinese narration with English captions"
        manifest["timeline"] = [row for row in manifest["timeline"] if row["act"] != 6]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        failures = self.names(root, probe)
        self.assertTrue({"submission-language", "submission-timeline"} <= failures)

    def test_dynamic_manifest_traversal_is_rejected(self) -> None:
        root, probe = self.make_tree()
        manifest_path = root / "docs/assets/demo/p34-video-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["submission_artifact"]["artifact"] = "docs/assets/demo/../../NOTICE"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertIn("submission-artifact-paths", self.names(root, probe))

    def test_invalid_dynamic_suffix_is_rejected(self) -> None:
        root, probe = self.make_tree()
        source = root / "docs/assets/demo/submission.mp4"
        invalid = source.with_suffix(".bin")
        invalid.write_bytes(source.read_bytes())
        manifest_path = root / "docs/assets/demo/p34-video-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["submission_artifact"].update(
            artifact="docs/assets/demo/submission.bin",
            sha256=hashlib.sha256(invalid.read_bytes()).hexdigest(),
            size_bytes=invalid.stat().st_size,
        )
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertIn("submission-artifact-paths", self.names(root, probe))

    def test_rejected_caption_traversal_is_never_read_by_privacy_scan(self) -> None:
        root, probe = self.make_tree()
        outside = root.parent / "outside.srt"
        outside.write_text("host 8.8.8.8", encoding="utf-8")
        manifest_path = root / "docs/assets/demo/p34-video-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["submission_artifact"]["caption_source"] = "../outside.srt"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        try:
            failures = self.names(root, probe)
            self.assertIn("submission-artifact-paths", failures)
            self.assertNotIn("submission-privacy-content", failures)
        finally:
            outside.unlink(missing_ok=True)

    def test_manifest_type_matrix_and_reversed_acts_fail(self) -> None:
        root, probe = self.make_tree()
        manifest_path = root / "docs/assets/demo/p34-video-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(capture_method=[], fault_injection=123, duration_seconds=299, resolution={})
        for row, act in zip(manifest["timeline"], range(6, 0, -1)):
            row["act"] = act
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        failures = self.names(root, probe)
        self.assertTrue({"submission-manifest-schema", "accepted-demo-immutability", "submission-timeline"} <= failures)

    def test_timeline_rejects_bool_duplicate_and_invalid_calendar_date(self) -> None:
        root, probe = self.make_tree()
        manifest_path = root / "docs/assets/demo/p34-video-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["recorded_at"] = "2026-02-31"
        manifest["timeline"][0]["act"] = True
        manifest["timeline"].insert(1, dict(manifest["timeline"][0]))
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        failures = self.names(root, probe)
        self.assertTrue({"submission-manifest-schema", "submission-timeline"} <= failures)

    def test_tracked_env_is_flagged_without_reading_its_contents(self) -> None:
        root, probe = self.make_tree()
        (root / ".env").write_text("do not inspect", encoding="utf-8")
        subprocess.run(["git", "-C", root, "add", ".env"], check=True)
        self.assertIn("submission-privacy-tracked-files", self.names(root, probe))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires FIFO support")
    def test_tracked_env_fifo_is_never_opened(self) -> None:
        root, probe = self.make_tree()
        dotenv = root / ".env"
        dotenv.write_text("placeholder", encoding="utf-8")
        subprocess.run(["git", "-C", root, "add", ".env"], check=True)
        dotenv.unlink()
        os.mkfifo(dotenv)
        try:
            self.assertIn("submission-privacy-tracked-files", self.names(root, probe))
        finally:
            dotenv.unlink(missing_ok=True)

    def test_git_failure_is_an_explicit_privacy_failure(self) -> None:
        root, probe = self.make_tree()
        git_dir = root / ".git"
        disabled = root / ".git-disabled"
        git_dir.rename(disabled)
        try:
            self.assertIn("submission-privacy-tracked-files", self.names(root, probe))
        finally:
            disabled.rename(git_dir)

    def test_secret_filenames_and_boolean_feature_flags(self) -> None:
        root, probe = self.make_tree()
        (root / ".netrc").write_text("machine example", encoding="utf-8")
        subprocess.run(["git", "-C", root, "add", ".netrc"], check=True)
        (root / "docs/benchmarks.md").write_text("REQUIRE_API_TOKEN=true", encoding="utf-8")
        failures = self.names(root, probe)
        self.assertIn("submission-privacy-tracked-files", failures)
        self.assertNotIn("submission-privacy-content", failures)

    def test_key_filename_with_public_substring_is_still_secret(self) -> None:
        root, probe = self.make_tree()
        key = root / "private-public-backup.key"
        key.write_text("placeholder", encoding="utf-8")
        subprocess.run(["git", "-C", root, "add", key.name], check=True)
        self.assertIn("submission-privacy-tracked-files", self.names(root, probe))

    def test_public_coordinate_and_missing_dynamic_readme_link_fail(self) -> None:
        root, probe = self.make_tree()
        (root / "docs/benchmarks.md").write_text("host 8.8.8.8", encoding="utf-8")
        (root / "README.md").write_text("[spec](docs/submission/PROJECT_SPECIFICATION.md)", encoding="utf-8")
        failures = self.names(root, probe)
        self.assertTrue({"submission-privacy-content", "submission-readme-links"} <= failures)

    @unittest.skipUnless(os.name == "posix", "requires POSIX symlink support")
    def test_rejects_symlink_and_ooxml_traversal(self) -> None:
        root, probe = self.make_tree()
        (root / "NOTICE").unlink()
        (root / "NOTICE").symlink_to("LICENSE")
        with zipfile.ZipFile(root / "docs/submission/DejaView-Project-Specification.docx", "a") as archive:
            archive.writestr("../escape.xml", "bad")
        self.assertTrue({"required-release-files", "submission-docx"} <= self.names(root, probe))

    def test_ooxml_missing_required_member_fails(self) -> None:
        root, probe = self.make_tree()
        docx = root / "docs/submission/DejaView-Project-Specification.docx"
        with zipfile.ZipFile(docx, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("_rels/.rels", "<Relationships/>")
            archive.writestr("word/document.xml", "<w>DejaView Track 2 ROCm privacy limitations</w>")
        self.assertIn("submission-docx", self.names(root, probe))

    def test_ooxml_duplicate_member_fails_without_raw_reread(self) -> None:
        root, probe = self.make_tree()
        docx = root / "docs/submission/DejaView-Project-Specification.docx"
        with zipfile.ZipFile(docx, "a") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
        self.assertEqual(_ooxml_raw_text(docx), "")
        self.assertIn("submission-docx", self.names(root, probe))

    def test_ooxml_noncanonical_member_names_fail(self) -> None:
        root, probe = self.make_tree()
        docx = root / "docs/submission/DejaView-Project-Specification.docx"
        with zipfile.ZipFile(docx, "a") as archive:
            archive.writestr("word/./extra.xml", "<x/>")
        self.assertIn("submission-docx", self.names(root, probe))

    def test_ooxml_unsupported_compression_is_a_controlled_failure(self) -> None:
        root, _probe = self.make_tree()
        docx = root / "docs/submission/DejaView-Project-Specification.docx"
        data = bytearray(docx.read_bytes())
        local = data.find(b"PK\x03\x04")
        central = data.find(b"PK\x01\x02")
        self.assertGreaterEqual(local, 0)
        self.assertGreaterEqual(central, 0)
        data[local + 8 : local + 10] = (99).to_bytes(2, "little")
        data[central + 10 : central + 12] = (99).to_bytes(2, "little")
        docx.write_bytes(data)
        ok, detail, _text = _office_check(docx, "docx")
        self.assertFalse(ok)
        self.assertIn("cannot inspect", detail)

    def test_ooxml_required_member_disguised_as_directory_fails(self) -> None:
        root, probe = self.make_tree()
        docx = root / "docs/submission/DejaView-Project-Specification.docx"
        with zipfile.ZipFile(docx, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("_rels/.rels", "<Relationships/>")
            archive.writestr("word/document.xml", "<w>DejaView Track 2 ROCm privacy limitations</w>")
            directory = zipfile.ZipInfo("word/styles.xml")
            directory.external_attr = (stat.S_IFDIR | 0o755) << 16
            archive.writestr(directory, b"")
            archive.writestr("docProps/core.xml", "<core/>")
        self.assertIn("submission-docx", self.names(root, probe))

    def test_pptx_wrong_notes_mapping_fails_without_duplicate_member(self) -> None:
        root, probe = self.make_tree()
        pptx = root / "docs/submission/DejaView-Track2-Presentation.pptx"
        self.write_office(
            pptx,
            "DejaView Track 2 ROCm Privacy Radeon Evidence",
            pptx=True,
            notes_target="/ppt/notesSlides/notesSlide2.xml",
        )
        self.assertIn("submission-pptx", self.names(root, probe))

    def test_pptx_relationship_types_are_required(self) -> None:
        root, probe = self.make_tree()
        pptx = root / "docs/submission/DejaView-Track2-Presentation.pptx"
        self.write_office(
            pptx,
            "DejaView Track 2 ROCm Privacy Radeon Evidence",
            pptx=True,
            notes_type="",
        )
        self.assertIn("submission-pptx", self.names(root, probe))

    @unittest.skipUnless(os.name == "posix", "requires POSIX symlink support")
    def test_symlink_repository_root_fails(self) -> None:
        root, probe = self.make_tree()
        link = root.parent / "linked-root"
        link.symlink_to(root, target_is_directory=True)
        try:
            failures = self.names(link, probe)
            self.assertIn("required-release-files", failures)
        finally:
            link.unlink(missing_ok=True)

    @unittest.skipUnless(os.name == "posix", "requires POSIX symlink support")
    def test_symlink_repository_ancestor_fails(self) -> None:
        root, probe = self.make_tree()
        parent_link = root.parent / f"{root.name}-parent-link"
        parent_link.symlink_to(root.parent, target_is_directory=True)
        try:
            failures = self.names(parent_link / root.name, probe)
            self.assertIn("required-release-files", failures)
        finally:
            parent_link.unlink(missing_ok=True)

    def test_missing_human_boundary_fails_on_regular_root(self) -> None:
        root, probe = self.make_tree()
        (root / "docs/verification-log.md").write_text("English evidence", encoding="utf-8")
        self.assertIn("submission-human-boundaries", self.names(root, probe))

    def test_readme_instruction_hidden_in_comment_or_code_is_rejected(self) -> None:
        root, probe = self.make_tree()
        readme = root / "README.md"
        links = "\n".join(
            f"[{path}]({path})"
            for path in (
                "docs/submission/PROJECT_SPECIFICATION.md",
                "docs/submission/DejaView-Project-Specification.docx",
                "docs/submission/DejaView-Track2-Presentation.pptx",
                "docs/assets/demo/p34-video-manifest.json",
                "docs/assets/demo/submission.mp4",
                "docs/assets/demo/submission.srt",
                "docs/benchmarks.md",
                "docs/benchmark-evidence/p31/p31-w7900d-20260728T075653Z/",
                "docs/licenses.md",
            )
        )
        readme.write_text(
            links
            + "\n<!-- fork the official competition repository and open the English pull request; does not claim -->\n"
            + "```text\nfork the official competition repository and open the English pull request; does not claim\n```\n",
            encoding="utf-8",
        )
        self.assertIn("submission-readme-links", self.names(root, probe))

    def test_readme_links_hidden_in_long_fence_are_rejected(self) -> None:
        root, probe = self.make_tree()
        readme = root / "README.md"
        hidden_links = readme.read_text(encoding="utf-8").split("Human-only step:", 1)[0]
        readme.write_text(
            "````markdown\n"
            + hidden_links
            + "````\nHuman-only step: fork the official competition repository and open the English pull request. This checkout does not claim completion.\n",
            encoding="utf-8",
        )
        self.assertIn("submission-readme-links", self.names(root, probe))

    def test_ooxml_entity_encoded_credential_is_rejected(self) -> None:
        root, probe = self.make_tree()
        self.write_office(
            root / "docs/submission/DejaView-Project-Specification.docx",
            "DejaView Track 2 ROCm privacy limitations &#115;&#107;&#45;" + "A" * 24,
        )
        self.assertIn("submission-privacy-content", self.names(root, probe))


if __name__ == "__main__":
    unittest.main()
