import { useCallback, useEffect, useMemo, useRef, useState, type ComponentRef } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Html, OrbitControls } from "@react-three/drei";
import { Vector3 } from "three";
import { Map as MapLibreMap, Marker, NavigationControl, setWorkerUrl, type GeoJSONSource, type MapLayerMouseEvent, type RasterTileSource } from "maplibre-gl";
import mapLibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Building, EvidenceStatus } from "./types";

setWorkerUrl(mapLibreWorkerUrl);

const CAMPUS_CENTER: [number, number] = [78.4402911, 17.5944932];
const ESRI_TILE_URL = "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";

function buildingCoordinates(building: Building): [number, number] {
  // Schematic spacing only. These positions are not surveyed building footprints.
  const metersPerGridUnit = 34;
  const longitude = CAMPUS_CENTER[0] + (building.position_x * metersPerGridUnit) / (111320 * Math.cos(CAMPUS_CENTER[1] * Math.PI / 180));
  const latitude = CAMPUS_CENTER[1] - (building.position_z * metersPerGridUnit) / 110540;
  return [longitude, latitude];
}

function SatelliteCampus({ buildings, selectedId, onSelect, recenterKey, onModelFallback, onImageryUnavailable, active, retryKey }: { buildings: Building[]; selectedId: string | null; onSelect: (id: string) => void; recenterKey: number; onModelFallback: () => void; onImageryUnavailable: () => void; active: boolean; retryKey: number }) {
  const mapContainer = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const markersRef = useRef<Array<{ id: string; marker: Marker; element: HTMLButtonElement }>>([]);
  const tileTimeoutRef = useRef<number | null>(null);
  const [tileStatus, setTileStatus] = useState<"loading" | "ready" | "unavailable">("loading");
  const previousSelectedId = useRef<string | null>(null);
  const features = useMemo(() => buildings.map((building) => {
    const [longitude, latitude] = buildingCoordinates(building);
    const halfWidth = 17.5;
    const halfDepth = 12.5;
    const dx = halfWidth / (111320 * Math.cos(latitude * Math.PI / 180));
    const dy = halfDepth / 110540;
    return {
      type: "Feature" as const,
      properties: { id: building.id, name: building.name, area: building.reported_area_m2 },
      geometry: { type: "Polygon" as const, coordinates: [[[longitude-dx,latitude-dy],[longitude+dx,latitude-dy],[longitude+dx,latitude+dy],[longitude-dx,latitude+dy],[longitude-dx,latitude-dy]]] },
    };
  }), [buildings]);

  useEffect(() => {
    if (!mapContainer.current || mapRef.current) return;
    let map: MapLibreMap;
    try {
      map = new MapLibreMap({
        container: mapContainer.current,
        style: {
          version: 8,
          sources: { esri: { type: "raster", tiles: [ESRI_TILE_URL], tileSize: 256, attribution: "Tiles © Esri, Maxar, Earthstar Geographics, and the GIS User Community" } },
          layers: [{ id: "satellite", type: "raster", source: "esri", paint: { "raster-fade-duration": 200 } }],
        },
        center: CAMPUS_CENTER,
        zoom: 16.5,
        pitch: 47,
        bearing: -8,
        maxPitch: 65,
        minZoom: 15.7,
        maxZoom: 19.2,
        maxBounds: [[78.427, 17.583], [78.454, 17.606]],
      });
    } catch {
      setTileStatus("unavailable");
      return;
    }
    mapRef.current = map;
    map.addControl(new NavigationControl({ visualizePitch: true }), "bottom-right");
    // Add local overlays after the style is parsed, without waiting on remote imagery tiles.
    map.on("style.load", () => {
      map.addSource("campus-buildings", { type: "geojson", data: { type: "FeatureCollection", features } });
      map.addLayer({ id: "campus-footprints", type: "fill-extrusion", source: "campus-buildings", paint: {
        "fill-extrusion-color": ["case", ["==", ["get", "id"], selectedId ?? ""], "#d5a04f", "#d7e0d2"],
        "fill-extrusion-height": 22,
        "fill-extrusion-base": 0,
        "fill-extrusion-opacity": 0.88,
      } });
      map.addLayer({ id: "campus-outline", type: "line", source: "campus-buildings", paint: { "line-color": "#fff9e9", "line-width": 2 } });
      buildings.forEach((building) => {
        const [longitude, latitude] = buildingCoordinates(building);
        const label = document.createElement("button");
        label.className = "satellite-building-label";
        label.classList.toggle("selected", building.id === selectedId);
        label.type = "button";
        label.textContent = shortLabels[building.id] ?? building.name.slice(0, 3).toUpperCase();
        label.title = building.name;
        label.setAttribute("aria-label", `Select ${building.name}`);
        label.addEventListener("click", (event) => { event.stopPropagation(); onSelect(building.id); });
        const marker = new Marker({ element: label, anchor: "bottom" }).setLngLat([longitude, latitude]).addTo(map);
        markersRef.current.push({ id: building.id, marker, element: label });
      });
      map.on("click", "campus-footprints", (event: MapLayerMouseEvent) => {
        const id = event.features?.[0]?.properties?.id;
        if (typeof id === "string") onSelect(id);
      });
      map.on("mouseenter", "campus-footprints", () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", "campus-footprints", () => { map.getCanvas().style.cursor = ""; });
    });
    map.on("error", () => setTileStatus((status) => status === "ready" ? status : "loading"));
    tileTimeoutRef.current = window.setTimeout(() => setTileStatus((status) => status === "loading" ? "unavailable" : status), 9000);
    map.on("sourcedata", (event) => {
      if (event.sourceId === "esri" && event.sourceDataType === "content") {
        setTileStatus("ready");
        if (tileTimeoutRef.current !== null) window.clearTimeout(tileTimeoutRef.current);
        tileTimeoutRef.current = null;
      }
    });
    return () => { if (tileTimeoutRef.current !== null) window.clearTimeout(tileTimeoutRef.current); markersRef.current.forEach(({ marker }) => marker.remove()); markersRef.current = []; map.remove(); mapRef.current = null; };
  // Map instance is created once; latest overlay data is updated below.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const source = map?.getSource("campus-buildings") as GeoJSONSource | undefined;
    source?.setData({ type: "FeatureCollection", features });
    if (map?.getLayer("campus-footprints")) map.setPaintProperty("campus-footprints", "fill-extrusion-color", ["case", ["==", ["get", "id"], selectedId ?? ""], "#d5a04f", "#d7e0d2"]);
    markersRef.current.forEach(({ id, element }) => element.classList.toggle("selected", id === selectedId));
  }, [features, selectedId]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (recenterKey) map.flyTo({ center: CAMPUS_CENTER, zoom: 16.5, pitch: 47, bearing: -8, duration: 850 });
  }, [recenterKey]);

  useEffect(() => {
    if (!active) return;
    const frame = window.requestAnimationFrame(() => mapRef.current?.resize());
    return () => window.cancelAnimationFrame(frame);
  }, [active]);

  useEffect(() => {
    if (!retryKey) return;
    setTileStatus("loading");
    if (tileTimeoutRef.current !== null) window.clearTimeout(tileTimeoutRef.current);
    tileTimeoutRef.current = window.setTimeout(() => setTileStatus((status) => status === "loading" ? "unavailable" : status), 9000);
    const source = mapRef.current?.getSource("esri") as RasterTileSource | undefined;
    source?.setTiles([`${ESRI_TILE_URL}?retry=${retryKey}`]);
    mapRef.current?.resize();
  }, [retryKey]);

  useEffect(() => {
    if (tileStatus === "unavailable") onImageryUnavailable();
  }, [onImageryUnavailable, tileStatus]);

  useEffect(() => {
    if (selectedId && previousSelectedId.current && selectedId !== previousSelectedId.current) {
      const building = buildings.find((item) => item.id === selectedId);
      if (building) mapRef.current?.flyTo({ center: buildingCoordinates(building), duration: 650 });
    }
    previousSelectedId.current = selectedId;
  }, [buildings, selectedId]);

  return <>
    <div className={`satellite-view ${active ? "active" : "inactive"}`} aria-hidden={!active}>
      <div ref={mapContainer} className="satellite-map" aria-label="Satellite map with approximate 3D campus building overlays" />
      {tileStatus === "unavailable" && <div className="tile-status" role="status"><strong>Satellite imagery is unavailable</strong><span>Check network access, or open the local 3D campus model.</span><button onClick={onModelFallback}>Open 3D model</button></div>}
      {tileStatus === "loading" && <div className="tile-loading" role="status">Loading Esri satellite imagery…</div>}
      <div className="map-attribution-note">Satellite imagery © Esri and contributors</div>
    </div>
  </>;
}

