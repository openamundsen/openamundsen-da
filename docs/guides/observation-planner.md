---
layout: default
title: Observation Planner
parent: Documentation
nav_order: 4
---

# Observation Planner

Plan updates for a reanalysis project before running an ensemble. Each project
references one reviewed assimilation schedule. To compare update frequencies,
build one observation inventory per period and use the same withheld
observations to evaluate every schedule for that period.

## Set up the planner folders

The Rofental planner under `examples/rofental/planner/` compares three
schedules across four periods. Define each schedule once under `schedules/`
and reuse its rules across periods. Each directory under `periods/` holds the
inputs and generated selections for a specific date range.

```text
planner/
├── evaluation.yml
├── schedules/
│   ├── monthly.yml
│   ├── 14-day.yml
│   └── 7-day.yml
└── periods/
    └── 2022_2023/
        ├── period.yml
        ├── inventory.csv
        ├── withheld.yml
        ├── schedules/
        │   ├── monthly.yml
        │   ├── 14-day.yml
        │   └── 7-day.yml
        └── reports/
            ├── monthly.pdf
            ├── 14-day.pdf
            ├── 7-day.pdf
            └── details/
                ├── monthly.yml
                ├── 14-day.yml
                ├── 7-day.yml
                └── withheld.yml
```

Set inclusive dates and an optional display label in `period.yml`.

```yaml
label: '2022/23'
start: '2022-10-01'
end: '2023-06-30'
```

The planner uses the period's start year when resolving relative dates in
rules. For example, `0-10-01` means October 1 of that year and `1-02-last`
means the last day of the following February, including leap years. ISO dates
set absolute bounds.

For a new period with one configured schedule, generation writes
`<name>.schedule.yml` beside `period.yml`. Its PDF and `<name>_details.yml`
share `reports/`. Additional schedules can use the same flat layout. The planner
also recognizes existing schedules under `schedules/`.

## Define a schedule

Each `schedules/<name>.yml` file contains defaults and observation-specific
settings. The planner also reads the older `schedules/<name>/rules.yml` layout.
Use `monthly` for calendar months or a positive integer for a fixed
interval in days. Give each observation a priority. It is active throughout
the period unless you set `period_start` and `period_end` to narrow its date
range. Use `intervals` for several date ranges or to change the cadence within
a period. Each interval starts its own sequence of targets and can override
the frequency and tolerance.

```yaml
defaults:
  frequency: monthly
  tolerance_days: 7
  reschedule: true
  min_spacing_days: 1
observations:
  scf:
    priority: 1
    period_start: '0-10-01'
    period_end: '1-06-30'
    ranking: quality
    min_support_coverage_ratio: 0.1
  station_hs:
    priority: 2
    ranking: nearest
    min_station_count: 2
    intervals:
      - period_start: '0-11-01'
        period_end: '0-12-31'
      - period_start: '1-01-01'
        period_end: '1-02-last'
        frequency: 14
```

Apply thresholds such as `min_station_count`, `min_support_coverage_ratio`
and `max_cloud_fraction` to screen observations. A rule can tighten a project
quality limit. After screening, `nearest` favors proximity to the target and
`quality` favors the ranking measure. Station updates use `nearest`. Give each
variable a distinct priority and use that value across its intervals. Lower
numbers take precedence in same-day conflicts. Set `reschedule` consistently
across the schedule. When enabled, a lower-priority update can move to another
eligible date within its original timing tolerance, date range and spacing
constraints.

## Inventory the observations

Use a project configuration that defines the station files or satellite products.
For satellite observations, first preprocess the configured sources to the
project observation summaries. Use the same setup grid, region of interest,
observation classes and land-cover settings as the planned runs.

From the repository root, use these paths to plot and validate the shipped
Rofental schedules. To generate your own schedules, point `planner` at a
folder with your rule files and `period` at a directory with your `period.yml`.

```bash
project=examples/rofental/projects/project_2022_2023
planner=examples/rofental/planner
period="$planner/periods/2022_2023"
```

Build a new `inventory.csv` using the dates in `period.yml` or start with the
shipped inventory when reproducing the Rofental selections. A period can also
use a YAML inventory. Use `--output` to choose its filename and format.

```bash
openamundsen-da planner inventory "$project" --period "$period" \
  --variables station_hs scf wet_snow_line
```

For stations, the inventory records which stations have a usable value at the
target model time and the timestamps of the contributing measurements. It uses
the project's clock and the framework's station time matcher. For satellite
products, it records the acquisition identity, known acquisition time, grid
coverage and available quality measures. A scene with missing quality
information can remain in the inventory with a reason for exclusion. Regenerate
the inventory when its inputs or spatial mask change. To test a new selection
threshold, apply the updated rules to the stored measurements.

## Reserve observations for evaluation

Use the optional `evaluation.yml` at the planner root to reserve observations
for all schedules in each period. Group rules by observation type under
`evaluation`. Shared thresholds and spacing can be set once under `defaults`.
Each variable can set its own values and define multiple selection intervals.
Use a scalar `count` for every period or `count_by_year` to specify the
requested count for each listed period start year.

```yaml
evaluation:
  defaults:
    min_spacing_days: 14
    min_support_coverage_ratio: 0.1
  scf:
    intervals:
      - {period_start: '0-10-01', period_end: '0-12-31', strategy: best, count: 1}
      - {period_start: '1-04-01', period_end: '1-06-30', strategy: best_per_month, count: 3}
  wet_snow_line:
    period_start: '1-02-15'
    period_end: '1-06-30'
    strategy: spread
    count_by_year: {2019: 2, 2020: 2, 2021: 1, 2022: 2}
    min_n_wet: 50
    min_wet_bands: 1
```

