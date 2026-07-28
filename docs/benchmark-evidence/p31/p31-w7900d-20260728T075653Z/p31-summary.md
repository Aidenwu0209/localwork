# P3.1 ROCm ablation summary

Raw evidence run directory: `p31-w7900d-20260728T075653Z/` (the directory containing this summary).
Every row is derived from one warm-up plus the recorded measured batches;
the benchmark driver enforces at least three trials.

## Brain: quant × MTP × client concurrency

| Quant | MTP | conc | prefill t/s | decode t/s/request | aggregate output t/s | request P95 ms | resident / peak VRAM GiB | draft accepted / generated | correct prefix / pass | n |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Q8_0 | off | 1 | 173.4 | 21.4 | 20.9 | 11056.9 | 29.36 / 29.37 | — | 80 / 100% | 3 |
| Q8_0 | off | 4 | 55.7 | 15.1 | 56.8 | 16294.6 | 29.36 / 29.39 | — | 80 / 100% | 3 |
| Q8_0 | off | 8 | 30.4 | 10.0 | 74.5 | 24858.3 | 29.36 / 29.43 | — | 80 / 100% | 3 |
| Q8_0 | on | 1 | 117.6 | 45.1 | 42.2 | 5496.6 | 34.31 / 34.34 | 558 / 564 (98.9%) | 80 / 100% | 3 |
| Q8_0 | on | 4 | 44.1 | 15.0 | 56.0 | 16543.5 | 34.31 / 34.40 | 2232 / 2256 (98.9%) | 80 / 100% | 3 |
| Q8_0 | on | 8 | 22.7 | 9.9 | 72.5 | 25477.0 | 34.31 / 34.43 | 4464 / 4512 (98.9%) | 80 / 100% | 3 |
| Q6_K | off | 1 | 147.4 | 24.7 | 23.9 | 9654.7 | 23.49 / 23.50 | — | 80 / 100% | 3 |
| Q6_K | off | 4 | 51.1 | 13.7 | 51.3 | 18168.3 | 23.49 / 23.50 | — | 80 / 100% | 3 |
| Q6_K | off | 8 | 25.6 | 8.0 | 59.7 | 31064.3 | 23.49 / 23.83 | — | 80 / 100% | 3 |
| Q6_K | on | 1 | 104.0 | 44.8 | 41.6 | 5571.6 | 28.44 / 28.46 | 558 / 564 (98.9%) | 80 / 100% | 3 |
| Q6_K | on | 4 | 41.9 | 18.0 | 64.7 | 14305.6 | 28.44 / 28.47 | 2232 / 2256 (98.9%) | 80 / 100% | 3 |
| Q6_K | on | 8 | 23.1 | 10.7 | 77.1 | 23979.2 | 28.44 / 29.23 | 4464 / 4512 (98.9%) | 80 / 100% | 3 |
| Q4_K_M | off | 1 | 164.6 | 28.0 | 27.2 | 8507.9 | 18.56 / 18.57 | — | 80 / 100% | 3 |
| Q4_K_M | off | 4 | 52.3 | 13.2 | 49.8 | 18567.0 | 18.56 / 18.57 | — | 80 / 100% | 3 |
| Q4_K_M | off | 8 | 30.8 | 7.4 | 55.2 | 33494.4 | 18.56 / 18.90 | — | 80 / 100% | 3 |
| Q4_K_M | on | 1 | 114.3 | 42.8 | 40.1 | 5771.9 | 23.51 / 23.53 | 558 / 564 (98.9%) | 80 / 100% | 3 |
| Q4_K_M | on | 4 | 43.1 | 19.8 | 71.5 | 12937.0 | 23.51 / 23.54 | 2232 / 2256 (98.9%) | 80 / 100% | 3 |
| Q4_K_M | on | 8 | 23.5 | 11.5 | 83.0 | 22496.6 | 23.51 / 24.10 | 4464 / 4512 (98.9%) | 80 / 100% | 3 |

### MTP aggregate-throughput ratio (on / off)

| Quant | concurrency | ratio |
|---|---:|---:|
| Q8_0 | 1 | 2.018× |
| Q8_0 | 4 | 0.986× |
| Q8_0 | 8 | 0.973× |
| Q6_K | 1 | 1.738× |
| Q6_K | 4 | 1.261× |
| Q6_K | 8 | 1.292× |
| Q4_K_M | 1 | 1.474× |
| Q4_K_M | 4 | 1.436× |
| Q4_K_M | 8 | 1.504× |

Deterministic MTP output parity: **PASS**.

## Perceive: server slots paired with client concurrency

| Quant | server -np | client conc | prefill t/s | decode t/s/request | aggregate output t/s | request P95 ms | resident / peak VRAM GiB | visual text pass | n |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Q8_0 | 1 | 1 | 1152.9 | 59.4 | 31.2 | 739.1 | 6.38 / 6.51 | 100% | 3 |
| Q8_0 | 2 | 2 | 581.8 | 46.8 | 40.7 | 1150.8 | 6.41 / 6.55 | 100% | 3 |
| Q8_0 | 4 | 4 | 430.4 | 32.8 | 50.0 | 1861.6 | 6.48 / 6.62 | 100% | 3 |

Environment, hashes, server logs, per-request timings, and rocm-smi JSON remain beside this summary.
