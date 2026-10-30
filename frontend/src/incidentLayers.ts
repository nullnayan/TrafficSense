// @file: frontend/src/incidentLayers.ts
// @exports: incidentLayers, severityColor, groundPosition
// @depends_on: @deck.gl/layers, @deck.gl/aggregation-layers, types.ts
// @purpose: Terrain-anchored hexagonal PCU towers, exact 500m pulse rings and canonical CAD labels.
import {ColumnLayer, GeoJsonLayer, ScatterplotLayer, TextLayer} from '@deck.gl/layers';
import {HeatmapLayer} from '@deck.gl/aggregation-layers';
import type {Color, PickingInfo} from '@deck.gl/core';
import type {GoldenRecord, Location, Theme} from './types';
export function groundPosition(location: Location, ground: (lon: number, lat: number) => number): [number, number, number] {
  return [location.longitude, location.latitude, ground(location.longitude, location.latitude) + 6];
}
export const withAlpha = (color: Color, alpha: number): [number, number, number, number] => [color[0], color[1], color[2], alpha];
export function severityColor(record: GoldenRecord, theme: Theme, blocked = false): Color {
  return blocked || record.severity === 'catastrophic' ? [217, 45, 32, 240] : record.severity === 'severe' ? [217, 119, 6, 240] :
    [21, 128, 61, 240];
}
export function incidentLayers(records: GoldenRecord[], theme: Theme, heatmap: boolean, pulse: number,
  ground: (lon: number, lat: number) => number, select: (record: GoldenRecord) => void, blocked: Set<string>) {
  const height = (r: GoldenRecord) => Math.min(3500, Math.max(450,
    (r.clearance?.pcu_queue_load || ({minor: 1, moderate: 2, severe: 3, catastrophic: 5}[r.severity] || 1) * 450)));
  const position = (r: GoldenRecord) => groundPosition(r.location, ground);
  const color = (r: GoldenRecord) => severityColor(r, theme, blocked.has(r.cluster_id));
  return [new ScatterplotLayer<GoldenRecord>({id: 'dbscan-500m', data: records, getPosition: position,
    getRadius: 500, radiusUnits: 'meters', filled: true, stroked: true, getLineWidth: 2, lineWidthUnits: 'pixels',
    getFillColor: r => withAlpha(color(r), 15 + pulse * 12),
    getLineColor: r => withAlpha(color(r), 130 + pulse * 90)}),
  new ColumnLayer<GoldenRecord>({id: 'incident-towers', data: records, diskResolution: 6, radius: 280,
    extruded: true, getPosition: position, getElevation: height, getFillColor: color,
    material: {ambient: .45, diffuse: .8, shininess: 35, specularColor: [235, 235, 225]}, pickable: true,
    onClick: (info: PickingInfo<GoldenRecord>) => {if (info.object) select(info.object);}}),
  new GeoJsonLayer({id: 'postgis-impact-zones', data: records.filter(r => r.impact_polygon_geojson).map(r =>
    ({type: 'Feature' as const, geometry: r.impact_polygon_geojson!, properties: {record: r}})),
    filled: false, stroked: true, getLineColor: [13, 122, 95, 100], getLineWidth: 1, lineWidthUnits: 'pixels'}),
  ...(heatmap ? [new HeatmapLayer<GoldenRecord>({id: 'density', data: records,
    getPosition: position, getWeight: r => r.report_count, radiusPixels: 55})] : []),
  new TextLayer<GoldenRecord>({id: 'cad-callouts', data: records, getPosition: r => {
    const p = position(r); return [p[0], p[1], p[2] + height(r) + 100];},
    getText: r => `${r.landmark_tuple.primary_landmark || r.incident_type}\n${r.report_count} SRC · ${Math.round(r.clearance?.pcu_queue_load || 0)} PCU`,
    getSize: 10, getColor: theme === 'asphalt_carbon_radar' ? [245, 245, 240] : [17, 19, 21],
    fontFamily: 'monospace', characterSet: 'auto', background: true, getBackgroundColor: theme === 'asphalt_carbon_radar' ? [20, 20, 22, 230] : [255, 255, 255, 230],
    backgroundPadding: [6, 4], billboard: true})];
}
