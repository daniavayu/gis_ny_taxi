# Part 2 Step by Step
# 1. Loading spatial datasets: taxi trips (points → lines), neighborhoods (polygons)
# 2. Converting everything to same coordinate system (EPSG 2263)
# 3. Turning trips into lines
# 4. Finding which neighborhoods each trip crosses
# 5. Computing: trip speed and average speed per neighborhood
# 6. Visualizing results on a choropleth map

import json
from pathlib import Path
import duckdb
import folium

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = BASE_DIR / "taxi_analysis.duckdb"

# Constants
SHAPEFILE_PATH = DATA_DIR / "ZillowNeighborhoods-NY.shp"
FEET_PER_MILE = 5280.0
MAX_SPEED_MPH = 100
SECONDS_PER_HOUR = 3600.0

def load_spatial(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("INSTALL spatial;")
    conn.execute("LOAD spatial;")

def main() -> None:
    print("Part 2: Average taxi speed by crossed neighborhood")

    conn = duckdb.connect(str(DB_PATH))
    load_spatial(conn)

    conn.execute(
        f"""
        CREATE OR REPLACE TABLE taxi_trips AS
        SELECT *
        FROM read_csv_auto('{DATA_DIR / "nyc_taxi_data.csv"}', header=true)
        """
    )

    # Create neighborhoods table from shapefile, eliminates duplicated neighborhoods
    conn.execute(
        f"""
        CREATE OR REPLACE TABLE neighborhoods_2263 AS
        WITH raw_neighborhoods AS (
            SELECT
                Name,
                City,
                ST_Transform(geom, 'EPSG:4326', 'EPSG:2263', always_xy := true) AS geom,
                ROW_NUMBER() OVER (PARTITION BY Name ORDER BY Name) AS name_rank
            FROM ST_Read('{SHAPEFILE_PATH}')
            WHERE City = 'New York'
        )
        SELECT
            Name AS neighborhood,
            geom
        FROM raw_neighborhoods
        WHERE name_rank = 1
        """
    )

    # Create trip lines (converts points into straight line)
    conn.execute(
        """
        CREATE OR REPLACE TABLE trip_lines_2263 AS
        SELECT
            id,
            trip_duration,
            ST_MakeLine(
                ST_Transform(
                    ST_Point(pickup_longitude, pickup_latitude),
                    'EPSG:4326',
                    'EPSG:2263',
                    always_xy := true
                ),
                ST_Transform(
                    ST_Point(dropoff_longitude, dropoff_latitude),
                    'EPSG:4326',
                    'EPSG:2263',
                    always_xy := true
                )
            ) AS geom
        FROM taxi_trips
        WHERE
            pickup_longitude IS NOT NULL
            AND pickup_latitude IS NOT NULL
            AND dropoff_longitude IS NOT NULL
            AND dropoff_latitude IS NOT NULL
            AND trip_duration > 0
        """
    )

    # Creates table with trip's speed by computing length of line (in miles) divided by time of trip and converted to hours. 
    # Distance (miles), time (hours) = miles per hour
    conn.execute(
        """
        CREATE OR REPLACE TABLE trip_speeds_2263 AS
        SELECT
            id,
            geom,
            (ST_Length(geom) / ?) / (trip_duration / ? ) AS speed_mph
        FROM trip_lines_2263
        """,
        [FEET_PER_MILE,SECONDS_PER_HOUR],
    )

    # Table using a spatial join (intersection between geometry of trip and geom of neighborhood)
    # A trip belongs to a neighborhood if its line intersects neighborhood's polygon
    conn.execute(
        """
        CREATE OR REPLACE TABLE part2_neighborhood_speeds AS
        SELECT
            n.neighborhood,
            AVG(t.speed_mph) AS average_mph
        FROM trip_speeds_2263 AS t
        INNER JOIN neighborhoods_2263 AS n
            ON ST_Intersects(t.geom, n.geom)
        WHERE t.speed_mph > 0 AND t.speed_mph <= ?
        GROUP BY n.neighborhood
        ORDER BY average_mph ASC
        """,
        [MAX_SPEED_MPH],
    )

    part2 = conn.execute("SELECT * FROM part2_neighborhood_speeds").fetchdf()

    part2_path = BASE_DIR / "part2.csv"
    part2.to_csv(part2_path, index=False)
    print(part2.head(10))
    print(f"Wrote {part2_path}")

    # Build dataset for map visualization
    # Convert SQL geometry to JSON format (usable for web map)
    # Transform coordinate system back because Folium expects lat/lon format
    map_rows = conn.execute(
        """
        SELECT
            n.neighborhood,
            p.average_mph,
            ST_AsGeoJSON(
                ST_Transform(n.geom, 'EPSG:2263', 'EPSG:4326', always_xy := true)
            ) AS geometry
        FROM neighborhoods_2263 AS n
        LEFT JOIN part2_neighborhood_speeds AS p USING (neighborhood)
        """
    ).fetchall()

    # Create GeoJson    
    features = [
        {
            "type": "Feature",
            "properties": {
                "neighborhood": neighborhood,
                "average_mph": average_mph,
            },
            "geometry": json.loads(geometry),
        }
        for neighborhood, average_mph, geometry in map_rows
    ]
    geojson = {"type": "FeatureCollection", "features": features}

    # Create map
    speed_map = folium.Map(location=[40.7128, -74.0060], zoom_start=11, tiles="CartoDB positron")
    folium.Choropleth(
        geo_data=geojson,
        name="Average speed",
        data=part2,
        columns=["neighborhood", "average_mph"],
        key_on="feature.properties.neighborhood",
        fill_color="RdYlGn",
        fill_opacity=0.75,
        line_opacity=0.25,
        legend_name="Average taxi speed (mph)",
        nan_fill_color="lightgray",
    ).add_to(speed_map)
    folium.GeoJson(
        geojson,
        name="Neighborhoods",
        tooltip=folium.GeoJsonTooltip(
            fields=["neighborhood", "average_mph"],
            aliases=["Neighborhood", "Average mph"],
            localize=True,
        ),
        style_function=lambda feature: {
            "fillOpacity": 0,
            "color": "#333333",
            "weight": 0.4,
        },
    ).add_to(speed_map)
    folium.LayerControl().add_to(speed_map)

    map_path = BASE_DIR / "nyc_neighborhoods_speed_choropleth.html"
    speed_map.save(map_path)
    print(f"Wrote {map_path}")

    conn.close()


if __name__ == "__main__":
    main()
