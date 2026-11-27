# Rofental observation schedules

Start with the [2022/23 monthly schedule](periods/2022_2023/schedules/monthly.yml) and its [withheld observations](periods/2022_2023/withheld.yml). The [monthly](schedules/monthly/rules.yml), [14-day](schedules/14-day/rules.yml) and [7-day](schedules/7-day/rules.yml) rules apply across winters. The [evaluation rules](evaluation.yml) reserve observations for use with all three schedules.

Each winter under `periods/` contains its dates and display label in `period.yml`, its candidate observations in `inventory.yml` and its shared evaluation list in `withheld.yml`. The files under `schedules/` are chronological lists of assigned updates in project YAML format. Compare the [2022/23 monthly schedule](periods/2022_2023/schedules/monthly.yml) with its [target report](periods/2022_2023/reports/monthly_targets.yml) for the requested dates, acquisition choices and reasons for missed targets. The other reports summarize counts, spacing and excluded observations. [Counts by winter, schedule and variable](schedule_counts.yml)

The [four-winter timeline](timeline.png) compares the schedules on the same October–June axis. The 2 × 2 figure prints at 185 × 103 mm. The individual timelines below give each winter the full publication width.

| Winter and timeline | Monthly updates | 14-day updates | 7-day updates | Withheld optical / radar |
| --- | ---: | ---: | ---: | ---: |
| [2019/20](periods/2019_2020/timeline.png) | 14 | 23 | 43 | 4 / 2 |
| [2020/21](periods/2020_2021/timeline.png) | 14 | 25 | 47 | 4 / 2 |
| [2021/22](periods/2021_2022/timeline.png) | 15 | 24 | 45 | 4 / 1 |
| [2022/23](periods/2022_2023/timeline.png) | 14 | 26 | 42 | 4 / 2 |

From the planner directory, reproduce the combined figure from the reviewed schedules:

```bash
openamundsen-da planner plot --planner-dir . \
  --title "Rofental assimilation schedules and withheld observations"
```

## Selection and verification

Optical fractional snow cover (SCF) targets run from October 1 through June 30, station snow depth (HS) from November 1 through the last day of February and radar wet-snow line altitude (WSLA) from February 15 through June 30. Each period starts its own sequence of targets. The timing tolerances are seven days for monthly targets, three days for 14-day targets and two days for weekly targets. Both stations must have usable snow depth at local model midnight. The satellite products require at least 10% valid catchment coverage. Radar additionally requires a retrievable line, at least 50 wet pixels and at least one wet elevation band.

Within the timing tolerance, satellites are ranked by valid catchment support and stations by proximity to the target. On conflicting dates, radar takes priority over optical, followed by stations. All 12 schedules enable rescheduling. Five station updates moved by one day to accommodate optical acquisitions. Their target reports record the preferred and selected dates. The 2020/21 weekly station target on November 8 had no eligible pair of station measurements.

Each winter reserves one optical acquisition in October–December and one each in April, May and June. Radar holdouts are distributed across the eligible dates. Within each product the holdouts are at least 14 days apart. All 23 reserved acquisitions are excluded from every corresponding winter schedule. Five radar evaluations precede their schedule's first radar update, including both 2022/23 monthly radar holdouts. They can evaluate the schedule as a whole.

Across 537 targets, 332 received an update and 205 were missed. The 413 satellite targets and 124 station targets reproduce the reviewed date-level selections, including all missed targets. All dates came from the rules without manual changes. Validation against the four winters' source observations passed for each of the twelve schedules.

For the 2022/23 monthly project, preparation from a referenced CSV, a referenced YAML file and an equivalent inline list produced the same 14 assimilation updates, including observation times and contributing stations. All three produced 15 identical propagation windows and 25 byte-identical step YAML and satellite observation CSV files. This monthly schedule contains four station, nine optical and one radar update. The shipped tutorial project has its own schedule of eight updates.

## Reproduce the schedules

From this directory, run the following Bash commands in the framework environment. They copy the rules, period definitions and inventories to a fresh directory, regenerate all four withheld lists and twelve schedules, and compare the results and their reports byte for byte with the reviewed tables. Each timeline uses the schedules and withheld list in its period directory.

