// Scenario "list": a page of the catalog, 20 rows, pages 1-500 of the 10,000
// bench products (ADR-0042).
import { buildOptions, get, range } from "./lib.js";
export { handleSummary } from "./lib.js";

export const options = buildOptions();

export default function () {
  get(`/v1/products?page=${range(1, 500)}&size=20`, [200]);
}
