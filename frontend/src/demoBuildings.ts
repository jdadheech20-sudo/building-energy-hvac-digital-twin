import seedRows from "../../campus_twin/data/mlrit_buildings.json";
import type { Building } from "./types";

type SeedBuilding = Omit<Building, "layout_status">;

export const demoBuildings: Building[] = (seedRows as SeedBuilding[]).map((building) => ({
  ...building,
  layout_status: "schematic",
}));
