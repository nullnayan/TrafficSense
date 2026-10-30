// @file: frontend/src/routeLayers.ts
// @exports: routeLayers, verdictColor
// @depends_on: @deck.gl/layers, incidentLayers.ts, types.ts
// @purpose: Risk-segmented corridor glow/core ribbons, raised telemetry arcs and hexagonal choke towers.
import {ArcLayer, ColumnLayer, PathLayer, ScatterplotLayer} from '@deck.gl/layers';
import type {Color} from '@deck.gl/core';
import {groundPosition, withAlpha} from './incidentLayers';
import {chokeHazard} from './eta';
import type {ScoutResult, Overlap, Theme} from './types';
export const verdictColor = (verdict: Overlap, _theme: Theme): Color =>
  verdict === 'BLOCKED_AT_ETA' ? [217, 45, 32, 245] : verdict === 'RESIDUAL_RISK' ? [217, 119, 6, 245] : [21, 128, 61, 245];
export function routeSegments(route: ScoutResult | null, ground: (lon: number, lat: number) => number) {
  if (!route || route.route_geojson.type !== 'LineString') return [];
  const coords = route.route_geojson.coordinates as number[][];
  let cumulative = 0;
  return coords.slice(1).map((point, index) => {
    const previous = coords[index];
    cumulative += Math.hypot((point[0] - previous[0]) * 96000, (point[1] - previous[1]) * 111000);
    const hazard = route.incidents.find(h => Math.abs(h.distance_along_route_meters - cumulative) < 1000);
    const verdict: Overlap = hazard?.overlap || 'CLEARED_BEFORE_ETA';
    return {path: [previous, point].map(p => [p[0], p[1], ground(p[0], p[1]) + 20]), verdict};
  });
}
export function routeLayers(route: ScoutResult | null, theme: Theme, ground: (lon: number, lat: number) => number) {
  if (!route || route.route_geojson.type !== 'LineString') return [];
  const coords = route.route_geojson.coordinates as number[][];
  const ribbons = routeSegments(route, ground);
  const origin = coords[0];
  const links = route.choke_points.map(choke => ({choke, source: [origin[0], origin[1], ground(origin[0], origin[1]) + 20],
    target: groundPosition(choke.location, ground)}));
  return [new PathLayer({id: 'corridor-glow', data: ribbons, getPath: d => d.path,
    getColor: d => withAlpha(verdictColor(d.verdict, theme), 64), getWidth: 16, widthMinPixels: 10}),
  new PathLayer({id: 'corridor-core', data: ribbons, getPath: d => d.path,
    getColor: d => verdictColor(d.verdict, theme), getWidth: 5, widthMinPixels: 3.5}),
  new ArcLayer({id: 'telemetry-links', data: links, getSourcePosition: d => d.source,
    getTargetPosition: d => d.target, getSourceColor: [13, 122, 95, 75], getTargetColor: [21, 128, 61, 170],
    getWidth: 1.5, greatCircle: false, getHeight: .3}),
  new ColumnLayer({id: 'choke-towers', data: route.choke_points, diskResolution: 6, radius: 280, extruded: true,
    getPosition: c => groundPosition(c.location, ground), getElevation: c => Math.min(2400, 450 + c.delay_minutes * 35),
    getFillColor: c => c.live_speed_ratio != null && c.live_speed_ratio < .4 ? [217, 45, 32, 230] :
      verdictColor(chokeHazard(c, route)?.overlap || (c.delay_minutes > 0 ? 'RESIDUAL_RISK' : 'CLEARED_BEFORE_ETA'), theme), pickable: true}),
  new ScatterplotLayer({id: 'choke-rings', data: route.choke_points, getPosition: c => groundPosition(c.location, ground),
    getRadius: 500, filled: false, stroked: true, getLineColor: [21, 128, 61, 155], getLineWidth: 2, lineWidthUnits: 'pixels'})];
}