```bash
(
  set -eu
  reviewed=$PWD
  out=$(mktemp -d)
  cp -R schedules evaluation.yml "$out/"
  cd "$out"
  for year in 2019 2020 2021 2022; do
    next=$((year + 1))
    period="periods/${year}_${next}"
    mkdir -p "$period"
    cp "$reviewed/$period/period.yml" "$reviewed/$period/inventory.yml" "$period/"
    openamundsen-da planner holdouts --period "$period"
    cmp "$reviewed/$period/withheld.yml" "$period/withheld.yml"
    for cadence in monthly 14-day 7-day; do
      openamundsen-da planner generate --period "$period" --schedule "$cadence"
      cmp "$reviewed/$period/schedules/$cadence.yml" "$period/schedules/$cadence.yml"
    done
    diff -r "$reviewed/$period/reports" "$period/reports"
    openamundsen-da planner plot --period "$period" \
      --title "Rofental assimilation schedules and withheld observations, $year/${next:2}"
  done
  openamundsen-da planner plot --planner-dir . \
    --title "Rofental assimilation schedules and withheld observations"
  for image in timeline.png periods/*/timeline.png; do
    cmp "$reviewed/$image" "$image"
  done
  echo "Reproduced schedules and figures in $out"
)
```

The shipped Rofental setup includes the source files for 2022/23. Run its read-only validation against all three schedules after editing a schedule or its shared evaluation list.

```bash
openamundsen-da planner validate --project-dir ../projects/project_2022_2023 \
  --period periods/2022_2023 \
  --schedule periods/2022_2023/schedules/monthly.yml \
  --schedule periods/2022_2023/schedules/14-day.yml \
  --schedule periods/2022_2023/schedules/7-day.yml
```

To prepare the monthly pilot, use these entries in the existing `data_assimilation` section of a copy of the 2022/23 project YAML. The paths resolve from the project directory under `projects/`.

```yaml
assimilation_events: ../../planner/periods/2022_2023/schedules/monthly.yml
observation_planner:
  period: ../../planner/periods/2022_2023
  schedule: monthly
```

## Source observations

The prepared inputs use the Rofental project's 100 m grid, land-cover mask and three-hour time step in fixed UTC+1. Station eligibility comes from the shipped Latschbloder and Proviantdepot hourly CSVs. The target report lists the stations contributing to each selected update. The inventory records the UTC timestamps of the readings used to match each model midnight.

The four-winter acquisition manifest records 228 optical and 116 radar rasters, together with the SHA-256 digests of each observation and uncertainty GeoTIFF. Optical acquisition times were matched by acquisition date and Sentinel-2 tile T32TPS to the Copernicus Data Space catalogue. Each date has one distinct acquisition time. Two dates have two processing versions at the same instant. The manifest records the matching product names and catalogue identifiers. Each radar time comes from the corresponding raster's metadata sidecar. These are product metadata timestamps in UTC. [Acquisition manifest](satellite_acquisition_times.csv)

The first three winters use externally prepared Sentinel-2 SCF and Sentinel-1 wet-snow GeoTIFFs. The fourth uses the shipped Rofental products. The project's uncertainty generators supplied the missing companion rasters using the 2022/23 configuration. All selected and withheld satellite acquisitions have a verified source time, readable observation and uncertainty rasters on matching grids, and usable uncertainty in the observation summary. The two March 2021 radar rasters (March 8 and 20) contained only missing values. The June 10, 2022 optical raster had 18 finite pixels, all outside the eligible catchment. Each had zero valid cells out of 6,653 and remains identifiable in its inventory.

To rebuild the inventories from the source files, place the rasters and their companions in the configured observation directories, copy the four-winter acquisition manifest to the path referenced by both product definitions, and prepare the optical and wet-snow summaries for the relevant dates with the Rofental project configuration. For example, this command rebuilds the 2022/23 inventory from the shipped data.

```bash
openamundsen-da planner inventory ../projects/project_2022_2023 \
  --period periods/2022_2023 --variables station_hs scf wet_snow_line \
  --output "$(mktemp -d)/inventory.yml"
```

The first inventory starts October 1, 2019, to preserve the common optical target dates. The prepared forcing for that winter begins October 4, before the first selected update. The other projects start October 1. The four full winter validation periods end June 30 at 21:00 local time.
