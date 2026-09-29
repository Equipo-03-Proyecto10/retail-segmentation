/* The segmentation dashboard's four charts (F12-01).
 *
 * Reads the JSON a same-origin <script type="application/json"> block carries
 * -- inert data, never executed -- and calls Highcharts against it. Nothing
 * here fetches anything: the page is server-rendered with its data already
 * embedded, per docs/roadmap.md's "dashboards constraint, resolved by the
 * second delivery".
 */
(function () {
  "use strict";

  // Choosing a run shows it at once; the Show button remains for when this
  // script does not run. Attached here, not as an onchange attribute: an
  // inline handler is script the page's same-origin script-src refuses.
  var picker = document.getElementById("run");
  if (picker && picker.form) {
    picker.addEventListener("change", function () {
      picker.form.submit();
    });
  }

  var node = document.getElementById("mq-dashboard-data");
  if (!node) return;
  var data = JSON.parse(node.textContent);

  if (!window.Highcharts) {
    document.querySelectorAll(".mq-chart").forEach(function (el) {
      el.innerHTML =
        '<div class="mq-state mq-state--error">' +
        '<span class="mq-state__mark">Offline</span>' +
        '<span class="mq-state__text">The chart library did not load.</span>' +
        "</div>";
    });
    return;
  }

  if (window.MOSAIQCharts) {
    window.MOSAIQCharts.apply();
  }
  var categorical = window.MOSAIQCharts ? window.MOSAIQCharts.categorical() : undefined;
  var sequentialStops = window.MOSAIQCharts
    ? window.MOSAIQCharts.sequentialStops()
    : undefined;

  function bar(elementId, title, series) {
    Highcharts.chart(elementId, {
      chart: { type: "column" },
      xAxis: { categories: series.map(function (p) { return p.name; }), tickLength: 0 },
      yAxis: { title: { text: title } },
      legend: { enabled: false },
      series: [
        {
          name: title,
          data: series.map(function (p, i) {
            return { y: p.y, color: categorical ? categorical[i % categorical.length] : undefined };
          }),
        },
      ],
    });
  }

  bar("mq-chart-sizes", "Customers", data.sizes);
  bar("mq-chart-revenue", "Revenue", data.revenue);

  var quintile = ["Q1", "Q2", "Q3", "Q4", "Q5"];
  Highcharts.chart("mq-chart-rfm", {
    chart: { type: "heatmap", spacing: [8, 8, 4, 4] },
    xAxis: { categories: quintile, title: { text: "Frequency quintile" }, gridLineWidth: 0, tickLength: 0 },
    yAxis: { categories: quintile, title: { text: "Recency quintile" }, gridLineWidth: 0, reversed: true },
    colorAxis: { stops: sequentialStops, min: 0 },
    legend: { align: "right", layout: "vertical", verticalAlign: "middle", symbolHeight: 160, margin: 8 },
    series: [
      {
        name: "Customers",
        data: data.heatmap,
        dataLabels: { enabled: true, format: "{point.value}" },
      },
    ],
  });

  if (data.migration) {
    Highcharts.chart("mq-chart-migration", {
      chart: { type: "sankey" },
      tooltip: {
        pointFormat: "{point.fromNode.name} \u2192 {point.toNode.name}: <b>{point.weight}</b> customers",
      },
      series: [
        {
          keys: ["from", "to", "weight"],
          nodes: data.migration.nodes,
          data: data.migration.data,
        },
      ],
    });
  }
})();
