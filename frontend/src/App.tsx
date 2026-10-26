import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import CampusScene from "./CampusScene";
import { demoBuildings } from "./demoBuildings";
import ScenarioPanel from "./ScenarioPanel";
import type { Building, ChatResult, EvidenceStatus, Forecast, Reading, ScenarioInput } from "./types";

const Plot = lazy(() => import("react-plotly.js"));

const statusLabel: Record<EvidenceStatus, string> = {
  reference: "Reference source",
  measured: "Measured",
  synthetic: "Synthetic demo data",
  predicted: "Prediction",
  simulated: "Simulation",
  mixed: "Mixed evidence",
};

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json() as Promise<T>;
}

export default function App() {
  const [buildings, setBuildings] = useState<Building[]>(demoBuildings);
  const [selectedId, setSelectedId] = useState<string | null>(demoBuildings[0]?.id ?? null);
  const [readings, setReadings] = useState<Reading[]>([]);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [meterType, setMeterType] = useState("electricity");
  const [apiError, setApiError] = useState<string | null>(null);
  const [chatText, setChatText] = useState("");
  const [chatResult, setChatResult] = useState<ChatResult | null>(null);
  const [chatBusy, setChatBusy] = useState(false);
  const [scenarioConfig, setScenarioConfig] = useState<ScenarioInput | null>(null);

  useEffect(() => {
    getJson<Building[]>("/api/v1/buildings")
      .then((rows) => { const available = rows.length ? rows : demoBuildings; setBuildings(available); setSelectedId((current) => current && available.some((row) => row.id === current) ? current : available[0]?.id ?? null); setApiError(null); })
      .catch((error: Error) => setApiError(`API unavailable: ${error.message}`));
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    Promise.all([
      getJson<Reading[]>(`/api/v1/readings?building_id=${encodeURIComponent(selectedId)}&meter_type=${meterType}&limit=168`),
      getJson<Forecast>(`/api/v1/buildings/${encodeURIComponent(selectedId)}/forecasts?meter_type=${meterType}&horizon=24`),
    ])
      .then(([data, fc]) => { setReadings(data.reverse()); setForecast(fc); setApiError(null); })
      .catch((error: Error) => { setReadings([]); setForecast(null); setApiError(`Data request failed: ${error.message}`); });
  }, [selectedId, meterType]);

  const selected = buildings.find((b) => b.id === selectedId) ?? null;
  const readingStatus = readings[readings.length - 1]?.evidence_status ?? "reference";
  const plot = useMemo(() => {
    const points = forecast?.points ?? [];
    return {
      data: [
        { x: readings.map((r) => r.interval_end), y: readings.map((r) => r.value), type: "scatter" as const, mode: "lines" as const, name: `Observed (${statusLabel[readingStatus]})`, line: { color: "#366f54", width: 2 } },
        { x: points.map((p) => p.timestamp), y: points.map((p) => p.value), type: "scatter" as const, mode: "lines" as const, name: "Seasonal baseline forecast", line: { color: "#b77932", width: 2, dash: "dash" } },
        { x: [...points.map((p) => p.timestamp), ...points.map((p) => p.timestamp).reverse()], y: [...points.map((p) => p.upper), ...points.map((p) => p.lower).reverse()], type: "scatter" as const, mode: "lines" as const, fill: "toself" as const, fillcolor: "rgba(183,121,50,0.16)", line: { color: "transparent" }, name: "Empirical interval" },
      ],
      layout: { autosize: true, margin: { l: 54, r: 18, t: 12, b: 42 }, paper_bgcolor: "#fffdf8", plot_bgcolor: "#fffdf8", font: { color: "#25362e", family: "Inter, sans-serif" }, xaxis: { title: { text: "Interval end (UTC)" }, gridcolor: "#e7e9e3" }, yaxis: { title: { text: readings[0]?.unit ?? "Energy per interval" }, gridcolor: "#e7e9e3" }, legend: { orientation: "h" as const, y: -0.28 } },
      config: { displayModeBar: false, responsive: true },
    };
  }, [forecast, readings, readingStatus]);

  async function sendChat(event: React.FormEvent) {
    event.preventDefault();
    if (!chatText.trim()) return;
    setChatBusy(true);
    try {
      const response = await fetch("/api/v1/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: chatText, building_id: selectedId, scenario: scenarioConfig }) });
      const result = await response.json() as ChatResult;
      if (!response.ok) throw new Error(result.answer ?? response.statusText);
      setChatResult(result);
    } catch (error) {
      setChatResult({ answer: error instanceof Error ? error.message : "Chat request failed", evidence_status: "reference", tool_calls: [], assumptions: [], source_freshness: null });
    } finally {
      setChatBusy(false);
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">SDG 11 · Sustainable campus research prototype</p>
          <h1>MLRIT Campus Twin</h1>
          <p className="subtitle">Explore building-level resources, forecasts, and simulated scenarios.</p>
        </div>
        <div className="status-group"><span className="live-dot" /> Local research demo</div>
      </header>

      {apiError && <div className="notice" role="status">{apiError}. Start the API at port 8000 to load campus data.</div>}

      <section className="hero-grid">
        <div className="panel campus-panel">
          <div className="panel-heading"><div><p className="eyebrow">Campus view</p><h2>Explore MLRIT in 3D</h2></div><span className="badge badge-map">Interactive 3D campus</span></div>
          <CampusScene buildings={buildings} selectedId={selectedId} onSelect={setSelectedId} resourceMode={meterType} evidenceStatus={readingStatus} />
          <div className="source-note">Campus anchor: <a href="https://files.mlrit.ac.in/classrooms/2021-22/MG104.pdf" target="_blank" rel="noreferrer">2021 institutional disclosure</a>. This local scene stays stable without map tiles; building locations, footprints, and heights are illustrative.</div>
        </div>

        <aside className="panel building-panel">
          <div className="panel-heading"><div><p className="eyebrow">Selected entity</p><h2>{selected?.name ?? "Select a building"}</h2></div><span className="badge badge-synthetic">Approximate overlay</span></div>
          {selected ? <div className="building-facts">
            <div><span>Reported built area</span><strong>{selected.reported_area_m2?.toLocaleString() ?? "Not reported"}{selected.reported_area_m2 ? " m²" : ""}</strong></div>
            <div><span>Floors</span><strong>{selected.floor_count ?? "Unknown"}</strong></div>
            <div><span>Building metadata</span><strong>Institutional report, 2023</strong></div>
          </div> : <p className="muted">Choose a building from the 3D campus or the building list.</p>}
          <label className="field-label" htmlFor="meter-select">Resource meter</label>
          <select id="meter-select" value={meterType} onChange={(event) => setMeterType(event.target.value)}>
            <option value="electricity">Electricity</option><option value="chilled_water">Chilled water</option><option value="steam">Steam</option><option value="hot_water">Hot water</option><option value="solar">Solar</option><option value="water">Water</option>
          </select>
          <div className="evidence-banner"><strong>{statusLabel[readingStatus]}</strong><span>Seeded local example · no college meter records are connected</span></div>
        </aside>
      </section>

      <section className="panel forecast-panel">
        <div className="panel-heading"><div><p className="eyebrow">Forecast and trust</p><h2>Recent intervals and next-day baseline</h2></div>{forecast && <span className="badge badge-predicted">{forecast.model_name}</span>}</div>
        <div className="chart-wrap"><Suspense fallback={<p className="muted">Loading forecast chart…</p>}><Plot data={plot.data} layout={plot.layout} config={plot.config} useResizeHandler style={{ width: "100%", height: "360px" }} /></Suspense></div>
        <div className="chart-foot"><span>Source status: {forecast ? statusLabel[forecast.source_evidence_status] : "waiting for API"}</span><span>Coverage: {forecast?.metrics.n ?? 0} backtest intervals</span><span>Prediction band is empirical, not a deployment guarantee</span></div>
        {forecast?.limitations.map((line) => <p className="muted small" key={line}>{line}</p>)}
      </section>

      <ScenarioPanel onConfigChange={setScenarioConfig} />

      <section className="lower-grid">
        <div className="panel building-list-panel">
          <div className="panel-heading"><div><p className="eyebrow">Campus entities</p><h2>Buildings</h2></div><span className="badge">{buildings.length} records</span></div>
          <div className="building-list">{buildings.map((building) => <button className={`building-row ${building.id === selectedId ? "selected" : ""}`} key={building.id} onClick={() => setSelectedId(building.id)}><span className="building-mark">{building.name.slice(0, 1)}</span><span><strong>{building.name}</strong><small>{building.reported_area_m2?.toLocaleString()} m² reported · floors unknown</small></span><span className="chevron">›</span></button>)}</div>
        </div>
        <div className="panel assistant-panel">
          <div className="panel-heading"><div><p className="eyebrow">Evidence-backed assistant</p><h2>Ask the twin</h2></div><span className="badge badge-reference">Read-only tools</span></div>
          <form className="chat-form" onSubmit={sendChat}><textarea value={chatText} onChange={(event) => setChatText(event.target.value)} placeholder="Ask about energy use or data freshness for this building…" rows={3} /><button type="submit" disabled={chatBusy}>{chatBusy ? "Working…" : "Ask"}</button></form>
          <p className="prompt-example">Try: “Show energy usage for {selected?.id ?? "sr-block"}”. Run the what-if form above first to give chat the same explicit scenario limits.</p>
          {chatResult && <div className="chat-result"><div className="panel-heading"><span className={`badge badge-${chatResult.evidence_status}`}>{statusLabel[chatResult.evidence_status]}</span><span className="muted small">Freshness: {chatResult.source_freshness ? new Date(chatResult.source_freshness).toLocaleString() : "not available"}</span></div><p>{chatResult.answer}</p>{chatResult.tool_calls.length > 0 && <p className="muted small">Tools: {chatResult.tool_calls.join(", ")}</p>}<ul>{chatResult.assumptions.map((item) => <li key={item}>{item}</li>)}</ul></div>}
          <p className="safety-note">Scenario outputs are simulated estimates. Chat cannot control equipment.</p>
        </div>
      </section>

      <footer><span>MLRIT Sustainable Campus Twin · research prototype</span><span>Measured data, predictions, and simulations are labelled separately.</span></footer>
    </main>
  );
}



