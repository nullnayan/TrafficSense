// @file: frontend/src/main.tsx
// @exports: React root
// @depends_on: App.tsx, hud.css, panels.css, maplibre-gl.css
// @purpose: React 18 application entry and shared console styles.
import React from 'react';
import {createRoot} from 'react-dom/client';
import App from './App';
import 'maplibre-gl/dist/maplibre-gl.css';
import './hud.css';
import './panels.css';
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
