// Scenario "filter": the structured filters, category_id with active, 50 rows.
import { buildOptions, get, range } from "./lib.js";
export { handleSummary } from "./lib.js";

export const options = buildOptions();

export default function () {
  get(`/v1/products?category_id=${range(1, 30)}&active=true&size=50`, [200]);
}
