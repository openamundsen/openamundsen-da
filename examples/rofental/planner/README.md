# Rofental observation schedules

Open the [2022/23 weekly report](periods/2022_2023/reports/7-day.pdf) to see which targets received a station, optical or radar observation and which were missed. Its [schedule](periods/2022_2023/schedules/7-day.yml) contains the dated updates used by the project. Its [detailed results](periods/2022_2023/reports/details/7-day.yml) give target dates, acquisition choices and exclusions by observation type.

Each winter has one `inventory.csv` and one `withheld.yml` shared by all three schedules. The [assimilation rules](schedules/) set the cadence, quality thresholds and tolerance for each observation type. The [evaluation rules](evaluation.yml) reserve optical and radar acquisitions before scheduling.

| Winter | Monthly report | 14-day report | 7-day report | Withheld optical / radar |
| --- | ---: | ---: | ---: | ---: |
| [2019/20](periods/2019_2020/timeline.png) | [14 updates](periods/2019_2020/reports/monthly.pdf) | [23 updates](periods/2019_2020/reports/14-day.pdf) | [43 updates](periods/2019_2020/reports/7-day.pdf) | 4 / 2 |
| [2020/21](periods/2020_2021/timeline.png) | [14 updates](periods/2020_2021/reports/monthly.pdf) | [25 updates](periods/2020_2021/reports/14-day.pdf) | [47 updates](periods/2020_2021/reports/7-day.pdf) | 4 / 2 |
| [2021/22](periods/2021_2022/timeline.png) | [15 updates](periods/2021_2022/reports/monthly.pdf) | [24 updates](periods/2021_2022/reports/14-day.pdf) | [45 updates](periods/2021_2022/reports/7-day.pdf) | 4 / 1 |
| [2022/23](periods/2022_2023/timeline.png) | [14 updates](periods/2022_2023/reports/monthly.pdf) | [26 updates](periods/2022_2023/reports/14-day.pdf) | [42 updates](periods/2022_2023/reports/7-day.pdf) | 4 / 2 |

From this directory, regenerate the 2022/23 weekly schedule in a working copy. The first two commands replace the existing selection and its reports. After editing dated assignments by hand, use the third command to refresh the report.

```bash
period=periods/2022_2023
openamundsen-da planner holdouts --period "$period" --overwrite
openamundsen-da planner generate --period "$period" --schedule 7-day --overwrite
openamundsen-da planner report --project-dir ../projects/project_2022_2023 --period "$period" --schedule 7-day
```

The inventories use the Rofental 100 m grid, masked catchment and local midnight (UTC+1) for station snow depth. The [satellite manifest](../obs/satellite_acquisition_times.csv) records the Sentinel-2 catalogue times and Sentinel-1 sidecar times. It includes observation and uncertainty raster hashes for the 344 four-winter acquisitions. The 2022/23 project includes its source rasters. The first three winters require external rasters for source validation. All schedules can be regenerated from the shipped inventories. [Four-winter timeline](timeline.png) · [Observation Planner guide](../../../docs/guides/observation-planner.md)