Use `best` to reserve a requested count of high-quality observations,
`best_per_month` to select across distinct calendar months or `spread` to
distribute observations across the eligible dates. Set `min_spacing_days` for
the required separation. A station reservation can identify individual stations.
Other available stations can still contribute if the station minimum is met.

When `evaluation.yml` exists, prepare `withheld.yml` before generating schedules.

```bash
openamundsen-da planner holdouts --period "$period"
```

## Generate and review schedules

```bash
for cadence in monthly 14-day 7-day; do
  openamundsen-da planner generate --period "$period" --schedule "$cadence"
done

openamundsen-da planner plot --period "$period"
openamundsen-da planner plot --planner-dir "$planner"
```

Each `schedules/<name>.yml` is a chronological list of assigned updates using
the same entries as `data_assimilation.assimilation_events` in the project.
Edit this file to change the observations used by the project. Each entry has a
date and variable. Satellite entries retain known acquisition times. Source IDs
identify otherwise ambiguous acquisitions, and station IDs specify a selected subset.

```yaml
- date: '2022-10-03'
  variable: scf
  observation_time: '2022-10-03T10:17:49.024000Z'
- date: '2022-11-03'
  variable: station_hs
```

Generating a schedule writes a compact `reports/<name>.pdf` for review.
`reports/details/<name>.yml` groups the summary, target outcomes and exclusions
by observation type. Each target records its requested date (`target_date`)
and the assigned observation date or reason for a miss. The details also
include candidate counts and quality diagnostics. The shared evaluation
selection has its own details in `reports/details/withheld.yml`. A planner with
a single schedule keeps `<name>_details.yml` next to its PDF.
Use `--overwrite` to replace an existing selection and its reports.

After editing a period schedule, refresh its report from the reviewed
assignments and the source observations in the project.

```bash
openamundsen-da planner report --project-dir "$project" --period "$period" --schedule 7-day
```

`plot --period` writes `timeline.png` in the period directory. To compare all
periods in one figure from the planner directory, run `plot --planner-dir .`
or `plot` with no input flags. The combined figure is written to
`timeline.png` in the planner directory. It orders schedules by average
target count across periods with that schedule, then by filename. Use
`--title` to set the figure title or `--output` to write to another path.
Parentheses give the count of assigned updates or withheld observations.
Crosses use the requested dates from the target report and reflect the current schedule assignments. Without a target report, a compact file shows its assigned updates. The plot also reads handwritten schedule files
from each period's `schedules/` directory.

For a custom set of panels, use
`openamundsen-da planner plot --panels timeline.yml --output timeline.png`.
In the optional `timeline.yml`, `panels` maps display labels to directories
relative to the configuration file. `schedules` maps row labels to CSV or
YAML paths relative to each panel directory, such as `schedules/monthly.yml`.
Both mappings follow their order in the file. Set `withheld` to include an
evaluation table from each panel directory and `title` to name the figure.

## Validate and use a reviewed schedule

```bash
openamundsen-da planner validate --project-dir "$project" --period "$period" \
  --schedule "$period/schedules/monthly.yml" \
  --schedule "$period/schedules/14-day.yml" \
  --schedule "$period/schedules/7-day.yml"
```

The check is read-only. It reports missed prescribed targets as warnings and
invalid assignments as errors. A manual timing exception requires a
`timing_exception_reason` on the edited row. Quality limits and evaluation
exclusions still apply. A date-only satellite row can be reviewed in a draft.
Where the project requires an exact acquisition time, resolve the time from
source metadata before running. If multiple acquisitions match, identify the
intended source in the row.

A handwritten schedule can contain just `variable` and `date` when they
uniquely identify usable project observations. Use a timezone-aware ISO
timestamp for `observation_time`, the inventory's source identifier for
`source_id` and vertical bars to separate `station_ids`.

```yaml
- variable: station_hs
  date: '2022-11-03'
  station_ids: latschbloder|proviantdepot
```

Place the reviewed files inside the setup. Paths in the project YAML are
relative to the project directory. For a project in `projects/project_2022_2023/`
with a sibling `planner/` directory next to `projects/`, use these settings.

```yaml
data_assimilation:
  assimilation_events: ../../planner/periods/2022_2023/schedules/monthly.yml
  observation_planner:
    period: ../../planner/periods/2022_2023
    schedule: monthly
```

Project loading validates the referenced schedule and uses the existing
propagation-window builder. Inline event lists remain supported. Add
`observation_planner: {}` to apply planner checks to an inline list during
project loading. The standalone validator can also check an inline list
before preparation. Running validation without rules checks applicable project
requirements and reports which timing and frequency checks it could apply.

## Use explicit files

The planner also accepts standalone CSV or YAML tables through explicit paths.
Use `--rules`, `--inventory`, `--year` and `--output` for selection with flat
rules. The `holdouts` command also accepts grouped evaluation rules with
explicit `period_start` and `period_end`. Standalone generation writes its
PDF and grouped YAML beside the schedule, appending `.pdf` and `.details.yml`
to the full output filename (for example, `trial.csv.pdf` and
`trial.csv.details.yml`). The planner can still read older
schedules containing full target rows. Use `--withheld` to supply shared
evaluation observations when generating, validating or plotting. For a plot
from selected files, repeat `--schedule [LABEL=]FILE` and set `--output`.
