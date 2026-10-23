// Scenario "detail": one product by id. 5% of the ids do not exist, and their
// 404 is an expected answer, not an error.
import { buildOptions, get, range } from "./lib.js";
export { handleSummary } from "./lib.js";

export const options = buildOptions();

export default function () {
  if (Math.random() < 0.05) {
    get(`/v1/products/${range(900000, 999999)}`, [404]);
  } else {
    get(`/v1/products/${range(100001, 110000)}`, [200]);
  }
}
