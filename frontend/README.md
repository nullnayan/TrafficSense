# TrafficSense 3D digital twin

Run `.\dev.ps1 api` and `.\dev.ps1 ui` in separate terminals from the repository
root, or `make api` / `make ui`. Open http://127.0.0.1:5173. The frontend also
supports `npm install`, `npm run dev` and `npm run build` inside `frontend/`.
The build runs strict `tsc -b` checking before Vite. Use Node 22.12+ or newer.
MapLibre 6.16 uses the dedicated `@deck.gl/maplibre` 9.4 integration and a bundled
same-origin module worker. A current browser with WebGL2 is required. Versions are
locked to include the fix for GHSA-jrc7-96c5-q579; `npm audit` checks run in CI.

The Vite proxy defaults to http://127.0.0.1:8000; `TRAFFICSENSE_API_URL` overrides
it. Production hosting needs an `/api` reverse proxy and native backend services.

The map fills the entire viewport. Four floating instruments provide the command
ribbon, incident/SQL drawer, route/clearance horizon and custom replay timeline.
There are no marketing blocks, KPI cards, native select/range/date widgets or
default MapLibre navigation/attribution pills. Attribution remains in a small text strip.

The final user color override replaces the earlier visual themes:

- `01 Civic White CAD`: matte white, asphalt text, highway green, light stone buildings.
- `02 Highway Paper Blueprint`: warm ivory, charcoal grid and engineering callouts.
- `03 Asphalt Carbon Radar`: neutral carbon with red/amber/green terrain telemetry.

Vector water, roads, labels and backgrounds are recolored to prevent provider blue
palettes from returning. No navy/cyan UI palette remains. Initial camera: center
`[78.08, 30.22]`, zoom 11.5, pitch 58, bearing -18. Themes set their own pitch and
lighting; camera controls explicitly select 2D, 60-degree 3D, or a slow orbit.

MapLibre uses an AWS/Mapzen Terrarium DEM and native OpenFreeMap building
extrusions. Incident and choke columns are hexagonal Deck.gl glyphs; their heights
represent modeled severity/PCU or delay, never surveyed building heights. Fixed
500m rings pulse by opacity. Routes use risk-segmented glow/core ribbons and
raised origin-to-choke arcs. Elevation and eastward slope badges come from the DEM;
unavailable samples stay blank. Building contact shadows approximate ground shading;
This configuration uses contact shading rather than native ambient occlusion.
Source coverage is not guaranteed.

ETA distinguishes baseline driving, modeled choke/residual delay and blockage
waiting. Upstream delays propagate into later hazard arrivals, preventing duplicate
clearance waits. A long blockage is labeled `DEPARTURE HOLD`; it is not presented
as many hours of driving. Fallback corridor geometry remains explicitly approximate.

Live choke speeds require `SCOUT_LIVE_TRAFFIC_ENABLED=true`,
`INGESTION_SOURCE_MODE=live`, one existing `TOMTOM_API_KEY` or `HERE_API_KEY`, and
a route request with fixtures excluded. At most five points are queried concurrently,
with a 2.5s per-point deadline and 60s Redis cache. TomTom takes precedence when
its key is present; otherwise HERE is used. Errors/blank keys retain local telemetry.
HERE metres/second are converted to km/h; the speed ratio is bounded to 0..1.
Live speed metadata enriches the records; delay and clearance remain local models.
Provider road-direction matching and live API quality have not been field validated.

SSE uses `/api/v1/events/stream?include_fixtures=true&replay_active=true`, independent
XREAD cursors, Last-Event-ID recovery and supersession. A 30s snapshot reconciles
expiry. `max_events` bounds all SSE frames for tests; the legacy alias remains.
The timeline filters update times in the current active snapshot; it does not
reconstruct archived incident states. Corridor history uses the safe SQL assistant.

The fixture pipeline seeds/ingests three stable Rajpur Road reports and drains
calibrated NLP, spatial and analytics stages. Retry preserves URL/stream identities.
Stable demo reports do not manufacture fresh activity after their expiry.

Map/DEM/fonts require network access. The map bundle still produces Vite's large
chunk warning. 60 FPS, live provider quality and field clearance calibration remain unverified.

Primary references: [MapLibre 3D buildings](https://maplibre.org/maplibre-gl-js/docs/examples/display-buildings-in-3d/),
[OpenFreeMap](https://openfreemap.org/quick_start/),
[TomTom flow](https://docs.tomtom.com/traffic-api/documentation/tomtom-maps/v1/traffic-flow/flow-segment-data),
[HERE flow](https://docs.here.com/traffic-api/docs/flow),
[MapLibre ambient-occlusion proposal](https://github.com/maplibre/maplibre-style-spec/issues/1796).
