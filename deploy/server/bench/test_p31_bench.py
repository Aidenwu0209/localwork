"""Offline integrity tests for the P3.1 evidence pipeline."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


def _load_module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bench = _load_module("p31_openai_bench", "openai_bench.py")
summary = _load_module("p31_summary", "summarize_p31.py")
kfd = _load_module("p31_kfd_scope", "kfd_scope.py")


MANIFEST_SHA = "a" * 64
MANIFEST = {
    "run_id": "test-run",
    "runs": "3",
    "warmup_batches": "1",
    "brain_max_tokens": "256",
    "perceive_max_tokens": "96",
    "brain_port": "18001",
    "perceive_port": "18002",
    "llama_cpp_commit": "76f46ad29abc",
    "llama_bin_sha256": "b" * 64,
    "perceive_image_sha256": summary.PERCEIVE_IMAGE_SHA256,
    "brain_prompt_sha256": "c" * 64,
    "perceive_prompt_sha256": "d" * 64,
}


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _fake_gpu_mapping(
    *,
    kfd_root: Path,
    dri_root: Path,
    drm_root: Path,
    render_minor: int,
    gpu_id: int,
    unique_id: int,
) -> None:
    (dri_root / f"renderD{render_minor}").touch()
    _write(
        drm_root / f"renderD{render_minor}/device/unique_id",
        f"{unique_id:x}\n",
    )
    _write(
        kfd_root / f"topology/nodes/{render_minor}/properties",
        (
            f"drm_render_minor {render_minor}\n"
            f"unique_id {unique_id}\n"
        ),
    )
    _write(
        kfd_root / f"topology/nodes/{render_minor}/gpu_id",
        f"{gpu_id}\n",
    )


def test_kfd_inventory_ignores_host_processes_on_unassigned_gpus(
    tmp_path: Path,
) -> None:
    kfd_root = tmp_path / "kfd"
    dri_root = tmp_path / "dri"
    drm_root = tmp_path / "drm"
    proc_root = tmp_path / "proc"
    dri_root.mkdir()
    _fake_gpu_mapping(
        kfd_root=kfd_root,
        dri_root=dri_root,
        drm_root=drm_root,
        render_minor=130,
        gpu_id=60148,
        unique_id=6992697270274875713,
    )

    _write(kfd_root / "proc/111/vram_47780", "41685000192\n")
    _write(kfd_root / "proc/111/queues/0/gpuid", "47780\n")
    _write(proc_root / "111/comm", "other-container\n")

    _write(kfd_root / "proc/222/vram_60148", "1048576\n")
    _write(kfd_root / "proc/222/queues/0/gpuid", "60148\n")
    _write(proc_root / "222/comm", "llama-server\n")

    assert kfd.assigned_gpu_ids(
        kfd_root=kfd_root,
        dri_root=dri_root,
        drm_root=drm_root,
    ) == {
        60148
    }
    assert kfd.scoped_processes(
        kfd_root=kfd_root,
        dri_root=dri_root,
        drm_root=drm_root,
        proc_root=proc_root,
    ) == [(222, "llama-server", [60148])]


def test_kfd_inventory_fails_when_render_node_cannot_be_mapped(
    tmp_path: Path,
) -> None:
    dri_root = tmp_path / "dri"
    dri_root.mkdir()
    (dri_root / "renderD130").touch()
    with pytest.raises(RuntimeError, match="exactly one"):
        kfd.assigned_gpu_ids(
            kfd_root=tmp_path / "kfd",
            dri_root=dri_root,
            drm_root=tmp_path / "drm",
        )


def test_kfd_inventory_rejects_partial_multi_render_mapping(
    tmp_path: Path,
) -> None:
    kfd_root = tmp_path / "kfd"
    dri_root = tmp_path / "dri"
    drm_root = tmp_path / "drm"
    dri_root.mkdir()
    _fake_gpu_mapping(
        kfd_root=kfd_root,
        dri_root=dri_root,
        drm_root=drm_root,
        render_minor=130,
        gpu_id=60148,
        unique_id=6992697270274875713,
    )
    (dri_root / "renderD131").touch()
    with pytest.raises(RuntimeError, match="exactly one"):
        kfd.assigned_gpu_ids(
            kfd_root=kfd_root,
            dri_root=dri_root,
            drm_root=drm_root,
        )


def test_kfd_inventory_fails_closed_on_unreadable_queue_gpu_id(
    tmp_path: Path,
) -> None:
    proc_dir = tmp_path / "kfd/proc/333"
    (proc_dir / "queues/0").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="KFD queue"):
        kfd.process_gpu_ids(proc_dir)


def test_kfd_inventory_fails_closed_on_existing_unscoped_process(
    tmp_path: Path,
) -> None:
    kfd_root = tmp_path / "kfd"
    dri_root = tmp_path / "dri"
    drm_root = tmp_path / "drm"
    dri_root.mkdir()
    _fake_gpu_mapping(
        kfd_root=kfd_root,
        dri_root=dri_root,
        drm_root=drm_root,
        render_minor=130,
        gpu_id=60148,
        unique_id=6992697270274875713,
    )
    (kfd_root / "proc/444/queues").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="no auditable GPU"):
        kfd.scoped_processes(
            kfd_root=kfd_root,
            dri_root=dri_root,
            drm_root=drm_root,
            proc_root=tmp_path / "proc",
        )


def test_kfd_inventory_ignores_zero_usage_contexts_without_queues(
    tmp_path: Path,
) -> None:
    kfd_root = tmp_path / "kfd"
    dri_root = tmp_path / "dri"
    drm_root = tmp_path / "drm"
    dri_root.mkdir()
    _fake_gpu_mapping(
        kfd_root=kfd_root,
        dri_root=dri_root,
        drm_root=drm_root,
        render_minor=130,
        gpu_id=60148,
        unique_id=6992697270274875713,
    )
    _write(kfd_root / "proc/555/vram_60148", "0\n")
    _write(kfd_root / "proc/555/sdma_60148", "0\n")
    (kfd_root / "proc/555/queues").mkdir()
    assert (
        kfd.scoped_processes(
            kfd_root=kfd_root,
            dri_root=dri_root,
            drm_root=drm_root,
            proc_root=tmp_path / "proc",
        )
        == []
    )


def test_kfd_inventory_fails_closed_when_process_root_is_missing(
    tmp_path: Path,
) -> None:
    kfd_root = tmp_path / "kfd"
    dri_root = tmp_path / "dri"
    drm_root = tmp_path / "drm"
    dri_root.mkdir()
    _fake_gpu_mapping(
        kfd_root=kfd_root,
        dri_root=dri_root,
        drm_root=drm_root,
        render_minor=130,
        gpu_id=60148,
        unique_id=6992697270274875713,
    )
    with pytest.raises(RuntimeError, match="process inventory"):
        kfd.scoped_processes(
            kfd_root=kfd_root,
            dri_root=dri_root,
            drm_root=drm_root,
            proc_root=tmp_path / "proc",
        )


def test_vram_telemetry_uses_only_mounted_render_nodes(
    tmp_path: Path,
) -> None:
    dri_root = tmp_path / "dri"
    drm_root = tmp_path / "drm"
    dri_root.mkdir()
    (dri_root / "renderD131").touch()
    _write(
        drm_root / "renderD130/device/mem_info_vram_total",
        "51522830336\n",
    )
    _write(
        drm_root / "renderD130/device/mem_info_vram_used",
        "1048576\n",
    )
    _write(
        drm_root / "renderD131/device/mem_info_vram_total",
        "51522830336\n",
    )
    _write(
        drm_root / "renderD131/device/mem_info_vram_used",
        "46000000000\n",
    )
    assert kfd.scoped_vram_bytes(
        drm_root=drm_root,
        dri_root=dri_root,
    ) == (46000000000, 51522830336)


def test_vram_telemetry_fails_closed_when_mounted_node_is_unreadable(
    tmp_path: Path,
) -> None:
    dri_root = tmp_path / "dri"
    dri_root.mkdir()
    (dri_root / "renderD131").touch()
    with pytest.raises(RuntimeError, match="scoped VRAM"):
        kfd.scoped_vram_bytes(
            drm_root=tmp_path / "drm",
            dri_root=dri_root,
        )


def _sample(
    *,
    request_id: str = "trial-1-request-1",
    draft_n: int = 0,
    accepted: int = 0,
    require_image: bool = False,
) -> dict:
    exact_content = " ".join(str(value) for value in range(1, 81))
    return {
        "request_id": request_id,
        "http_status": 200,
        "wall_ms": 100.0,
        "cache_n": 0,
        "prompt_n": 10,
        "predicted_n": 80,
        "prompt_per_second": 100.0,
        "predicted_per_second": 50.0,
        "draft_n": draft_n,
        "draft_n_accepted": accepted,
        "numeric_sequence_prefix": 0 if require_image else 80,
        "numeric_sequence_complete": True,
        "numeric_sequence_exact": True,
        "content_matches_required": True,
        "content_sha256": hashlib.sha256(
            ("parse.py" if require_image else exact_content).encode()
        ).hexdigest(),
        "content_length": len("parse.py") if require_image else len(exact_content),
        "content_preview": "parse.py" if require_image else exact_content,
        "timings": {
            "cache_n": 0,
            "prompt_per_second": 100.0,
            "predicted_per_second": 50.0,
        },
    }


def _record(
    path: Path,
    *,
    require_draft: bool,
    require_image: bool,
    draft_n: int,
    accepted: int,
) -> dict:
    batches = []
    for trial in range(1, 4):
        sample = _sample(
            request_id=f"trial-{trial}-request-1",
            draft_n=draft_n,
            accepted=accepted,
            require_image=require_image,
        )
        batches.append(
            {
                "trial": trial,
                "batch_wall_ms": 100.0,
                "aggregate_output_tps_end_to_end": 800.0,
                "generated_tokens": 80,
                "prompt_tokens": 10,
                "samples": [sample],
            }
        )
    return {
        "schema_version": 2,
        "label": path.stem,
        "evidence": {
            "run_id": MANIFEST["run_id"],
            "manifest_sha256": MANIFEST_SHA,
            "llama_commit": MANIFEST["llama_cpp_commit"],
            "llama_bin_sha256": MANIFEST["llama_bin_sha256"],
        },
        "method": {
            "model": "perceive-bench" if require_image else "brain-bench",
            "url": (
                "http://127.0.0.1:"
                + MANIFEST["perceive_port" if require_image else "brain_port"]
                + "/v1/chat/completions"
            ),
            "concurrency": 1,
            "runs": 3,
            "warmup_batches": 1,
            "max_tokens": 96 if require_image else 256,
            "enable_thinking": False,
            "temperature": 0,
            "cache_prompt": False,
            "image_sha256": (
                summary.PERCEIVE_IMAGE_SHA256 if require_image else None
            ),
            "required_content_regex": (
                r"(?i)\bparse\.py\b" if require_image else None
            ),
            "required_numeric_prefix": 0 if require_image else 80,
            "require_draft": require_draft,
            "forbid_draft": not require_draft,
            "synthetic_prompt_sha256": (
                MANIFEST["perceive_prompt_sha256"]
                if require_image
                else MANIFEST["brain_prompt_sha256"]
            ),
        },
        "summary": bench._summarize(batches),
        "batches": batches,
    }


def test_summary_medians_and_draft_acceptance() -> None:
    batches = [
        {
            "batch_wall_ms": wall,
            "aggregate_output_tps_end_to_end": rate,
            "samples": [_sample(draft_n=4, accepted=3)],
        }
        for wall, rate in ((100.0, 40.0), (120.0, 50.0), (140.0, 60.0))
    ]
    result = bench._summarize(batches)
    assert result["trial_count"] == 3
    assert result["request_wall_p95_ms"] == 100.0
    assert result["aggregate_output_tps_end_to_end_median"] == 50.0
    assert result["draft_n"] == 12
    assert result["draft_n_accepted"] == 9
    assert result["draft_acceptance_ratio"] == 0.75


@pytest.mark.parametrize(
    "content",
    [
        " ".join(str(value) for value in range(1, 82)),
        " ".join(str(value) for value in range(1, 81)) + " 80",
        " ".join(str(value) for value in range(1, 81)) + " extra 999",
        " ".join(str(value) for value in range(1, 81)) + " arbitrary prose",
    ],
)
def test_numeric_quality_requires_exact_output(content: str) -> None:
    assert bench._numeric_sequence_prefix(content) >= 80
    assert not bench._numeric_sequence_exact(content, 80)


def test_evidence_validator_rejects_fake_mtp_label(tmp_path: Path) -> None:
    path = tmp_path / "brain-Q6_K-mtp-on-c1.json"
    record = _record(
        path,
        require_draft=True,
        require_image=False,
        draft_n=0,
        accepted=0,
    )
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="generated no draft tokens"):
        summary._validate_record(
            path,
            record,
            manifest=MANIFEST,
            manifest_sha256=MANIFEST_SHA,
            concurrency=1,
            require_draft=True,
            require_image=False,
            expected_model="brain-bench",
            max_tokens_field="brain_max_tokens",
            prompt_sha_field="brain_prompt_sha256",
        )


def test_mtp_zero_acceptance_is_preserved_as_negative_result(
    tmp_path: Path,
) -> None:
    path = tmp_path / "brain-Q6_K-mtp-on-c1.json"
    record = _record(
        path,
        require_draft=True,
        require_image=False,
        draft_n=4,
        accepted=0,
    )
    summary._validate_record(
        path,
        record,
        manifest=MANIFEST,
        manifest_sha256=MANIFEST_SHA,
        concurrency=1,
        require_draft=True,
        require_image=False,
        expected_model="brain-bench",
        max_tokens_field="brain_max_tokens",
        prompt_sha_field="brain_prompt_sha256",
    )


def test_evidence_validator_requires_perceive_image(tmp_path: Path) -> None:
    path = tmp_path / "perceive-Q8_0-np1-c1.json"
    record = _record(
        path,
        require_draft=False,
        require_image=True,
        draft_n=0,
        accepted=0,
    )
    record["method"]["image_sha256"] = None
    with pytest.raises(ValueError, match="visual-grounding contract"):
        summary._validate_record(
            path,
            record,
            manifest=MANIFEST,
            manifest_sha256=MANIFEST_SHA,
            concurrency=1,
            require_draft=False,
            require_image=True,
            expected_model="perceive-bench",
            max_tokens_field="perceive_max_tokens",
            prompt_sha_field="perceive_prompt_sha256",
        )


def test_perceive_filename_requires_paired_np_and_concurrency() -> None:
    assert summary.PERCEIVE_RE.fullmatch("perceive-Q8_0-np2-c2.json")
    assert not summary.PERCEIVE_RE.fullmatch("perceive-Q8_0-np2-c4.json")


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("all", (18, 3)),
        ("brain", (18, 0)),
        ("perceive", (0, 3)),
    ],
)
def test_success_modes_require_their_complete_matrix(
    mode: str, expected: tuple[int, int]
) -> None:
    assert summary._expected_matrix_counts(mode) == expected


def test_rendered_summary_uses_portable_evidence_path_and_no_optional_claim() -> None:
    rendered = summary._render(
        Path("/tmp/dejaview-p31/run-123"),
        [],
        [],
    )
    assert "run-123/" in rendered
    assert "/tmp/dejaview-p31" not in rendered
    assert "Prometheus snapshots remain" not in rendered


def test_validator_recomputes_summary_from_raw_samples(tmp_path: Path) -> None:
    path = tmp_path / "brain-Q6_K-mtp-off-c1.json"
    record = _record(
        path,
        require_draft=False,
        require_image=False,
        draft_n=0,
        accepted=0,
    )
    record["summary"]["prompt_tps_median"] = 9999.0
    with pytest.raises(ValueError, match="does not match raw prompt_tps_median"):
        summary._validate_record(
            path,
            record,
            manifest=MANIFEST,
            manifest_sha256=MANIFEST_SHA,
            concurrency=1,
            require_draft=False,
            require_image=False,
            expected_model="brain-bench",
            max_tokens_field="brain_max_tokens",
            prompt_sha_field="brain_prompt_sha256",
        )


@pytest.mark.parametrize(
    "field",
    [
        "batch_wall_p50_ms",
        "batch_wall_p95_ms",
        "completion_tokens_median",
        "numeric_sequence_prefix_median",
        "numeric_sequence_complete_rate",
        "required_content_match_rate",
        "draft_acceptance_ratio",
    ],
)
def test_validator_recomputes_every_reported_summary_field(
    tmp_path: Path, field: str
) -> None:
    path = tmp_path / "brain-Q6_K-mtp-on-c1.json"
    record = _record(
        path,
        require_draft=True,
        require_image=False,
        draft_n=4,
        accepted=3,
    )
    record["summary"][field] = 999.0
    with pytest.raises(ValueError, match=field):
        summary._validate_record(
            path,
            record,
            manifest=MANIFEST,
            manifest_sha256=MANIFEST_SHA,
            concurrency=1,
            require_draft=True,
            require_image=False,
            expected_model="brain-bench",
            max_tokens_field="brain_max_tokens",
            prompt_sha_field="brain_prompt_sha256",
        )


def test_validator_rejects_impossible_draft_acceptance(tmp_path: Path) -> None:
    path = tmp_path / "brain-Q6_K-mtp-on-c1.json"
    record = _record(
        path,
        require_draft=True,
        require_image=False,
        draft_n=4,
        accepted=5,
    )
    with pytest.raises(ValueError, match="invalid draft token counts"):
        summary._validate_record(
            path,
            record,
            manifest=MANIFEST,
            manifest_sha256=MANIFEST_SHA,
            concurrency=1,
            require_draft=True,
            require_image=False,
            expected_model="brain-bench",
            max_tokens_field="brain_max_tokens",
            prompt_sha_field="brain_prompt_sha256",
        )


def test_validator_rejects_prompt_cache_contamination(tmp_path: Path) -> None:
    path = tmp_path / "brain-Q6_K-mtp-off-c1.json"
    record = _record(
        path,
        require_draft=False,
        require_image=False,
        draft_n=0,
        accepted=0,
    )
    record["batches"][0]["samples"][0]["cache_n"] = 10
    record["batches"][0]["samples"][0]["timings"]["cache_n"] = 10
    with pytest.raises(ValueError, match="prompt cache contaminated"):
        summary._validate_record(
            path,
            record,
            manifest=MANIFEST,
            manifest_sha256=MANIFEST_SHA,
            concurrency=1,
            require_draft=False,
            require_image=False,
            expected_model="brain-bench",
            max_tokens_field="brain_max_tokens",
            prompt_sha_field="brain_prompt_sha256",
        )
