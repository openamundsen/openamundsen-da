---
layout: default
title: Observation Planner
parent: Documentation
nav_order: 4
---

# Observation Planner

Plan updates for a reanalysis project before running an ensemble. A project
references one reviewed assimilation schedule. To compare update frequencies,
build the observation inventory once for the study period and reserve one
evaluation list to use with every schedule.

## Inventory the observations

Use a project configuration that defines the station files or satellite products.
For satellite observations, first preprocess the configured sources to the
project observation summaries. Use the same setup grid, region of interest,
observation classes and land-cover settings as the planned runs.

```bash
project=examples/rofental/projects/project_2022_2023
rules=examples/rofental/planner/rules.yml

openamundsen-da planner inventory "$project" \
  --start 2022-10-01 --end 2023-06-30 --variables station_hs scf wet_snow_line \
  --output inventory.yml
```

For stations, the inventory records which stations have a usable value at the
target model time and the timestamps of the contributing measurements. It uses
the project's clock and the framework's station time matcher. For satellite
products, it records the acquisition identity, known acquisition time, grid
coverage and available quality measures. A scene with missing quality
information can remain in the inventory with a reason for exclusion. Regenerate
the inventory when its inputs or spatial mask change. Editing a selection
threshold only requires applying the rules to the stored measurements again.

## Select updates and withhold observations for evaluation

Rules, schedules, inventories and evaluation lists use headered CSV files or
YAML sequences of rows. Each assimilation rule names the observation variable,
active period, target frequency, timing tolerance and observation-type priority.
Use an ISO date for an absolute period boundary. For rules shared across
winters, `0-10-01` denotes October 1 of the supplied year and `1-02-last`
denotes the last day of the following February, including leap years.

Use `monthly` for calendar months or a number of days for a fixed interval.
`min_station_count`, `min_support_coverage_ratio` and `max_cloud_fraction`
are examples of bounds on inventory measurements. A rule can tighten a project
quality limit. Use `nearest` to favor proximity to the target after screening,
or `quality` to favor the ranking measure. The priority order settles same-day
conflicts. The shared `reschedule` switch controls whether a lower-priority
update may use another eligible observation. A replacement still has to meet
the original tolerance, period and spacing.

```bash
openamundsen-da planner holdouts --inventory inventory.yml --rules "$rules" \
  --year 2022 --output evaluation.yml
for cadence in monthly 14-day 7-day; do
  openamundsen-da planner generate --inventory inventory.yml --rules "$rules" \
    --holdouts evaluation.yml --year 2022 --schedule "$cadence" --output "$cadence.yml"
done
openamundsen-da planner plot --schedule monthly.yml --schedule 14-day.yml \
  --schedule 7-day.yml --holdouts evaluation.yml --output timeline.png
```

For evaluation rules, use `best` for a requested count of high-quality
observations, `best_per_month` to select across distinct calendar months, or
`spread` to distribute observations across the eligible period. Set `count`
and `min_spacing_days` for the required separation. If too few observations
qualify, inspect the achieved count in the companion `_summary` table and the
reasons in `_exclusions`. A station reservation can identify individual
stations. Other available stations can still contribute if the minimum is met.

Review the actual and missed targets, variable assignments and common evaluation
observations. Edit the schedule file directly if needed. Generation protects an
existing schedule until you explicitly request replacement with `--overwrite`.

## Validate and use a reviewed schedule

```bash
openamundsen-da planner validate --project-dir "$project" --inventory inventory.yml \
  --rules "$rules" --withheld evaluation.yml --schedule monthly.yml \
  --schedule 14-day.yml --schedule 7-day.yml
```

The check is read-only. It reports missing prescribed targets as warnings and
reports invalid assigned observations as errors. A manual timing exception
requires a `timing_exception_reason` on the edited row. Quality limits and evaluation exclusions still apply. A
date-only satellite row can be reviewed in a draft. Where the project requires
an exact acquisition time, resolve the time from source metadata before running.
If multiple acquisitions match, identify the intended source in the row.

A handwritten schedule can contain just the required `variable` and `date`
fields when these uniquely identify usable project observations. Use a
timezone-aware ISO timestamp for `observation_time`, the inventory's source
identifier for `source_id`, and station identifiers separated by a vertical bar
for `station_ids`. For example, this YAML selects an observed station update.

```yaml
- variable: station_hs
  date: '2022-11-03'
  station_ids: latschbloder|proviantdepot
```

Running the validator without rules checks the applicable project requirements and reports
which timing and frequency checks it could apply.

Place the reviewed files inside the setup. In the project YAML, paths to planner
tables are relative to the project directory. For example, if the tables live
next to the project YAML, the reference takes this form.

```yaml
data_assimilation:
  assimilation_events: monthly.yml
  observation_planner:
    inventory: inventory.yml
    rules: rules.yml
    withheld: evaluation.yml
    schedule: monthly
    year: 2022
```

Project loading validates the referenced schedule and uses the existing
propagation-window builder. Inline event lists remain supported. To apply the
planner checks to a minimal inline list as part of project loading, include an
`observation_planner: {}` mapping. The standalone validator can also check an
inline list before preparation.