const CAMPUS_SCALE_X = 2.3;
const CAMPUS_SCALE_Z = 2.2;

const shortLabels: Record<string, string> = {
  "sr-block": "SR",
  "ak-block": "APJ",
  "jc-block": "JCB",
  "mt-block": "MT",
  "cv-raman-block": "CVR",
  "vs-block": "VS",
  "mg-block": "MG",
  "kc-block": "KC",
};

function BuildingModel({ building, selected, showLabels, onSelect }: { building: Building; selected: boolean; showLabels: boolean; onSelect: () => void }) {
  const x = building.position_x * CAMPUS_SCALE_X;
  const z = building.position_z * CAMPUS_SCALE_Z;
  const height = 5.4;
  return <group position={[x, 0, z]} onClick={(event) => { event.stopPropagation(); onSelect(); }}>
    <mesh position={[0, 0.12, 0]} receiveShadow>
      <boxGeometry args={[5.4, 0.24, 4.5]} />
      <meshStandardMaterial color={selected ? "#d9b77f" : "#a5bc9e"} roughness={0.94} />
    </mesh>
    <mesh position={[0, height / 2 + 0.24, 0]} castShadow receiveShadow>
      <boxGeometry args={[4.35, height, 3.35]} />
      <meshStandardMaterial color={selected ? "#e9b764" : "#e8ebe3"} roughness={0.68} metalness={0.04} />
    </mesh>
    <mesh position={[0, height + 0.28, 0]} castShadow>
      <boxGeometry args={[4.55, 0.22, 3.55]} />
      <meshStandardMaterial color={selected ? "#895c2e" : "#426754"} roughness={0.58} />
    </mesh>
    {[-1.35, -0.45, 0.45, 1.35].flatMap((windowX) => [1.05, 2.15, 3.25, 4.35].map((windowY) => <mesh key={`${windowX}-${windowY}`} position={[windowX, windowY, -1.69]}>
      <boxGeometry args={[0.52, 0.42, 0.06]} />
      <meshStandardMaterial color="#639a9b" metalness={0.28} roughness={0.3} />
    </mesh>))}
    {showLabels && <Html position={[0, height + 0.85, 0]} center distanceFactor={13} zIndexRange={[20, 0]}>
      <button className={`campus-building-tag ${selected ? "selected" : ""}`} onClick={(event) => { event.stopPropagation(); onSelect(); }} title={building.name}>
        <span>{shortLabels[building.id] ?? building.name.slice(0, 3).toUpperCase()}</span>
        <small>{building.name.replace(" Block", "")}</small>
      </button>
    </Html>}
  </group>;
}

