# Spatial development data

`regional_gazetteer.csv` contains 24 approximate, synthetic development landmarks
covering Uttarakhand, NCR and highway examples. IDs -10001..-10024 are reserved
fixture identifiers, not real GeoNames IDs. Coordinates are unsuitable for navigation.

`spatial_demo_reports.jsonl` supplies three clearly labeled `fixture:spatial_demo`
reports with precise landmark text, one explicit blocked-lane count, and no invented
total-lane count. These are separate from news and training annotations.

Load the booster with `python -m backend.spatial.gazetteer_loader --seed-regional`.
Seed reports explicitly with `python -m backend.spatial.demo`, then run the spatial
drain. Repeating either command does not duplicate rows or stream messages.
Original news fixtures with no identifiable landmark are intentionally unresolved.

GeoNames loader: `python -m backend.spatial.gazetteer_loader --file IN.txt`.
Optional `--admin1-file admin1CodesASCII.txt --admin2-file admin2Codes.txt` resolves
administrative codes into display names for location filtering. Without these,
codes are retained; built-in state aliases cover Uttarakhand/Delhi/Haryana/UP.
Files are streamed in configurable batches, committed per batch, and safely rerunnable.
Simplified CSV uses geoname_id,name,latitude,longitude plus optional ascii_name,
alternate_names (comma or pipe separated), admin1_state, admin2_district, city_name,
population, feature_class, feature_code, country_code (IN). Invalid rows are counted.
GeoNames data attribution/license: https://www.geonames.org/ and
https://download.geonames.org/export/dump/readme.txt (CC BY 4.0). No full dump is bundled.

Run `python -m backend.spatial.benchmark` for measured local SQL timings. Its report
explicitly identifies the dataset size and forced-index explain check. A small
fixture benchmark cannot establish performance or geocoding accuracy on 300k places.
Fuzzy confidence is a bounded heuristic score; statistical calibration remains future work.

Nominatim is disabled by default. Enabling it requires NOMINATIM_ENABLED=true;
the configured service sends the project User-Agent, countrycodes=in and address checks.
An async monotonic limiter plus a shared Redis start gate enforces at most 1Hz across
service instances; results write through to the local gazetteer. Standalone clients
without Redis must share one limiter. OpenStreetMap attribution is required for
displayed provider-derived data. Policy: https://operations.osmfoundation.org/policies/nominatim/

Spatial clustering uses locally centered metric AEQD geometry and DBSCAN at 500m,
with an inclusive maximum event-time span of 60 minutes, not fixed clock buckets.
Published report times define windows; compatibility uses exact incident category
and separates fixture/live sources. DBSCAN may connect a chain farther than 500m
end-to-end; each adjacency must satisfy the metric epsilon. A 90-minute temporal
chain is prevented. Existing complete cluster memberships remain intact on updates.
The impact polygon is a 500m geographic buffer around the canonical centroid.

`GET /api/v1/incidents/active` returns strict Golden Records, polygons and source_scope.
Use `?include_fixtures=false` to exclude demonstration incidents. Cleared-event
lifecycle/clearance expiry remains Phase 5; this endpoint currently uses is_active.
`POST /api/v1/spatial/process` or `dev.ps1 spatial` drains bounded batches. A native
worker can run via `python -m backend.spatial.worker --watch`. Failed resolutions
become GEOCODE_FAILED and dead-letter; after improving the gazetteer, explicitly
requeue the report's extraction payload to retry. Transient failures remain pending.
