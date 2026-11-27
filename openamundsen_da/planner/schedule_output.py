"""Turn assigned planner targets into the rows a project needs for assimilation."""

from collections import defaultdict

from openamundsen_da.util.observation_time import parse_utc_timestamp
from openamundsen_da.util.station_da import is_station_variable

from .selection import Row, station_ids

UPDATE_FIELDS = ("date", "variable", "observation_time", "source_id", "station_ids")


def _time(row: Row):
    raw = row.get("observation_time")
    return parse_utc_timestamp(raw, field="observation_time") if raw else None


def assigned_updates(targets: list[dict], inventory: list[Row]) -> list[dict]:
    """Keep the fields needed to resolve each assignment to its reviewed observations."""
    by_day = defaultdict(list)
    for observation in inventory:
        by_day[observation["variable"], observation["date"]].append(observation)

    updates = []
    for target in targets:
        day, variable = target["date"], target["variable"]
        if not day:
            continue
        update = {"date": day, "variable": variable}
        matches = by_day[variable, day]
        station = is_station_variable(variable)
        stamp = _time(target)
        if stamp and (not station or any(_time(item) != stamp for item in matches)):
            update["observation_time"] = target["observation_time"]
            matches = [item for item in matches if _time(item) == stamp]
        if len(matches) > 1 and target.get("source_id"):
            update["source_id"] = target["source_id"]
            matches = [item for item in matches if item.get("source_id") == target["source_id"]]
        if len(matches) != 1:
            raise ValueError(f"{variable} on {day}: cannot uniquely identify the selected observation")
        if station and station_ids(target) != station_ids(matches[0]):
            update["station_ids"] = target["station_ids"]
        updates.append(update)
    return sorted(updates, key=lambda row: row["date"])