function CampusTree({ x, z, dark }: { x: number; z: number; dark: boolean }) {
  return <group position={[x, 0, z]}>
    <mesh position={[0, 0.55, 0]} castShadow>
      <cylinderGeometry args={[0.12, 0.18, 1.1, 7]} />
      <meshStandardMaterial color="#896d4b" />
    </mesh>
    <mesh position={[0, 1.55, 0]} castShadow>
      <coneGeometry args={[0.85, 1.9, 7]} />
      <meshStandardMaterial color={dark ? "#396448" : "#4f805c"} roughness={0.9} />
    </mesh>
  </group>;
}

function CameraRig({ buildings, focusId, is3d, recenterKey, controlsRef }: { buildings: Building[]; focusId: string | null; is3d: boolean; recenterKey: number; controlsRef: React.RefObject<ComponentRef<typeof OrbitControls> | null> }) {
  const { camera } = useThree();
  const goalPosition = useMemo(() => new Vector3(), []);
  const goalTarget = useMemo(() => new Vector3(), []);
  const transitioning = useRef(false);
  useEffect(() => {
    const building = buildings.find((item) => item.id === focusId);
    const targetX = building ? building.position_x * CAMPUS_SCALE_X : 0;
    const targetZ = building ? building.position_z * CAMPUS_SCALE_Z : -4.4;
    goalTarget.set(targetX, 0, targetZ);
    if (is3d) goalPosition.set(targetX, building ? 12 : 36, targetZ + (building ? 16 : 45));
    else goalPosition.set(targetX, 48, targetZ + 0.01);
    transitioning.current = true;
  }, [buildings, camera, controlsRef, focusId, is3d, recenterKey]);
  useFrame((_, delta) => {
    if (!transitioning.current) return;
    const controls = controlsRef.current;
    if (!controls) return;
    const blend = 1 - Math.exp(-5 * delta);
    camera.position.lerp(goalPosition, blend);
    controls.target.lerp(goalTarget, blend);
    controls.update();
    if (camera.position.distanceTo(goalPosition) < 0.08 && controls.target.distanceTo(goalTarget) < 0.08) {
      camera.position.copy(goalPosition);
      controls.target.copy(goalTarget);
      controls.update();
      transitioning.current = false;
    }
  });
  return null;
}

