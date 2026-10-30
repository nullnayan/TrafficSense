// @file: frontend/src/nativeRibbon.ts
// @exports: drapeRibbon
// @depends_on: maplibre-gl, routeLayers.ts, types.ts
// @purpose: Terrain-draped risk ribbon foundation keeps corridor geometry visible between elevated Deck path vertices.
import type {GeoJSONSource, Map} from 'maplibre-gl';
import {routeSegments} from './routeLayers';
import type {ScoutResult} from './types';
export function drapeRibbon(map: Map, route: ScoutResult | null) {
  const data: GeoJSON.FeatureCollection<GeoJSON.LineString> = {type: 'FeatureCollection',
    features: routeSegments(route, () => 0).map(segment => ({type: 'Feature',
      geometry: {type: 'LineString', coordinates: segment.path.map(point => [point[0], point[1]])},
      properties: {verdict: segment.verdict}}))};
  const source = map.getSource('corridor-drape') as GeoJSONSource | undefined;
  if (source) {source.setData(data); return;}
  map.addSource('corridor-drape', {type: 'geojson', data});
  for (const [id, width, opacity] of [['ribbon-drape-glow', 10, .25], ['ribbon-drape-core', 3.5, .85]] as const) {
    map.addLayer({id, type: 'line', source: 'corridor-drape', layout: {'line-join': 'round', 'line-cap': 'round'},
      paint: {'line-color': ['match', ['get', 'verdict'], 'BLOCKED_AT_ETA', '#D92D20', 'RESIDUAL_RISK', '#D97706', '#15803D'],
        'line-width': width, 'line-opacity': opacity}}, map.getLayer('building-contact') ? 'building-contact' : undefined);
  }
}
