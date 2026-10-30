// @file: frontend/src/map3d.ts
// @exports: looks, configure3d, terrainAt
// @depends_on: maplibre-gl, OpenFreeMap, AWS Terrarium DEM, types.ts
// @purpose: Real terrain mesh, height-based native buildings, contact shading and theme lighting.
import type {Map} from 'maplibre-gl';
import type {Theme, TerrainPoint} from './types';
export const looks = {
  civic_white_cad: {style: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json', pitch: 55, bearing: -18, terrain: 1.2, buildings: '#E2E5E9', light: '#ffffff'},
  highway_paper_blueprint: {style: 'https://tiles.openfreemap.org/styles/liberty', pitch: 35, bearing: -18, terrain: 1.2, buildings: '#d1cdc5', light: '#fffaf0'},
  asphalt_carbon_radar: {style: 'https://tiles.openfreemap.org/styles/dark', pitch: 64, bearing: -28, terrain: 1.8, buildings: '#565657', light: '#eeeeea'},
};
export function configure3d(map: Map, theme: Theme) {
  const look = looks[theme];
  const carbon = theme === 'asphalt_carbon_radar', paper = theme === 'highway_paper_blueprint';
  // Recolor vector paints too: provider styles can otherwise reintroduce blue water/labels.
  for (const layer of map.getStyle().layers || []) {
    if (layer.type === 'background') map.setPaintProperty(layer.id, 'background-color', carbon ? '#141416' : paper ? '#F4F1EA' : '#F8F9FA');
    if (layer.type === 'fill') map.setPaintProperty(layer.id, 'fill-color', /water/.test(layer.id) ?
      carbon ? '#2c2c2e' : paper ? '#e3dfd4' : '#dfe3e0' : carbon ? '#202022' : paper ? '#ede9df' : '#f3f4f3');
    if (layer.type === 'line') map.setPaintProperty(layer.id, 'line-color', /water/.test(layer.id) ?
      carbon ? '#3a3a3c' : '#bbc2bb' : carbon ? '#555553' : /cas|outline/.test(layer.id) ? '#c4c3bd' : '#e3e1dc');
    if (layer.type === 'symbol' && layer.layout?.['text-field']) {
      map.setPaintProperty(layer.id, 'text-color', carbon ? '#d9d9d4' : '#52534e');
      map.setPaintProperty(layer.id, 'text-halo-color', carbon ? '#141416' : paper ? '#f4f1ea' : '#f8f9fa');
    }
    if (layer.type === 'fill' && layer.paint?.['fill-pattern']) map.setPaintProperty(layer.id, 'fill-pattern', undefined);
    if (layer.type === 'symbol' && layer.layout?.['icon-image']) map.setLayoutProperty(layer.id, 'icon-image', undefined);
  }
  if (!map.getSource('terrain-dem')) map.addSource('terrain-dem', {type: 'raster-dem',
    tiles: ['https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'],
    encoding: 'terrarium', tileSize: 256, maxzoom: 14});
  map.setTerrain({source: 'terrain-dem', exaggeration: look.terrain});
  if (!map.getSource('twin-buildings')) map.addSource('twin-buildings', {type: 'vector', url: 'https://tiles.openfreemap.org/planet'});
  const label = map.getStyle().layers?.find(layer => layer.type === 'symbol' && layer.layout?.['text-field'])?.id;
  if (!map.getLayer('terrain-relief')) map.addLayer({id: 'terrain-relief', type: 'hillshade', source: 'terrain-dem',
    paint: {'hillshade-shadow-color': carbon ? '#050505' : '#77786f',
      'hillshade-highlight-color': look.light, 'hillshade-exaggeration': .35}}, label);
  if (!map.getLayer('building-contact')) map.addLayer({id: 'building-contact', type: 'fill',
    source: 'twin-buildings', 'source-layer': 'building', minzoom: 13,
    paint: {'fill-color': '#111315', 'fill-opacity': .22, 'fill-translate': [2, 3]}}, label);
  if (!map.getLayer('building-3d')) map.addLayer({id: 'building-3d', type: 'fill-extrusion',
    source: 'twin-buildings', 'source-layer': 'building', minzoom: 13,
    filter: ['!=', ['get', 'hide_3d'], true], paint: {'fill-extrusion-color': look.buildings,
      'fill-extrusion-height': ['coalesce', ['get', 'render_height'], ['get', 'height'], 6],
      'fill-extrusion-base': ['coalesce', ['get', 'render_min_height'], ['get', 'min_height'], 0],
      'fill-extrusion-opacity': .92, 'fill-extrusion-vertical-gradient': true}}, label);
  map.setLight({anchor: 'viewport', color: look.light, intensity: .6, position: [1.5, 205, 35]});
}
export function terrainAt(map: Map, longitude: number, latitude: number): TerrainPoint {
  const raw = map.queryTerrainElevation([longitude, latitude]);
  const east = map.queryTerrainElevation([longitude + .0005, latitude]);
  const altitude = raw == null ? null : raw / (map.getTerrain()?.exaggeration || 1);
  return {altitude, slope: raw == null || east == null ? null :
    Math.atan(Math.abs(east - raw) / ((map.getTerrain()?.exaggeration || 1) * 48)) * 180 / Math.PI};
}
