export type EvidenceStatus = "reference" | "measured" | "synthetic" | "predicted" | "simulated" | "mixed";

export interface Building {
  id: string;
  name: string;
  category: string;
  reported_area_m2: number | null;
  floor_count: number | null;
  layout_status: string;
  position_x: number;
  position_z: number;
  metadata_source: string;
  metadata_note: string | null;
}

export interface Reading {
  id: number;
  building_id: string;
  meter_type: string;
  interval_start: string;
  interval_end: string;
  value: number;
  unit: string;
  source_id: string;
  evidence_status: EvidenceStatus;
  quality_flags: string[];
  provenance: Record<string, unknown>;
}

export interface Forecast {
  building_id: string;
  model_name: string;
  source_evidence_status: EvidenceStatus;
  method: string;
  interval_hours: number;
  metrics: Record<string, number>;
  points: Array<{ timestamp: string; value: number; lower: number; upper: number; meter_type: string; unit: string; evidence_status: EvidenceStatus }>;
  limitations: string[];
}

export interface ChatResult {
  answer: string;
  evidence_status: EvidenceStatus;
  tool_calls: string[];
  assumptions: string[];
  source_freshness: string | null;
}

export interface ScenarioCandidate {
  heating_setpoint_c: number;
  cooling_setpoint_c: number;
  energy_kwh: number;
  cost: number;
  carbon_kg: number;
  comfort_degree_hours: number;
  peak_power_kw: number;
  feasible: boolean;
}

export interface ScenarioResult {
  evidence_status: EvidenceStatus;
  algorithm: string;
  source_note: string;
  feasible: boolean;
  candidates: ScenarioCandidate[];
  limitations: string[];
}

export interface ScenarioInput {
  horizon_days: number;
  tariff_per_kwh: number;
  carbon_factor_kg_per_kwh: number;
  cost_budget: number;
  comfort_limit_degree_hours: number;
  max_peak_power_kw: number;
  heating_setpoint_bounds_c: [number, number];
  cooling_setpoint_bounds_c: [number, number];
  equipment_max_heating_kw: number;
  equipment_max_cooling_kw: number;
  source_note: string;
}
