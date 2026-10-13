#!/usr/bin/env python3
"""Find address points likely separated from a nearby major road by a barrier.

This script targets a specific wrong-road snapping pattern:

    major road / highway
    wall, fence, or no direct access
    addressed residential building
    driveway -> internal neighborhood road

It reads addressed nodes/buildings, major-road node membership, and mapped
barrier geometry from an OSM PBF. Address points are associated with nearby
driveway approach/access records. Each point is then queried against a baseline
OSRM server and an access-aware resolver server.

The output is a shortlist for human review. "mapped_barrier=no" does not mean
that no real wall exists; it only means that the OSM PBF contains no intersecting
barrier geometry for the address-to-baseline-snap line.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import DefaultDict, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

try:
    import osmium
except ImportError as error:  # pragma: no cover - depends on the user's machine
    raise SystemExit(
        "pyosmium is required. Install the Python package that provides "
        "'import osmium'."
    ) from error


EARTH_RADIUS_M = 6_371_000.0
METERS_PER_LATITUDE_DEGREE = 111_320.0

MAJOR_ROAD_PRIORITY = {
    "motorway": 8,
    "motorway_link": 7,
    "trunk": 6,
    "trunk_link": 5,
    "primary": 4,
    "primary_link": 3,
    "secondary": 2,
    "secondary_link": 1,
}

BARRIER_TYPES = {
    "wall",
    "fence",
    "retaining_wall",
    "city_wall",
    "hedge",
}


@dataclass(frozen=True)
class AccessRecord:
    driveway_way_id: int
    approach_node_id: int
    approach_lon: float
    approach_lat: float
    access_node_id: int
    access_lon: float
    access_lat: float


@dataclass(frozen=True)
class AddressRecord:
    osm_type: str
    osm_id: int
    house_number: str
    street: str
    city: str
    postcode: str
    lon: float
    lat: float
    access: AccessRecord
    address_to_approach_m: float


@dataclass(frozen=True)
class Snap:
    name: str
    distance: float
    lon: float
    lat: float
    nodes: Tuple[int, ...]


def parse_bbox(value: str) -> Tuple[float, float, float, float]:
    try:
        numbers = tuple(float(item.strip()) for item in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "bbox must be min_lon,min_lat,max_lon,max_lat"
        ) from error
    if len(numbers) != 4:
        raise argparse.ArgumentTypeError("bbox must contain four numbers")
    min_lon, min_lat, max_lon, max_lat = numbers
    if min_lon >= max_lon or min_lat >= max_lat:
        raise argparse.ArgumentTypeError("bbox minimums must be smaller than maximums")
    return min_lon, min_lat, max_lon, max_lat


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Find addressed residential points that baseline OSRM snaps to a "
            "nearby major road while the resolver selects the internal street."
        )
    )
    parser.add_argument("pbf", type=Path, help="Source OSM PBF")
    parser.add_argument("access_csv", type=Path, help="*.osrm.access.csv sidecar")
    parser.add_argument(
        "--baseline-url", default="http://127.0.0.1:5001"
    )
    parser.add_argument(
        "--resolver-url", default="http://127.0.0.1:5000"
    )
    parser.add_argument(
        "--bbox",
        type=parse_bbox,
        help="Optional min_lon,min_lat,max_lon,max_lat region filter",
    )
    parser.add_argument(
        "--address-sample-size",
        type=int,
        default=20_000,
        help="Reservoir sample after address-to-driveway association; 0 keeps all",
    )
    parser.add_argument("--seed", type=int, default=20260911)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--max-address-approach-distance",
        type=float,
        default=40.0,
    )
    parser.add_argument(
        "--min-baseline-distance",
        type=float,
        default=5.0,
    )
    parser.add_argument(
        "--max-baseline-distance",
        type=float,
        default=75.0,
    )
    parser.add_argument(
        "--min-snap-separation",
        type=float,
        default=10.0,
    )
    parser.add_argument(
        "--access-tolerance",
        type=float,
        default=5.0,
    )
    parser.add_argument(
        "--require-mapped-barrier",
        action="store_true",
        help="Keep only lines that intersect barrier=wall/fence/etc. in OSM",
    )
    parser.add_argument(
        "--allow-resolver-street-mismatch",
        action="store_true",
        help="Do not require the resolver road name to match addr:street",
    )
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("highway_wall_snapping_candidates.csv"),
    )
    return parser.parse_args()


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    value = (
        math.sin(dp / 2.0) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_M * math.asin(math.sqrt(value))


def normalize_name(name: str) -> str:
    replacements = {
        "street": "st",
        "road": "rd",
        "avenue": "ave",
        "boulevard": "blvd",
        "drive": "dr",
        "court": "ct",
        "circle": "cir",
        "lane": "ln",
        "highway": "hwy",
        "parkway": "pkwy",
        "place": "pl",
        "trail": "trl",
        "way": "way",
    }
    tokens: List[str] = []
    token = ""
    for character in name.lower():
        if character.isalnum():
            token += character
        elif token:
            tokens.append(replacements.get(token, token))
            token = ""
    if token:
        tokens.append(replacements.get(token, token))
    return "".join(tokens)


def road_matches_street(road_name: str, street: str) -> bool:
    expected = normalize_name(street)
    if not expected:
        return False
    alternatives = [part.strip() for part in road_name.split("/")]
    return any(normalize_name(part) == expected for part in alternatives)


def inside_bbox(
    lon: float,
    lat: float,
    bbox: Optional[Tuple[float, float, float, float]],
) -> bool:
    if bbox is None:
        return True
    min_lon, min_lat, max_lon, max_lat = bbox
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat


class AccessGrid:
    def __init__(self, cell_size: float = 0.002) -> None:
        self.cell_size = cell_size
        self.records: List[AccessRecord] = []
        self.cells: DefaultDict[Tuple[int, int], List[int]] = defaultdict(list)

    def _cell(self, lon: float, lat: float) -> Tuple[int, int]:
        return math.floor(lon / self.cell_size), math.floor(lat / self.cell_size)

    def add(self, record: AccessRecord) -> None:
        index = len(self.records)
        self.records.append(record)
        self.cells[self._cell(record.approach_lon, record.approach_lat)].append(index)

    def nearest(
        self, lon: float, lat: float, maximum_distance: float
    ) -> Optional[Tuple[AccessRecord, float]]:
        center_x, center_y = self._cell(lon, lat)
        cos_lat = max(0.2, math.cos(math.radians(lat)))
        lon_degrees = maximum_distance / (METERS_PER_LATITUDE_DEGREE * cos_lat)
        lat_degrees = maximum_distance / METERS_PER_LATITUDE_DEGREE
        dx = max(1, math.ceil(lon_degrees / self.cell_size))
        dy = max(1, math.ceil(lat_degrees / self.cell_size))

        best: Optional[AccessRecord] = None
        best_distance = maximum_distance
        for x in range(center_x - dx, center_x + dx + 1):
            for y in range(center_y - dy, center_y + dy + 1):
                for index in self.cells.get((x, y), []):
                    record = self.records[index]
                    distance = haversine_m(
                        lon,
                        lat,
                        record.approach_lon,
                        record.approach_lat,
                    )
                    if distance <= best_distance:
                        best = record
                        best_distance = distance
        if best is None:
            return None
        return best, best_distance


class BarrierGrid:
    def __init__(self, cell_size: float = 0.002) -> None:
        self.cell_size = cell_size
        self.segments: List[Tuple[float, float, float, float, str, int]] = []
        self.cells: DefaultDict[Tuple[int, int], List[int]] = defaultdict(list)

    def _cell(self, lon: float, lat: float) -> Tuple[int, int]:
        return math.floor(lon / self.cell_size), math.floor(lat / self.cell_size)

    def add_segment(
        self,
        lon1: float,
        lat1: float,
        lon2: float,
        lat2: float,
        barrier_type: str,
        way_id: int,
    ) -> None:
        index = len(self.segments)
        self.segments.append((lon1, lat1, lon2, lat2, barrier_type, way_id))
        min_x, min_y = self._cell(min(lon1, lon2), min(lat1, lat2))
        max_x, max_y = self._cell(max(lon1, lon2), max(lat1, lat2))
        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                self.cells[(x, y)].append(index)

    @staticmethod
    def _orientation(
        ax: float,
        ay: float,
        bx: float,
        by: float,
        cx: float,
        cy: float,
    ) -> float:
        return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)

    @classmethod
    def _intersects(
        cls,
        a1x: float,
        a1y: float,
        a2x: float,
        a2y: float,
        b1x: float,
        b1y: float,
        b2x: float,
        b2y: float,
    ) -> bool:
        o1 = cls._orientation(a1x, a1y, a2x, a2y, b1x, b1y)
        o2 = cls._orientation(a1x, a1y, a2x, a2y, b2x, b2y)
        o3 = cls._orientation(b1x, b1y, b2x, b2y, a1x, a1y)
        o4 = cls._orientation(b1x, b1y, b2x, b2y, a2x, a2y)
        epsilon = 1e-12
        return (
            (o1 > epsilon and o2 < -epsilon or o1 < -epsilon and o2 > epsilon)
            and (o3 > epsilon and o4 < -epsilon or o3 < -epsilon and o4 > epsilon)
        )

    def first_intersection(
        self, lon1: float, lat1: float, lon2: float, lat2: float
    ) -> Optional[Tuple[str, int]]:
        min_x, min_y = self._cell(min(lon1, lon2), min(lat1, lat2))
        max_x, max_y = self._cell(max(lon1, lon2), max(lat1, lat2))
        candidates: Set[int] = set()
        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                candidates.update(self.cells.get((x, y), []))
        for index in candidates:
            b1x, b1y, b2x, b2y, barrier_type, way_id = self.segments[index]
            if self._intersects(lon1, lat1, lon2, lat2, b1x, b1y, b2x, b2y):
                return barrier_type, way_id
        return None


def load_access_grid(path: Path) -> AccessGrid:
    grid = AccessGrid()
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        for row in reader:
            grid.add(
                AccessRecord(
                    driveway_way_id=int(row["driveway_way_id"]),
                    approach_node_id=int(row["approach_node_id"]),
                    approach_lon=float(row["approach_lon"]),
                    approach_lat=float(row["approach_lat"]),
                    access_node_id=int(row["access_node_id"]),
                    access_lon=float(row["access_lon"]),
                    access_lat=float(row["access_lat"]),
                )
            )
    return grid


class CandidateHandler(osmium.SimpleHandler):
    def __init__(
        self,
        access_grid: AccessGrid,
        barrier_grid: BarrierGrid,
        bbox: Optional[Tuple[float, float, float, float]],
        max_address_approach_distance: float,
        sample_size: int,
        seed: int,
    ) -> None:
        super().__init__()
        self.access_grid = access_grid
        self.barrier_grid = barrier_grid
        self.bbox = bbox
        self.max_address_approach_distance = max_address_approach_distance
        self.sample_size = sample_size
        self.random = random.Random(seed)
        self.addresses: List[AddressRecord] = []
        self.addresses_seen = 0
        self.seen_address_keys: Set[Tuple[str, str, int, int]] = set()
        self.major_nodes: Dict[int, Tuple[str, str]] = {}
        self.major_names: Dict[str, Tuple[str, str]] = {}
        self.node_count = 0
        self.way_count = 0

    def _store_address(
        self,
        osm_type: str,
        osm_id: int,
        lon: float,
        lat: float,
        tags: object,
    ) -> None:
        if not inside_bbox(lon, lat, self.bbox):
            return
        house_number = str(tags.get("addr:housenumber") or "").strip()
        street = str(tags.get("addr:street") or "").strip()
        if not house_number or not street:
            return

        key = (
            house_number.lower(),
            normalize_name(street),
            round(lon * 100_000),
            round(lat * 100_000),
        )
        if key in self.seen_address_keys:
            return
        self.seen_address_keys.add(key)

        match = self.access_grid.nearest(
            lon, lat, self.max_address_approach_distance
        )
        if match is None:
            return
        access, distance = match
        address = AddressRecord(
            osm_type=osm_type,
            osm_id=osm_id,
            house_number=house_number,
            street=street,
            city=str(tags.get("addr:city") or "").strip(),
            postcode=str(tags.get("addr:postcode") or "").strip(),
            lon=lon,
            lat=lat,
            access=access,
            address_to_approach_m=distance,
        )

        self.addresses_seen += 1
        if self.sample_size == 0 or len(self.addresses) < self.sample_size:
            self.addresses.append(address)
            return
        replacement = self.random.randint(0, self.addresses_seen - 1)
        if replacement < self.sample_size:
            self.addresses[replacement] = address

    def node(self, node: object) -> None:
        self.node_count += 1
        if self.node_count % 5_000_000 == 0:
            print(
                f"PBF scan: nodes={self.node_count:,}; "
                f"address/driveway matches={self.addresses_seen:,}",
                file=sys.stderr,
            )
        try:
            lon = float(node.location.lon)
            lat = float(node.location.lat)
        except Exception:
            return
        self._store_address("node", int(node.id), lon, lat, node.tags)

    def way(self, way: object) -> None:
        self.way_count += 1
        highway = str(way.tags.get("highway") or "")
        if highway in MAJOR_ROAD_PRIORITY:
            name = str(way.tags.get("name") or way.tags.get("ref") or "")
            normalized = normalize_name(name)
            if normalized:
                existing = self.major_names.get(normalized)
                if (
                    existing is None
                    or MAJOR_ROAD_PRIORITY[highway]
                    > MAJOR_ROAD_PRIORITY[existing[0]]
                ):
                    self.major_names[normalized] = (highway, name)
            for node_ref in way.nodes:
                node_id = int(node_ref.ref)
                existing = self.major_nodes.get(node_id)
                if (
                    existing is None
                    or MAJOR_ROAD_PRIORITY[highway]
                    > MAJOR_ROAD_PRIORITY[existing[0]]
                ):
                    self.major_nodes[node_id] = (highway, name)

        coordinates: List[Tuple[float, float]] = []
        needs_geometry = (
            str(way.tags.get("barrier") or "") in BARRIER_TYPES
            or bool(way.tags.get("addr:housenumber"))
        )
        if needs_geometry:
            for node_ref in way.nodes:
                try:
                    if node_ref.location.valid():
                        coordinates.append(
                            (float(node_ref.location.lon), float(node_ref.location.lat))
                        )
                except Exception:
                    continue

        barrier_type = str(way.tags.get("barrier") or "")
        if barrier_type in BARRIER_TYPES and len(coordinates) >= 2:
            for first, second in zip(coordinates, coordinates[1:]):
                if inside_bbox(first[0], first[1], self.bbox) or inside_bbox(
                    second[0], second[1], self.bbox
                ):
                    self.barrier_grid.add_segment(
                        first[0],
                        first[1],
                        second[0],
                        second[1],
                        barrier_type,
                        int(way.id),
                    )

        if way.tags.get("addr:housenumber") and coordinates:
            if len(coordinates) > 1 and coordinates[0] == coordinates[-1]:
                coordinates = coordinates[:-1]
            lon = sum(item[0] for item in coordinates) / len(coordinates)
            lat = sum(item[1] for item in coordinates) / len(coordinates)
            self._store_address("way", int(way.id), lon, lat, way.tags)

    def classify_major_snap(self, snap: Snap) -> Optional[Tuple[str, str]]:
        best: Optional[Tuple[str, str]] = None
        for node_id in snap.nodes:
            classification = self.major_nodes.get(node_id)
            if classification is None:
                continue
            if (
                best is None
                or MAJOR_ROAD_PRIORITY[classification[0]]
                > MAJOR_ROAD_PRIORITY[best[0]]
            ):
                best = classification
        if best is not None:
            return best
        return self.major_names.get(normalize_name(snap.name))


def nearest(base_url: str, lon: float, lat: float, timeout: float) -> Snap:
    url = (
        base_url.rstrip("/")
        + f"/nearest/v1/driving/{lon:.7f},{lat:.7f}?number=1"
    )
    try:
        with urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(f"request failed: {url}: {error}") from error
    if payload.get("code") != "Ok" or not payload.get("waypoints"):
        raise RuntimeError(f"OSRM response is not Ok: {url}: {payload}")
    waypoint = payload["waypoints"][0]
    location = waypoint["location"]
    return Snap(
        name=waypoint.get("name") or "",
        distance=float(waypoint.get("distance", 0.0)),
        lon=float(location[0]),
        lat=float(location[1]),
        nodes=tuple(int(node) for node in waypoint.get("nodes", []) if int(node) != 0),
    )


def evaluate(
    address: AddressRecord,
    handler: CandidateHandler,
    args: argparse.Namespace,
) -> Optional[Dict[str, object]]:
    baseline = nearest(args.baseline_url, address.lon, address.lat, args.timeout)
    if baseline.distance < args.min_baseline_distance:
        return None
    if baseline.distance > args.max_baseline_distance:
        return None

    major = handler.classify_major_snap(baseline)
    if major is None:
        return None
    major_class, major_osm_name = major
    if road_matches_street(baseline.name, address.street):
        return None

    resolver = nearest(args.resolver_url, address.lon, address.lat, args.timeout)
    if not args.allow_resolver_street_mismatch and not road_matches_street(
        resolver.name, address.street
    ):
        return None

    resolver_to_access = haversine_m(
        resolver.lon,
        resolver.lat,
        address.access.access_lon,
        address.access.access_lat,
    )
    if resolver_to_access > args.access_tolerance:
        return None

    snap_separation = haversine_m(
        baseline.lon, baseline.lat, resolver.lon, resolver.lat
    )
    if snap_separation < args.min_snap_separation:
        return None

    barrier = handler.barrier_grid.first_intersection(
        address.lon,
        address.lat,
        baseline.lon,
        baseline.lat,
    )
    if args.require_mapped_barrier and barrier is None:
        return None

    mapped_barrier = "yes" if barrier else "no"
    barrier_type = barrier[0] if barrier else ""
    barrier_way_id = barrier[1] if barrier else ""
    score = (
        (2000.0 if barrier else 0.0)
        + 200.0 * MAJOR_ROAD_PRIORITY[major_class]
        + min(300.0, snap_separation)
        + max(0.0, 100.0 - baseline.distance)
    )
    full_address = " ".join(
        item
        for item in [
            address.house_number,
            address.street,
            address.city,
            address.postcode,
        ]
        if item
    )

    return {
        "score": round(score, 3),
        "address": full_address,
        "query_lon": address.lon,
        "query_lat": address.lat,
        "address_osm_type": address.osm_type,
        "address_osm_id": address.osm_id,
        "addr_street": address.street,
        "major_road_class": major_class,
        "major_road_osm_name": major_osm_name,
        "baseline_name": baseline.name,
        "baseline_distance_m": round(baseline.distance, 3),
        "baseline_lon": baseline.lon,
        "baseline_lat": baseline.lat,
        "baseline_nodes": "|".join(str(node) for node in baseline.nodes),
        "resolver_name": resolver.name,
        "resolver_distance_m": round(resolver.distance, 3),
        "resolver_lon": resolver.lon,
        "resolver_lat": resolver.lat,
        "resolver_nodes": "|".join(str(node) for node in resolver.nodes),
        "snap_separation_m": round(snap_separation, 3),
        "mapped_barrier": mapped_barrier,
        "barrier_type": barrier_type,
        "barrier_way_id": barrier_way_id,
        "address_to_approach_m": round(address.address_to_approach_m, 3),
        "driveway_way_id": address.access.driveway_way_id,
        "approach_node_id": address.access.approach_node_id,
        "access_node_id": address.access.access_node_id,
        "access_lon": address.access.access_lon,
        "access_lat": address.access.access_lat,
        "resolver_to_indexed_access_m": round(resolver_to_access, 3),
        "osm_map_url": (
            f"https://www.openstreetmap.org/?mlat={address.lat}&mlon={address.lon}"
            f"#map=19/{address.lat}/{address.lon}"
        ),
        "google_maps_url": (
            "https://www.google.com/maps/search/?api=1&query="
            f"{address.lat},{address.lon}"
        ),
    }


def write_csv(path: Path, rows: Sequence[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def validate_args(args: argparse.Namespace) -> None:
    if args.baseline_url.rstrip("/") == args.resolver_url.rstrip("/"):
        raise ValueError("baseline-url and resolver-url must be different")
    if args.address_sample_size < 0 or args.workers < 1 or args.limit < 1:
        raise ValueError("sample size, workers, and limit must be valid positive values")
    if args.min_baseline_distance > args.max_baseline_distance:
        raise ValueError("minimum baseline distance exceeds maximum")


def main() -> int:
    args = parse_args()
    try:
        validate_args(args)
        # Fail early if either local service is unavailable.
        nearest(args.baseline_url, -77.6321978, 38.8010158, args.timeout)
        nearest(args.resolver_url, -77.6321978, 38.8010158, args.timeout)
        print("Loading driveway access records...", file=sys.stderr)
        access_grid = load_access_grid(args.access_csv)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(error, file=sys.stderr)
        return 1

    print(f"Access records loaded: {len(access_grid.records):,}", file=sys.stderr)
    barrier_grid = BarrierGrid()
    handler = CandidateHandler(
        access_grid=access_grid,
        barrier_grid=barrier_grid,
        bbox=args.bbox,
        max_address_approach_distance=args.max_address_approach_distance,
        sample_size=args.address_sample_size,
        seed=args.seed,
    )

    print("Scanning addresses, major roads, and barriers from PBF...", file=sys.stderr)
    try:
        handler.apply_file(str(args.pbf), locations=True, idx="flex_mem")
    except (OSError, RuntimeError) as error:
        print(f"PBF scan failed: {error}", file=sys.stderr)
        return 1

    print(
        f"Address/driveway matches seen: {handler.addresses_seen:,}\n"
        f"Addresses retained for OSRM comparison: {len(handler.addresses):,}\n"
        f"Major-road nodes indexed: {len(handler.major_nodes):,}\n"
        f"Barrier segments indexed: {len(barrier_grid.segments):,}",
        file=sys.stderr,
    )

    results: List[Dict[str, object]] = []
    failures = 0
    completed = 0
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(evaluate, address, handler, args): address
            for address in handler.addresses
        }
        for future in as_completed(futures):
            try:
                result = future.result()
                if result is not None:
                    results.append(result)
            except (RuntimeError, ValueError, KeyError):
                failures += 1
            with lock:
                completed += 1
                if completed % 250 == 0 or completed == len(handler.addresses):
                    print(
                        f"Compared {completed}/{len(handler.addresses)}; "
                        f"candidates={len(results)}; failures={failures}",
                        file=sys.stderr,
                    )

    results.sort(key=lambda row: float(row["score"]), reverse=True)
    selected = results[: args.limit]
    write_csv(args.output, selected)

    mapped_count = sum(1 for row in selected if row["mapped_barrier"] == "yes")
    print(f"Candidates found before limit: {len(results)}")
    print(f"Rows written: {len(selected)}")
    print(f"Rows with mapped wall/fence: {mapped_count}")
    print(f"Request failures: {failures}")
    print(f"Output: {args.output}")
    print("\nTop candidates:")
    for row in selected[:10]:
        print(
            f"  {row['address']} | {row['query_lat']},{row['query_lon']} | "
            f"{row['major_road_class']} {row['baseline_name']} -> "
            f"{row['resolver_name']} | barrier={row['mapped_barrier']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
