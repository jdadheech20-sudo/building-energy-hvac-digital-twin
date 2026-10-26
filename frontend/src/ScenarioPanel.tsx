import { useState, type FormEvent } from "react";
import type { ScenarioInput, ScenarioResult } from "./types";

interface ScenarioPanelProps {
  onConfigChange: (config: ScenarioInput) => void;
}

const fields = [
  ["horizon_days", "Horizon (days)", "1", "1", "14", "1"],
  ["tariff_per_kwh", "Electricity tariff per kWh", "", "0", undefined, "0.01"],
  ["carbon_factor_kg_per_kwh", "Carbon factor kg CO₂ per kWh", "", "0", undefined, "0.01"],
  ["cost_budget", "Scenario cost budget", "", "0.01", undefined, "1"],
  ["comfort_limit_degree_hours", "Comfort limit degree-hours", "", "0", undefined, "0.1"],
  ["max_peak_power_kw", "Peak power limit kW", "", "0.01", undefined, "0.1"],
  ["heat_min", "Heating setpoint minimum °C", "", "10", "28", "0.5"],
  ["heat_max", "Heating setpoint maximum °C", "", "10", "28", "0.5"],
  ["cool_min", "Cooling setpoint minimum °C", "", "16", "35", "0.5"],
  ["cool_max", "Cooling setpoint maximum °C", "", "16", "35", "0.5"],
  ["equipment_max_heating_kw", "Heating equipment limit kW", "", "0.01", undefined, "0.1"],
  ["equipment_max_cooling_kw", "Cooling equipment limit kW", "", "0.01", undefined, "0.1"],
] as const;

export default function ScenarioPanel({ onConfigChange }: ScenarioPanelProps) {
  const [result, setResult] = useState<ScenarioResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function runScenario(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const number = (key: string) => Number(form.get(key));
    const body: ScenarioInput = {
      horizon_days: number("horizon_days"),
      tariff_per_kwh: number("tariff_per_kwh"),
      carbon_factor_kg_per_kwh: number("carbon_factor_kg_per_kwh"),
      cost_budget: number("cost_budget"),
      comfort_limit_degree_hours: number("comfort_limit_degree_hours"),
      max_peak_power_kw: number("max_peak_power_kw"),
      heating_setpoint_bounds_c: [number("heat_min"), number("heat_max")],
      cooling_setpoint_bounds_c: [number("cool_min"), number("cool_max")],
      equipment_max_heating_kw: number("equipment_max_heating_kw"),
      equipment_max_cooling_kw: number("equipment_max_cooling_kw"),
      source_note: String(form.get("source_note") ?? ""),
    };
    onConfigChange(body);
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/v1/scenarios/optimize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? response.statusText);
      setResult(data as ScenarioResult);
    } catch (reason) {
      setResult(null);
      setError(reason instanceof Error ? reason.message : "Scenario request failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel scenario-panel">
      <div className="panel-heading">
        <div><p className="eyebrow">What-if simulator</p><h2>Compare simulated setpoints</h2></div>
        <span className="badge badge-simulated">Simulation only</span>
      </div>
      <p className="muted scenario-intro">Enter the assumptions and limits you want to evaluate. The model uses the synthetic 1R1C building reference and will reject or mark infeasible candidates that break these limits.</p>
      <form className="scenario-form" onSubmit={runScenario}>
        <div className="scenario-fields">
          {fields.map(([name, label, value, min, max, step]) => (
            <label key={name}>{label}<input name={name} type="number" required defaultValue={value || undefined} min={min} max={max} step={step} /></label>
          ))}
        </div>
        <label className="scenario-note-label">Assumption provenance<textarea name="source_note" required minLength={4} placeholder="Describe where these tariff, carbon, comfort, and equipment assumptions came from." rows={2} /></label>
        <button type="submit" disabled={busy}>{busy ? "Simulating…" : "Run simulation"}</button>
      </form>
      {error && <div className="notice" role="status">Scenario not run: {error}</div>}
      {result && <div className="scenario-result" role="status">
        <div className="panel-heading"><strong>{result.feasible ? "Feasible candidates found" : "No feasible candidate"}</strong><span className="badge badge-simulated">{result.evidence_status}</span></div>
        <p className="muted small">{result.algorithm} · Assumptions: {result.source_note} · synthetic single-zone simulation</p>
        {result.candidates.length > 0 && <div className="scenario-table-wrap"><table className="scenario-table"><thead><tr><th>Heat °C</th><th>Cool °C</th><th>Energy kWh</th><th>Cost</th><th>Carbon kg</th><th>Comfort °C·h</th><th>Feasible</th></tr></thead><tbody>{result.candidates.slice(0, 8).map((candidate, index) => <tr key={`${candidate.heating_setpoint_c}-${candidate.cooling_setpoint_c}-${index}`}><td>{candidate.heating_setpoint_c.toFixed(1)}</td><td>{candidate.cooling_setpoint_c.toFixed(1)}</td><td>{candidate.energy_kwh.toFixed(1)}</td><td>{candidate.cost.toFixed(2)}</td><td>{candidate.carbon_kg.toFixed(1)}</td><td>{candidate.comfort_degree_hours.toFixed(1)}</td><td>{candidate.feasible ? "Yes" : "No"}</td></tr>)}</tbody></table></div>}
        <ul>{result.limitations.map((line) => <li key={line}>{line}</li>)}</ul>
      </div>}
    </section>
  );
}
