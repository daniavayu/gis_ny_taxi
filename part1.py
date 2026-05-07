# Part 1 Step by Step

# 1. Loading NYC taxi trip data
# 2. Loading tourist locations
# 3. Converting both into spatial coordinates
# 4. Classifying trips as: tourist-related vs. non-tourist
# 5. Computing: trip distance and speed
# 6. Comparing average speeds between groups

import json
from pathlib import Path
import duckdb

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = BASE_DIR / "taxi_analysis.duckdb"

# Constants
FEET_PER_MILE = 5280.0
TOURIST_THRESHOLD_FEET = 0.1 * FEET_PER_MILE
MAX_SPEED_MPH = 100

# Make DuckDB's spatial functions available
def load_spatial(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("INSTALL spatial;")
    conn.execute("LOAD spatial;")

# Connect to the DuckDB database and loads the spatial extension.
# Create the taxi_trips table from the CSV and tourist_spots from GeoJson
def main() -> None:
    print("Part 1: Tourist vs. non-tourist taxi trip speeds")

    # Opens/creates database and loads spatial features to it
    conn = duckdb.connect(str(DB_PATH))
    load_spatial(conn)

    # Load taxi trip data into a table in DB
    conn.execute(
        f"""
        CREATE OR REPLACE TABLE taxi_trips AS
        SELECT *
        FROM read_csv_auto('{DATA_DIR / "nyc_taxi_data.csv"}', header=true)
        """
    )

    # Load tourist spots into python dictionary
    with open(DATA_DIR / "tourist_locations-1.geojson") as f:
        tourist_locations = json.load(f)

    # Create new table with tourist spots in DB
    conn.execute("DROP TABLE IF EXISTS tourist_spots")
    conn.execute(
        """
        CREATE TABLE tourist_spots (
            name VARCHAR,
            geom GEOMETRY
        )
        """
    )

    # Loop through each tourist location and extract coordinates
    for feature in tourist_locations["features"]:
        longitude, latitude = feature["geometry"]["coordinates"]
        # Insert tourist spot name, geometry point, transformed point
        conn.execute(
            """
            INSERT INTO tourist_spots
            VALUES (
                ?,
                ST_Transform(
                    ST_Point(?, ?),
                    'EPSG:4326',
                    'EPSG:2263',
                    always_xy := true
                )
            )
            """,
            [feature["properties"].get("Tourist_Spot", ""), longitude, latitude],
        )

    conn.execute(
        """
        CREATE OR REPLACE TABLE part1_trip_speeds AS
        WITH trip_geometries AS (
            SELECT
                id,
                trip_duration,
                ST_Transform(
                    ST_Point(pickup_longitude, pickup_latitude),
                    'EPSG:4326',
                    'EPSG:2263',
                    always_xy := true
                ) AS pickup_geom,
                ST_Transform(
                    ST_Point(dropoff_longitude, dropoff_latitude),
                    'EPSG:4326',
                    'EPSG:2263',
                    always_xy := true
                ) AS dropoff_geom
            FROM taxi_trips
            WHERE
                pickup_longitude IS NOT NULL
                AND pickup_latitude IS NOT NULL
                AND dropoff_longitude IS NOT NULL
                AND dropoff_latitude IS NOT NULL
                AND trip_duration > 0
        ),
        classified AS (
            SELECT
                *,
                CASE
                    WHEN EXISTS (
                        SELECT 1
                        FROM tourist_spots
                        WHERE ST_Distance(pickup_geom, geom) <= ?
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM tourist_spots
                        WHERE ST_Distance(dropoff_geom, geom) <= ?
                    )
                    THEN 'tourist'
                    ELSE 'non-tourist'
                END AS location
            FROM trip_geometries
        )
        SELECT
            id,
            location,
            ST_Distance(pickup_geom, dropoff_geom) / ? AS distance_miles,
            (ST_Distance(pickup_geom, dropoff_geom) / ?) / (trip_duration / 3600.0) AS speed_mph
        FROM classified
        """,
        [TOURIST_THRESHOLD_FEET, TOURIST_THRESHOLD_FEET, FEET_PER_MILE, FEET_PER_MILE],
    )

    result = conn.execute(
        """
        SELECT
            location,
            AVG(speed_mph) AS average_mph
        FROM part1_trip_speeds
        WHERE speed_mph > 0 AND speed_mph <= ?
        GROUP BY location
        ORDER BY CASE WHEN location = 'tourist' THEN 0 ELSE 1 END
        """,
        [MAX_SPEED_MPH],
    ).fetchdf()

    output_path = BASE_DIR / "part1.csv"
    result.to_csv(output_path, index=False)
    print(result)
    print(f"Wrote {output_path}")

    conn.close()

if __name__ == "__main__":
    main()