function CampusModel({ buildings, selectedId, focusId, is3d, dark, showLabels, recenterKey, onSelect }: {
  buildings: Building[];
  selectedId: string | null;
  focusId: string | null;
  is3d: boolean;
  dark: boolean;
  showLabels: boolean;
  recenterKey: number;
  onSelect: (id: string) => void;
}) {
  const controlsRef = useRef<ComponentRef<typeof OrbitControls> | null>(null);
  const treePositions = useMemo(() => [
    [-17, -11], [-17, 0], [-17, 12], [17, -11], [17, 0], [17, 12],
    [-11, 13], [-4, 13], [4, 13], [11, 13], [-11, -13], [0, -13], [11, -13],
  ] as const, []);

  return <>
    <color attach="background" args={[dark ? "#17231f" : "#dce8df"]} />
    <ambientLight intensity={dark ? 0.72 : 0.9} />
    <hemisphereLight args={[dark ? "#d0dfdb" : "#f5f8f2", dark ? "#314239" : "#879984", 1.1]} />
    <directionalLight position={[-14, 24, 12]} intensity={dark ? 1.65 : 2.1} castShadow shadow-mapSize-width={1024} shadow-mapSize-height={1024} />
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.18, 0]} receiveShadow>
      <planeGeometry args={[43, 33]} />
      <meshStandardMaterial color={dark ? "#33483c" : "#93ae91"} roughness={1} />
    </mesh>
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.12, 0]}>
      <planeGeometry args={[39, 29]} />
      <meshStandardMaterial color={dark ? "#405145" : "#b8c9b0"} roughness={1} />
    </mesh>
    <mesh position={[0, -0.025, 0]}>
      <boxGeometry args={[2.2, 0.04, 25]} />
      <meshStandardMaterial color={dark ? "#728078" : "#d9d7c7"} roughness={0.95} />
    </mesh>
    <mesh position={[0, -0.02, -4.5]}>
      <boxGeometry args={[35, 0.05, 1.25]} />
      <meshStandardMaterial color={dark ? "#56635b" : "#d5d5c8"} roughness={0.95} />
    </mesh>
    <mesh position={[0, -0.015, 10.5]}>
      <boxGeometry args={[35, 0.06, 1.55]} />
      <meshStandardMaterial color={dark ? "#242c29" : "#626b65"} roughness={0.95} />
    </mesh>
    <mesh position={[0, 0.015, 4.2]}>
      <boxGeometry args={[5.2, 0.06, 2.3]} />
      <meshStandardMaterial color={dark ? "#778677" : "#d9c9a4"} roughness={0.95} />
    </mesh>
    {buildings.map((building) => <BuildingModel key={building.id} building={building} selected={building.id === selectedId} showLabels={showLabels} onSelect={() => onSelect(building.id)} />)}
    {treePositions.map(([x, z]) => <CampusTree key={`${x}-${z}`} x={x} z={z} dark={dark} />)}
    <CameraRig buildings={buildings} focusId={focusId} is3d={is3d} recenterKey={recenterKey} controlsRef={controlsRef} />
    <OrbitControls ref={controlsRef} makeDefault enableDamping dampingFactor={0.08} enableRotate={is3d} minDistance={18} maxDistance={70} maxPolarAngle={Math.PI / 2.02} target={[0, 0, -4.4]} />
  </>;
}

