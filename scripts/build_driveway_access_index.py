#!/usr/bin/env python3

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import osmium


ROUTABLE_HIGHWAYS = {
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "unclassified",
    "residential",
    "living_street",
    "service",
    "road",
}

BLOCKED_ACCESS = {"no", "private"}


@dataclass(frozen=True)
class Endpoint:
    node_id: int
    lon: float
    lat: float


class DrivewayHandler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.driveways = {}

    def way(self, way):
        if (
            way.tags.get("highway") != "service"
            or way.tags.get("service") != "driveway"
            or len(way.nodes) < 2
        ):
            return

        first = way.nodes[0]
        last = way.nodes[-1]

        if not first.location.valid() or not last.location.valid():
            return

        self.driveways[way.id] = (
            Endpoint(
                node_id=first.ref,
                lon=first.location.lon,
                lat=first.location.lat,
            ),
            Endpoint(
                node_id=last.ref,
                lon=last.location.lon,
                lat=last.location.lat,
            ),
        )


class RoadConnectionHandler(osmium.SimpleHandler):
    def __init__(self, endpoint_ids):
        super().__init__()
        self.endpoint_ids = endpoint_ids
        self.connected_endpoint_ids = set()

    def way(self, way):
        highway = way.tags.get("highway")

        if highway not in ROUTABLE_HIGHWAYS:
            return

        # driveway 自身不能被当作公共道路连接
        if way.tags.get("service") == "driveway":
            return

        for access_key in ("motorcar", "motor_vehicle", "vehicle", "access"):
            access_value = way.tags.get(access_key)

            if access_value:
                if access_value in BLOCKED_ACCESS:
                    return
                break

        for node in way.nodes:
            if node.ref in self.endpoint_ids:
                self.connected_endpoint_ids.add(node.ref)


def build_index(input_path, output_path):
    driveway_handler = DrivewayHandler()
    driveway_handler.apply_file(str(input_path), locations=True)

    endpoint_ids = set()

    for first, last in driveway_handler.driveways.values():
        endpoint_ids.add(first.node_id)
        endpoint_ids.add(last.node_id)

    road_handler = RoadConnectionHandler(endpoint_ids)
    road_handler.apply_file(str(input_path))

    output_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    disconnected = 0
    connected_at_both_ends = 0

    with output_path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.writer(output_file)

        writer.writerow(
            [
                "driveway_way_id",
                "approach_node_id",
                "approach_lon",
                "approach_lat",
                "access_node_id",
                "access_lon",
                "access_lat",
            ]
        )

        for way_id, (first, last) in driveway_handler.driveways.items():
            first_connected = (
                first.node_id in road_handler.connected_endpoint_ids
            )
            last_connected = (
                last.node_id in road_handler.connected_endpoint_ids
            )

            if first_connected == last_connected:
                if first_connected:
                    connected_at_both_ends += 1
                else:
                    disconnected += 1
                continue

            if first_connected:
                access = first
                approach = last
            else:
                access = last
                approach = first

            writer.writerow(
                [
                    way_id,
                    approach.node_id,
                    approach.lon,
                    approach.lat,
                    access.node_id,
                    access.lon,
                    access.lat,
                ]
            )

            written += 1

    print(f"Driveways found: {len(driveway_handler.driveways)}")
    print(f"Access records written: {written}")
    print(f"Disconnected driveways skipped: {disconnected}")
    print(f"Driveways connected at both ends skipped: {connected_at_both_ends}")
    print(f"Output: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Build driveway-to-road access-point index from an OSM PBF."
    )

    parser.add_argument("input_pbf", type=Path)
    parser.add_argument("output_csv", type=Path)

    args = parser.parse_args()
    build_index(args.input_pbf, args.output_csv)


if __name__ == "__main__":
    main()