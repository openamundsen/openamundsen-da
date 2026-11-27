---
layout: default
title: Example data sets
parent: Documentation
nav_order: 6
---

# Example data sets

The release image contains the shipped, versioned Rofental example under
`/workspace/examples`. It is a user-facing baseline and CI contract, not a
runtime output archive.

## Rofental

`examples/rofental` is the single-domain tutorial and scientific regression
baseline. It contains prepared openAMUNDSEN grids and forcing, snow-cover and
wet-snow products, station observations and one Rofental 2022–2023 project.
Static inputs are available at 100, 250 and 500 m; the setup YAML selects the
active resolution.

The current example supplies hourly meteorological forcing for 2019/20 through
2022/23. The configured project still runs 2022/23. It now uses the original
station precipitation, reversing the 40% reduction in the v0.9.4 demonstration.
Expect different numerical results, including benchmark scores. Use v0.9.4 to
reproduce the earlier results and tutorial reference images.
[Forcing sources and corrections](https://github.com/openamundsen/openamundsen-da/blob/main/examples/README.md#meteo)

Copy it from the image before use:

```bash
docker run --rm \
  -v "$HOME/openamundsen-da-tutorial:/data" \
  {{ site.data.release.image }} \
  bash -lc 'cp -a /workspace/examples/rofental /data/rofental'
```

Here `$HOME/openamundsen-da-tutorial` is a concrete host directory. Replace it
with another absolute path if you want the copied setup elsewhere.

The [How to Use tutorial]({{ '/tutorial/' | relative_url }}) works through this
existing setup incrementally. Its 11 reference images are selected from the
canonical ES30 output by a hash-checked publication manifest.

## Adapting an example

Copy the example outside the repository, keep the setup/project ownership boundary
and replace one input class at a time. Do not copy completed `steps/`, `results/`
or logs into a new experiment. Start with a short project, coarse grid and small
ensemble before increasing computational cost.