export default function CampusScene({ buildings, selectedId, onSelect, resourceMode, evidenceStatus }: {
  buildings: Building[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  resourceMode: string;
  evidenceStatus: EvidenceStatus;
}) {
  const [view, setView] = useState<"satellite" | "model">("satellite");
  const [is3d, setIs3d] = useState(true);
  const [dark, setDark] = useState(false);
  const [showLabels, setShowLabels] = useState(true);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [imageryOffline, setImageryOffline] = useState(false);
  const [retrySatelliteKey, setRetrySatelliteKey] = useState(0);
  const [recenterKey, setRecenterKey] = useState(0);
  const previousSelectedId = useRef<string | null>(null);

  useEffect(() => {
    if (selectedId && previousSelectedId.current && selectedId !== previousSelectedId.current) setFocusId(selectedId);
    previousSelectedId.current = selectedId;
  }, [selectedId]);

  function selectBuilding(id: string) {
    onSelect(id);
    setFocusId(id);
  }

  function recenterCampus() {
    setFocusId(null);
    setRecenterKey((value) => value + 1);
  }

  const openLocalModel = useCallback(() => { setImageryOffline(true); setView("model"); }, []);
  const retrySatellite = useCallback(() => { setImageryOffline(false); setRetrySatelliteKey((key) => key + 1); setView("satellite"); }, []);

  return <div className={`scene digital-twin-scene ${dark && view === "model" ? "scene-dark" : ""}`}>
    <SatelliteCampus active={view === "satellite"} buildings={buildings} selectedId={selectedId} onSelect={selectBuilding} recenterKey={recenterKey} onModelFallback={openLocalModel} onImageryUnavailable={openLocalModel} retryKey={retrySatelliteKey} />
    <div className={`model-view ${view === "model" ? "active" : "inactive"}`} aria-hidden={view !== "model"}><Canvas shadows dpr={[1, 1.5]} camera={{ position: [0, 29, 36], fov: 40 }} gl={{ antialias: true, powerPreference: "high-performance" }}>
      <CampusModel buildings={buildings} selectedId={selectedId} focusId={focusId} is3d={is3d} dark={dark} showLabels={showLabels} recenterKey={recenterKey} onSelect={selectBuilding} />
    </Canvas></div>
    <div className="digital-twin-stamp"><span className="map-source-dot" /> MLRIT CAMPUS <span>{view === "satellite" ? "SATELLITE + 3D OVERLAY" : "SCHEMATIC 3D MODEL"}</span></div>
    <div className="twin-controls" aria-label="Campus view controls">
      <div className="map-control-group"><button className={view === "satellite" ? "active" : ""} onClick={() => setView("satellite")} aria-pressed={view === "satellite"}>Satellite</button><button className={view === "model" ? "active" : ""} onClick={() => setView("model")} aria-pressed={view === "model"}>3D Model</button></div>
      {view === "model" && <div className="map-control-group"><button className={is3d ? "active" : ""} onClick={() => setIs3d(true)} aria-pressed={is3d}>3D</button><button className={!is3d ? "active" : ""} onClick={() => setIs3d(false)} aria-pressed={!is3d}>Plan</button></div>}
      <button className="map-control" onClick={recenterCampus} title="Recenter the whole campus">⌖ <span>Campus</span></button>
      {view === "model" && <><button className={`map-control ${showLabels ? "active" : ""}`} onClick={() => setShowLabels((value) => !value)} aria-pressed={showLabels} title="Toggle building labels">Aa <span>Labels</span></button><button className="map-control" onClick={() => setDark((value) => !value)} title="Switch lighting">◐ <span>{dark ? "Day" : "Night"}</span></button></>}
    </div>
    {imageryOffline && view === "model" && <div className="imagery-offline" role="status"><span>Satellite tiles are unreachable; showing the local 3D campus.</span><button onClick={retrySatellite}>Retry satellite</button></div>}
    <div className="map-legend"><span className="legend-swatch" /> {buildings.length} campus blocks <span className="legend-selected" /> Selected</div>
    <div className="twin-evidence"><strong>{resourceMode.replaceAll("_", " ")}</strong><span>{evidenceStatus} data</span></div>
    <div className="twin-note">{view === "satellite" ? "Imagery: Esri · building locations and footprints are approximate" : "Approximate building massing · floor counts and footprints not verified"}</div>
  </div>;
}

