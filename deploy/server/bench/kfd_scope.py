#!/usr/bin/env python3
"""Scope host-wide KFD process entries to GPUs exposed in this container."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

_RENDER_RE = re.compile(r"^renderD(\d+)$")
_GPU_FILE_RE = re.compile(r"^(?:vram|sdma)_(\d+)$")


def _properties(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            values[parts[0]] = parts[1]
    return values


def assigned_gpu_ids(
    *,
    kfd_root: Path,
    dri_root: Path,
    drm_root: Path,
) -> set[int]:
    """Return KFD IDs mapped by unique ID from each mounted DRM render node."""
    render_nodes = {
        path.name: int(match.group(1))
        for path in dri_root.glob("renderD*")
        if (match := _RENDER_RE.fullmatch(path.name))
    }
    if not render_nodes:
        raise RuntimeError(f"no DRM render nodes found under {dri_root}")

    mappings: dict[str, set[int]] = {name: set() for name in render_nodes}
    for render_name, render_minor in render_nodes.items():
        try:
            drm_unique_id = int(
                (
                    drm_root
                    / render_name
                    / "device"
                    / "unique_id"
                ).read_text(encoding="utf-8").strip(),
                16,
            )
        except ValueError as exc:
            raise RuntimeError(
                f"invalid DRM unique_id for mounted {render_name}"
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f"cannot read DRM unique_id for mounted {render_name}; "
                "each mounted DRM render node must map to exactly one KFD GPU ID"
            ) from exc
        for node in (kfd_root / "topology" / "nodes").glob("[0-9]*"):
            try:
                props = _properties(node / "properties")
            except OSError:
                continue
            try:
                topology_unique_id = int(props.get("unique_id", "-1"))
            except ValueError:
                continue
            if topology_unique_id != drm_unique_id:
                continue
            try:
                topology_render_minor = int(props["drm_render_minor"])
                gpu_id = int(
                    (node / "gpu_id").read_text(encoding="utf-8").strip()
                )
            except (KeyError, OSError, ValueError) as exc:
                raise RuntimeError(
                    f"cannot map mounted {render_name} to a KFD GPU ID"
                ) from exc
            if topology_render_minor != render_minor or gpu_id <= 0:
                raise RuntimeError(
                    f"mounted {render_name} has inconsistent KFD topology: "
                    f"render_minor={topology_render_minor}, gpu_id={gpu_id}"
                )
            mappings[render_name].add(gpu_id)

    invalid = {
        render_name: sorted(gpu_ids)
        for render_name, gpu_ids in mappings.items()
        if len(gpu_ids) != 1
    }
    if invalid:
        raise RuntimeError(
            "each mounted DRM render node must map to exactly one KFD GPU ID: "
            f"{invalid}"
        )
    return {next(iter(gpu_ids)) for gpu_ids in mappings.values()}


def scoped_vram_bytes(
    *,
    drm_root: Path,
    dri_root: Path,
) -> tuple[int, int]:
    """Return (used, total) VRAM for exactly the mounted DRM render nodes."""
    render_names = sorted(
        path.name
        for path in dri_root.glob("renderD*")
        if _RENDER_RE.fullmatch(path.name)
    )
    if not render_names:
        raise RuntimeError(f"no DRM render nodes found under {dri_root}")
    used_sum = 0
    total_sum = 0
    for render_name in render_names:
        device = drm_root / render_name / "device"
        try:
            total = int(
                (device / "mem_info_vram_total")
                .read_text(encoding="utf-8")
                .strip()
            )
            used = int(
                (device / "mem_info_vram_used")
                .read_text(encoding="utf-8")
                .strip()
            )
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"cannot read scoped VRAM telemetry for {render_name}"
            ) from exc
        if total <= 0 or used < 0 or used > total:
            raise RuntimeError(
                f"invalid scoped VRAM telemetry for {render_name}: "
                f"used={used}, total={total}"
            )
        used_sum += used
        total_sum += total
    return used_sum, total_sum


def process_gpu_ids(proc_dir: Path) -> set[int] | None:
    """Return GPU IDs with a live queue or non-zero usage for one KFD entry."""
    gpu_ids: set[int] = set()
    audited_usage_files = 0
    try:
        children = list(proc_dir.iterdir())
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError(f"cannot enumerate KFD process entry {proc_dir}") from exc
    for child in children:
        match = _GPU_FILE_RE.fullmatch(child.name)
        if match:
            try:
                usage = int(child.read_text(encoding="utf-8").strip())
            except FileNotFoundError as exc:
                if not proc_dir.exists():
                    return None
                raise RuntimeError(
                    f"KFD usage file vanished during audit: {child}"
                ) from exc
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"cannot read KFD GPU usage: {child}") from exc
            if usage < 0:
                raise RuntimeError(f"invalid negative KFD GPU usage: {child}")
            audited_usage_files += 1
            if usage > 0:
                gpu_ids.add(int(match.group(1)))
    queue_root = proc_dir / "queues"
    try:
        queue_entries = list(queue_root.iterdir())
    except FileNotFoundError:
        if not proc_dir.exists():
            return None
        raise RuntimeError(f"KFD queue inventory is missing: {queue_root}")
    except OSError as exc:
        raise RuntimeError(f"cannot enumerate KFD queues for {proc_dir}") from exc
    queues = [queue for queue in queue_entries if queue.name.isdigit()]
    for queue in queues:
        try:
            gpu_id = int((queue / "gpuid").read_text(encoding="utf-8").strip())
        except FileNotFoundError as exc:
            if not proc_dir.exists():
                return None
            raise RuntimeError(f"KFD queue vanished during audit: {queue}") from exc
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"cannot read KFD queue GPU ID: {queue}") from exc
        if gpu_id > 0:
            gpu_ids.add(gpu_id)
        else:
            raise RuntimeError(f"invalid KFD queue GPU ID: {queue}")
    if audited_usage_files == 0 and not queues:
        raise RuntimeError(
            f"KFD process entry {proc_dir} has no auditable GPU usage or queue"
        )
    return gpu_ids


def scoped_processes(
    *,
    kfd_root: Path,
    dri_root: Path,
    drm_root: Path,
    proc_root: Path,
) -> list[tuple[int, str, list[int]]]:
    """List only KFD processes that reference a GPU exposed in this container."""
    assigned = assigned_gpu_ids(
        kfd_root=kfd_root,
        dri_root=dri_root,
        drm_root=drm_root,
    )
    kfd_proc_root = kfd_root / "proc"
    try:
        process_entries = list(kfd_proc_root.iterdir())
    except OSError as exc:
        raise RuntimeError(
            f"cannot enumerate KFD process inventory {kfd_proc_root}"
        ) from exc
    rows: list[tuple[int, str, list[int]]] = []
    for proc_dir in process_entries:
        if not proc_dir.name.isdigit():
            continue
        try:
            pid = int(proc_dir.name)
        except ValueError:
            continue
        process_ids = process_gpu_ids(proc_dir)
        if process_ids is None:
            continue
        if not process_ids:
            continue
        touched = process_ids & assigned
        if not touched:
            continue
        try:
            comm = (proc_root / str(pid) / "comm").read_text(
                encoding="utf-8"
            ).strip()
        except OSError:
            comm = "unknown"
        rows.append((pid, comm or "unknown", sorted(touched)))
    return sorted(rows)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kfd-root", type=Path, default=Path("/sys/class/kfd/kfd"))
    parser.add_argument("--dri-root", type=Path, default=Path("/dev/dri"))
    parser.add_argument("--drm-root", type=Path, default=Path("/sys/class/drm"))
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))
    parser.add_argument("--assigned-ids", action="store_true")
    parser.add_argument(
        "--vram",
        choices=("used", "free", "total"),
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.assigned_ids:
        print(
            ",".join(
                str(value)
                for value in sorted(
                    assigned_gpu_ids(
                        kfd_root=args.kfd_root,
                        dri_root=args.dri_root,
                        drm_root=args.drm_root,
                    )
                )
            )
        )
        return
    if args.vram:
        used, total = scoped_vram_bytes(
            drm_root=args.drm_root,
            dri_root=args.dri_root,
        )
        values = {"used": used, "free": total - used, "total": total}
        print(values[args.vram])
        return
    for pid, comm, gpu_ids in scoped_processes(
        kfd_root=args.kfd_root,
        dri_root=args.dri_root,
        drm_root=args.drm_root,
        proc_root=args.proc_root,
    ):
        print(f"{pid}\t{comm}\t{','.join(str(value) for value in gpu_ids)}")


if __name__ == "__main__":
    main()
