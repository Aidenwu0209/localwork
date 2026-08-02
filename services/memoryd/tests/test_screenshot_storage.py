from __future__ import annotations

import io
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image

from memoryd.storage import TimelineStore, UnsafeScreenshotPath


TS = "2026-08-03T12:34:56.123456+00:00"


def _png_bytes(color: tuple[int, int, int] = (12, 34, 56)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.parametrize(
    "device_id",
    (
        "../../../../../../tmp/escape",
        "device/child",
        r"device\child",
        "device\nchild",
        "d" * 129,
    ),
)
def test_storage_rejects_unsafe_device_id_before_creating_screenshot_tree(
    tmp_path: Path, device_id: str
) -> None:
    data_root = tmp_path / "data"
    store = TimelineStore("postgresql://synthetic", data_root)

    with pytest.raises(ValueError, match="device_id"):
        store.write_screenshot(
            device_id=device_id,
            ts=TS,
            image_bytes=_png_bytes(),
        )

    assert not data_root.exists()


def test_storage_atomically_publishes_webp_inside_canonical_screenshot_root(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    store = TimelineStore("postgresql://synthetic", data_root)

    path = store.write_screenshot(
        device_id="synthetic-device",
        ts=TS,
        image_bytes=_png_bytes(),
    )

    assert path is not None
    assert path.resolve().is_relative_to((data_root / "screenshots").resolve())
    assert path.suffix == ".webp"
    assert list(path.parent.glob("*.tmp")) == []
    with Image.open(path) as image:
        assert image.format == "WEBP"
        assert image.size == (2, 2)


def test_same_device_and_second_publish_distinct_evidence_files(tmp_path: Path) -> None:
    store = TimelineStore("postgresql://synthetic", tmp_path / "data")

    first = store.write_screenshot(
        device_id="synthetic-device", ts=TS, image_bytes=_png_bytes((255, 0, 0))
    )
    second = store.write_screenshot(
        device_id="synthetic-device", ts=TS, image_bytes=_png_bytes((0, 0, 255))
    )

    assert first is not None and second is not None
    assert first != second
    assert first.read_bytes() != second.read_bytes()


def test_storage_rejects_symlinked_screenshot_parent(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    outside = tmp_path / "outside"
    data_root.mkdir()
    outside.mkdir()
    (data_root / "screenshots").symlink_to(outside, target_is_directory=True)
    store = TimelineStore("postgresql://synthetic", data_root)

    with pytest.raises(UnsafeScreenshotPath, match="directory"):
        store.write_screenshot(
            device_id="synthetic-device", ts=TS, image_bytes=_png_bytes()
        )

    assert list(outside.rglob("*.webp")) == []


def _fixed_destination(data_root: Path, token: str) -> Path:
    return (
        data_root
        / "screenshots"
        / "2026"
        / "08"
        / "03"
        / f"synthetic-device_20260803T123456123456_{token}.webp"
    )


def test_storage_rejects_symlink_at_final_destination(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    token = "a" * 32
    destination = _fixed_destination(data_root, token)
    destination.parent.mkdir(parents=True)
    outside = tmp_path / "outside.webp"
    outside.write_bytes(b"must-not-change")
    destination.symlink_to(outside)
    store = TimelineStore("postgresql://synthetic", data_root)

    with patch("memoryd.storage.uuid.uuid4", return_value=SimpleNamespace(hex=token)):
        with pytest.raises(UnsafeScreenshotPath, match="destination"):
            store.write_screenshot(
                device_id="synthetic-device", ts=TS, image_bytes=_png_bytes()
            )

    assert destination.is_symlink()
    assert outside.read_bytes() == b"must-not-change"


def test_storage_rejects_special_file_at_final_destination(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    token = "b" * 32
    destination = _fixed_destination(data_root, token)
    destination.parent.mkdir(parents=True)
    os.mkfifo(destination)
    store = TimelineStore("postgresql://synthetic", data_root)

    with patch("memoryd.storage.uuid.uuid4", return_value=SimpleNamespace(hex=token)):
        with pytest.raises(UnsafeScreenshotPath, match="destination"):
            store.write_screenshot(
                device_id="synthetic-device", ts=TS, image_bytes=_png_bytes()
            )

    assert destination.exists()


def test_failed_encode_leaves_no_partial_or_temporary_file(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    store = TimelineStore("postgresql://synthetic", data_root)
    image_bytes = _png_bytes()

    def fail_after_partial_write(
        _image: Image.Image, handle: object, **_kwargs: object
    ) -> None:
        handle.write(b"partial")  # type: ignore[union-attr]
        raise OSError("synthetic encoder failure")

    with patch("memoryd.storage.Image.Image.save", new=fail_after_partial_write):
        with pytest.raises(OSError, match="encoder failure"):
            store.write_screenshot(
                device_id="synthetic-device", ts=TS, image_bytes=image_bytes
            )

    assert [path for path in data_root.rglob("*") if path.is_file()] == []
