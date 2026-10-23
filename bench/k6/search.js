// Scenario "search": the free-text filter `q` over the bench products' names.
import { buildOptions, get, pick } from "./lib.js";
export { handleSummary } from "./lib.js";

export const options = buildOptions();

const TERMS = [
  "milk",
  "bread",
  "premium",
  "organic%20juice",
  "family%20soap",
  "charger",
  "salmon",
  "classic%20peas",
];

export default function () {
  get(`/v1/products?q=${pick(TERMS)}&size=20`, [200]);
}
