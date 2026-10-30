// @file: frontend/src/DigitalTwinMap.tsx
// @exports: DigitalTwinMap
// @depends_on: maplibre-gl, @deck.gl/maplibre, map3d.ts, incidentLayers.ts, routeLayers.ts
// @purpose: Edge-to-edge native 3D terrain/buildings with terrain-anchored volumetric telemetry and orbit camera.
import {useEffect, useRef, useState} from 'react';
import * as maplibregl from 'maplibre-gl';
import mapWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';
import {MapLibreOverlay} from '@deck.gl/maplibre';
import {configure3d, looks, terrainAt} from './map3d';
import {incidentLayers} from './incidentLayers';
import {routeLayers} from './routeLayers';
import {drapeRibbon} from './nativeRibbon';
import type {Cursor, GoldenRecord, ScoutResult, TerrainPoint, Theme} from './types';
maplibregl.setWorkerUrl(mapWorkerUrl);
type Props = {records: GoldenRecord[]; route: ScoutResult | null; selected: GoldenRecord | null; theme: Theme;
  ortho: boolean; orbit: boolean; heatmap: boolean; select: (record: GoldenRecord) => void;
  cameraTick: number;
  cursor: (cursor: Cursor) => void; terrain: (values: Record<string, TerrainPoint>) => void;
  status: (status: string) => void};
export default function DigitalTwinMap(props: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const overlay = useRef<MapLibreOverlay | null>(null);
  const latest = useRef(props); latest.current = props;
  const previousTheme = useRef(props.theme);
  const previousCamera = useRef(props.cameraTick);
  const [pulse, setPulse] = useState(0);
  useEffect(() => {
    const instance = new maplibregl.Map({container: container.current!, style: looks[props.theme].style,
      center: [78.08, 30.22], zoom: 11.5, pitch: 58, bearing: -18, maxPitch: 75, attributionControl: false});
    map.current = instance;
    const deck = new MapLibreOverlay({interleaved: true, layers: []});
    instance.addControl(deck as unknown as maplibregl.IControl); overlay.current = deck;
    instance.on('style.load', () => {configure3d(instance, latest.current.theme); drapeRibbon(instance, latest.current.route);
      latest.current.status('TERRAIN / BUILDINGS ENABLED');});
    instance.on('error', e => {latest.current.status(`MAP SOURCE DEGRADED · ${e.error?.message?.slice(0, 75) || 'network asset'}`);});
    let previous = '', moved = 0;
    const sample = () => {
      const terrain: Record<string, TerrainPoint> = {};
      latest.current.route?.choke_points.forEach(c => {terrain[c.choke_id] = terrainAt(instance, c.location.longitude, c.location.latitude);});
      const encoded = JSON.stringify(terrain);
      if (encoded !== previous) {previous = encoded; latest.current.terrain(terrain);}
    };
    instance.on('idle', sample); instance.on('moveend', sample);
    instance.on('mousemove', e => {
      if (performance.now() - moved < 150) return; moved = performance.now();
      latest.current.cursor({longitude: e.lngLat.lng, latitude: e.lngLat.lat,
        altitude: terrainAt(instance, e.lngLat.lng, e.lngLat.lat).altitude});
    });
    const timer = window.setInterval(() => setPulse(value => (value + .08) % 1), 180);
    return () => {clearInterval(timer); instance.remove(); map.current = null; overlay.current = null;};
  }, []);
  useEffect(() => {
    if (previousTheme.current === props.theme) return;
    previousTheme.current = props.theme;
    const look = looks[props.theme];
    map.current?.setTerrain(null);  // Release the old DEM shader before replacing its style.
    map.current?.setStyle(look.style, {diff: false});
    map.current?.easeTo({pitch: props.ortho ? 0 : look.pitch, bearing: look.bearing, duration: 1200});
  }, [props.theme]);
  useEffect(() => {
    if (previousCamera.current === props.cameraTick) return;
    previousCamera.current = props.cameraTick;
    map.current?.easeTo({pitch: props.ortho ? 0 : 60, duration: 900});
  }, [props.cameraTick]);
  useEffect(() => {
    if (!props.orbit) return;
    const timer = window.setInterval(() => {if (map.current) map.current.jumpTo({bearing: map.current.getBearing() + .045});}, 45);
    return () => clearInterval(timer);
  }, [props.orbit]);
  useEffect(() => {
    const ground = (lon: number, lat: number) => map.current?.queryTerrainElevation([lon, lat]) || 0;
    const blocked = new Set(props.route?.incidents.filter(h => h.overlap === 'BLOCKED_AT_ETA').map(h => h.cluster_id));
    overlay.current?.setProps({layers: [...incidentLayers(props.records, props.theme, props.heatmap, pulse, ground, props.select, blocked),
      ...routeLayers(props.route, props.theme, ground)],
      getTooltip: info => info.object?.choke_name ? `${info.object.choke_name} · local delay ${info.object.delay_minutes.toFixed(1)}m` : null});
  }, [props.records, props.route, props.theme, props.heatmap, props.select, pulse]);
  useEffect(() => {
    if (props.selected) map.current?.flyTo({center: [props.selected.location.longitude, props.selected.location.latitude],
      zoom: 14.2, pitch: props.ortho ? 0 : 62, speed: .8, essential: true});
  }, [props.selected?.cluster_id]);
  useEffect(() => {
    if (map.current?.isStyleLoaded()) drapeRibbon(map.current, props.route);
    if (props.route) map.current?.flyTo({center: [78.16, 30.18], zoom: 10.9, pitch: props.ortho ? 0 : 62, speed: .75});
  }, [props.route]);
  return <div ref={container} className="digital-twin-map" aria-label="3D Uttarakhand digital twin map"/>;
}
