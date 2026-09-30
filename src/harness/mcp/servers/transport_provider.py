"""Transport provider abstraction and synthetic Bavarian timetable provider."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

from harness.mcp.servers.transport_models import (
    JourneyLeg,
    Station,
    TransportDomainError,
    TransportErrorCode,
    TransportJourney,
)


class TransportProvider(ABC):
    """Abstract interface defining the contract for transit schedule and routing providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name and version identifier of the transport data source."""
        ...

    @abstractmethod
    def search_stations(self, query: str) -> list[Station]:
        """Search and resolve stations by name or keyword query."""
        ...

    @abstractmethod
    def validate_stations(self, origin: str, destination: str) -> tuple[str, str]:
        """Normalize and validate origin and destination station names.

        Returns:
            Tuple of canonical (origin, destination) station names or IDs.

        Raises:
            TransportDomainError: If station is unrecognized or invalid.
        """
        ...

    @abstractmethod
    def query_connections(
        self,
        origin: str,
        destination: str,
        departure_time: datetime,
        max_results: int,
    ) -> list[TransportJourney]:
        """Query scheduled transit connections between stations.

        Returns:
            List of TransportJourney objects satisfying query criteria.

        Raises:
            TransportDomainError: On domain or upstream provider failures.
        """
        ...


class SyntheticTransportProvider(TransportProvider):
    """Deterministic synthetic Bavarian rail timetable provider for reproducible evaluation.

    Scope & Provenance:
        Operates on fixed 8-station regional network inspired by Bavarian rail routes.
        Direct connections and 1-transfer connections are supported with Pareto
        dominance pruning to discard dominated journeys.
    """

    DATA_SOURCE_NAME = "synthetic_bavarian_timetable_v1"

    SUPPORTED_STATIONS: list[str] = [
        "Passau Hbf",
        "Vilshofen",
        "Plattling",
        "Deggendorf",
        "Landshut Hbf",
        "München Hbf",
        "Regensburg Hbf",
        "Nürnberg Hbf",
    ]

    _LINE_DEFINITIONS: list[dict[str, Any]] = [
        # RE 3: Passau Hbf -> Vilshofen -> Plattling -> Landshut Hbf -> München Hbf
        {
            "line": "RE 3",
            "direction": "southwest",
            "hours": list(range(5, 23)),  # 05:00 to 22:00
            "stops": [
                ("Passau Hbf", 25, "5"),
                ("Vilshofen", 40, "2"),
                ("Plattling", 55, "1"),
                ("Landshut Hbf", 105, "3"),   # minute 45 of next hour
                ("München Hbf", 150, "24"),  # minute 30 two hours later
            ],
        },
        # RE 3: München Hbf -> Landshut Hbf -> Plattling -> Vilshofen -> Passau Hbf
        {
            "line": "RE 3",
            "direction": "northeast",
            "hours": list(range(5, 23)),
            "stops": [
                ("München Hbf", 20, "24"),
                ("Landshut Hbf", 65, "3"),   # minute 05 of next hour
                ("Plattling", 115, "1"),     # minute 55 of next hour
                ("Vilshofen", 130, "2"),     # minute 10 two hours later
                ("Passau Hbf", 145, "5"),    # minute 25 two hours later
            ],
        },
        # ICE 28: Passau Hbf -> Plattling -> Regensburg Hbf -> Nürnberg Hbf
        {
            "line": "ICE 28",
            "direction": "northwest",
            "hours": [6, 8, 10, 12, 14, 16, 18, 20],
            "stops": [
                ("Passau Hbf", 10, "3"),
                ("Plattling", 40, "3"),
                ("Regensburg Hbf", 75, "1"),  # minute 15 of next hour
                ("Nürnberg Hbf", 130, "6"),   # minute 10 two hours later
            ],
        },
        # ICE 28: Nürnberg Hbf -> Regensburg Hbf -> Plattling -> Passau Hbf
        {
            "line": "ICE 28",
            "direction": "southeast",
            "hours": [7, 9, 11, 13, 15, 17, 19, 21],
            "stops": [
                ("Nürnberg Hbf", 50, "6"),
                ("Regensburg Hbf", 105, "1"),  # minute 45 of next hour
                ("Plattling", 140, "3"),       # minute 20 two hours later
                ("Passau Hbf", 170, "3"),      # minute 50 two hours later
            ],
        },
        # RB 35: Plattling <-> Deggendorf (Branch line shuttle)
        {
            "line": "RB 35",
            "direction": "north",
            "hours": list(range(6, 23)),
            "stops": [
                ("Plattling", 10, "4"),
                ("Deggendorf", 22, "1"),
            ],
        },
        {
            "line": "RB 35",
            "direction": "south",
            "hours": list(range(6, 23)),
            "stops": [
                ("Deggendorf", 38, "1"),
                ("Plattling", 50, "4"),
            ],
        },
        # RE 2: München Hbf <-> Landshut Hbf <-> Regensburg Hbf
        {
            "line": "RE 2",
            "direction": "north",
            "hours": list(range(6, 22)),
            "stops": [
                ("München Hbf", 45, "21"),
                ("Landshut Hbf", 90, "2"),    # minute 30 of next hour
                ("Regensburg Hbf", 135, "5"), # minute 15 two hours later
            ],
        },
        {
            "line": "RE 2",
            "direction": "south",
            "hours": list(range(6, 22)),
            "stops": [
                ("Regensburg Hbf", 45, "5"),
                ("Landshut Hbf", 90, "2"),   # minute 30 of next hour
                ("München Hbf", 135, "21"),  # minute 15 two hours later
            ],
        },
    ]

    def __init__(self) -> None:
        self._station_lookup: dict[str, str] = {
            s.lower(): s for s in self.SUPPORTED_STATIONS
        }

    @property
    def provider_name(self) -> str:
        return self.DATA_SOURCE_NAME

    def search_stations(self, query: str) -> list[Station]:
        cleaned = query.strip().lower()
        results: list[Station] = []
        for name in self.SUPPORTED_STATIONS:
            if cleaned in name.lower():
                results.append(
                    Station(
                        id=name.lower().replace(" ", "-"),
                        name=name,
                    )
                )
        return results

    def validate_stations(self, origin: str, destination: str) -> tuple[str, str]:
        cleaned_orig = origin.strip().lower()
        if cleaned_orig not in self._station_lookup:
            valid_list = ", ".join(f"'{s}'" for s in self.SUPPORTED_STATIONS)
            raise TransportDomainError(
                TransportErrorCode.INVALID_STATION,
                f"Unknown station '{origin}'. Supported stations are: {valid_list}",
            )

        cleaned_dest = destination.strip().lower()
        if cleaned_dest not in self._station_lookup:
            valid_list = ", ".join(f"'{s}'" for s in self.SUPPORTED_STATIONS)
            raise TransportDomainError(
                TransportErrorCode.INVALID_STATION,
                f"Unknown station '{destination}'. Supported stations are: {valid_list}",
            )

        norm_origin = self._station_lookup[cleaned_orig]
        norm_dest = self._station_lookup[cleaned_dest]

        if norm_origin == norm_dest:
            raise TransportDomainError(
                TransportErrorCode.INVALID_STATION,
                f"Origin and destination stations cannot be identical ('{norm_origin}').",
            )

        return norm_origin, norm_dest

    def _generate_line_runs(self, target_date: datetime.date) -> list[dict[str, Any]]:
        base_dt = datetime.combine(target_date, time(0, 0))
        runs: list[dict[str, Any]] = []

        for line_def in self._LINE_DEFINITIONS:
            line_name = line_def["line"]
            for hour in line_def["hours"]:
                first_stop_name, first_offset, _ = line_def["stops"][0]
                run_start_dt = base_dt + timedelta(hours=hour, minutes=first_offset)
                run_stops = []
                for station, offset, platform in line_def["stops"]:
                    diff_minutes = offset - first_offset
                    stop_dt = run_start_dt + timedelta(minutes=diff_minutes)
                    run_stops.append({
                        "station": station,
                        "datetime": stop_dt,
                        "platform": platform,
                    })
                runs.append({
                    "line": line_name,
                    "stops": run_stops,
                })
        return runs

    def query_connections(
        self,
        origin: str,
        destination: str,
        departure_time: datetime,
        max_results: int,
    ) -> list[TransportJourney]:
        norm_origin, norm_dest = self.validate_stations(origin, destination)
        target_date = departure_time.date()
        runs = self._generate_line_runs(target_date)

        candidate_connections: list[TransportJourney] = []

        # Direct connections
        for run in runs:
            stops = run["stops"]
            st_idx = {s["station"]: i for i, s in enumerate(stops)}
            if norm_origin in st_idx and norm_dest in st_idx:
                idx_orig = st_idx[norm_origin]
                idx_dest = st_idx[norm_dest]
                if idx_orig < idx_dest:
                    stop_orig = stops[idx_orig]
                    stop_dest = stops[idx_dest]
                    if stop_orig["datetime"] >= departure_time:
                        duration = int((stop_dest["datetime"] - stop_orig["datetime"]).total_seconds() // 60)
                        conn_id = f"CONN-{run['line'].replace(' ', '')}-{stop_orig['datetime'].strftime('%H%M')}"
                        leg = JourneyLeg(
                            line=run["line"],
                            from_station=norm_origin,
                            to_station=norm_dest,
                            departure_time=stop_orig["datetime"].isoformat(),
                            arrival_time=stop_dest["datetime"].isoformat(),
                            platform=stop_orig["platform"],
                        )
                        candidate_connections.append(
                            TransportJourney(
                                connection_id=conn_id,
                                origin=norm_origin,
                                destination=norm_dest,
                                departure_time=stop_orig["datetime"].isoformat(),
                                arrival_time=stop_dest["datetime"].isoformat(),
                                duration_minutes=duration,
                                transfers=0,
                                legs=[leg],
                            )
                        )

        # 1-transfer connections
        junctions = [s for s in self.SUPPORTED_STATIONS if s != norm_origin and s != norm_dest]
        for junction in junctions:
            leg1_candidates = []
            for run in runs:
                stops = run["stops"]
                st_idx = {s["station"]: i for i, s in enumerate(stops)}
                if norm_origin in st_idx and junction in st_idx:
                    i1, i2 = st_idx[norm_origin], st_idx[junction]
                    if i1 < i2 and stops[i1]["datetime"] >= departure_time:
                        leg1_candidates.append((run["line"], stops[i1], stops[i2]))

            leg2_candidates = []
            for run in runs:
                stops = run["stops"]
                st_idx = {s["station"]: i for i, s in enumerate(stops)}
                if junction in st_idx and norm_dest in st_idx:
                    i1, i2 = st_idx[junction], st_idx[norm_dest]
                    if i1 < i2:
                        leg2_candidates.append((run["line"], stops[i1], stops[i2]))

            for line1, stop_orig, stop_junc1 in leg1_candidates:
                arr_junc = stop_junc1["datetime"]
                for line2, stop_junc2, stop_dest in leg2_candidates:
                    dep_junc = stop_junc2["datetime"]
                    transfer_mins = int((dep_junc - arr_junc).total_seconds() // 60)
                    if 5 <= transfer_mins <= 60:
                        total_dur = int((stop_dest["datetime"] - stop_orig["datetime"]).total_seconds() // 60)
                        conn_id = (
                            f"CONN-{line1.replace(' ', '')}-{line2.replace(' ', '')}-"
                            f"{stop_orig['datetime'].strftime('%H%M')}"
                        )
                        leg1 = JourneyLeg(
                            line=line1,
                            from_station=norm_origin,
                            to_station=junction,
                            departure_time=stop_orig["datetime"].isoformat(),
                            arrival_time=stop_junc1["datetime"].isoformat(),
                            platform=stop_orig["platform"],
                        )
                        leg2 = JourneyLeg(
                            line=line2,
                            from_station=junction,
                            to_station=norm_dest,
                            departure_time=stop_junc2["datetime"].isoformat(),
                            arrival_time=stop_dest["datetime"].isoformat(),
                            platform=stop_junc2["platform"],
                        )
                        candidate_connections.append(
                            TransportJourney(
                                connection_id=conn_id,
                                origin=norm_origin,
                                destination=norm_dest,
                                departure_time=stop_orig["datetime"].isoformat(),
                                arrival_time=stop_dest["datetime"].isoformat(),
                                duration_minutes=total_dur,
                                transfers=1,
                                legs=[leg1, leg2],
                            )
                        )

        # Pareto dominance pruning
        def _is_dominated(c1: TransportJourney, c2: TransportJourney) -> bool:
            dep1, dep2 = c1.departure_time, c2.departure_time
            arr1, arr2 = c1.arrival_time, c2.arrival_time
            tr1, tr2 = c1.transfers, c2.transfers
            if dep2 >= dep1 and arr2 <= arr1 and tr2 <= tr1:
                if dep2 > dep1 or arr2 < arr1 or tr2 < tr1:
                    return True
            return False

        pareto_conns = [
            c for c in candidate_connections
            if not any(_is_dominated(c, other) for other in candidate_connections if other is not c)
        ]

        pareto_conns.sort(
            key=lambda c: (c.departure_time, c.duration_minutes, c.transfers)
        )

        seen_keys = set()
        unique_conns: list[TransportJourney] = []
        for c in pareto_conns:
            key = (c.departure_time, c.arrival_time, c.transfers)
            if key not in seen_keys:
                seen_keys.add(key)
                unique_conns.append(c)

        return unique_conns[:max_results]


__all__ = [
    "TransportProvider",
    "SyntheticTransportProvider",
    "LiveTransportProvider",
]


def __getattr__(name: str) -> Any:
    if name == "LiveTransportProvider":
        from harness.mcp.servers.transport_live import LiveTransportProvider
        return LiveTransportProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
