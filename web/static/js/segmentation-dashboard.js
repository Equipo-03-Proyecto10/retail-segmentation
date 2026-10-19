/* The segmentation dashboard's five charts (F12-01, #537).
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

  // Monetary on the same grid: coloured by mean spend per customer, with the
  // cell's customers and total in the tooltip. An empty cell is null, drawn as
  // the null colour with a dash, never as a spend of zero.
  function compact(value) {
    if (value >= 1e6) return (value / 1e6).toFixed(1) + "M";
    if (value >= 1e4) return Math.round(value / 1e3) + "k";
    if (value >= 1e3) return (value / 1e3).toFixed(1) + "k";
    return String(Math.round(value));
  }
  Highcharts.chart("mq-chart-rfm-spend", {
    chart: { type: "heatmap", spacing: [8, 8, 4, 4] },
    xAxis: { categories: quintile, title: { text: "Frequency quintile" }, gridLineWidth: 0, tickLength: 0 },
    yAxis: { categories: quintile, title: { text: "Recency quintile" }, gridLineWidth: 0, reversed: true },
    colorAxis: { stops: sequentialStops, min: 0, labels: { format: "{value:,.0f}" } },
    legend: {
      align: "right",
      layout: "vertical",
      verticalAlign: "middle",
      symbolHeight: 160,
      margin: 8,
      title: { text: "MXN" },
    },
    tooltip: {
      formatter: function () {
        var p = this.point;
        var where = "R " + quintile[p.y] + " \u00b7 F " + quintile[p.x];
        if (p.value === null) return where + "<br>No customers";
        return (
          where +
          "<br>Mean spend: <b>" + Highcharts.numberFormat(p.value, 2) + " MXN</b>" +
          "<br>" + p.customers + " customers, " + Highcharts.numberFormat(p.total, 2) + " MXN in total"
        );
      },
    },
    series: [
      {
        name: "Mean spend (MXN)",
        data: data.rfm_spend,
        nullColor: "transparent",
        dataLabels: {
          enabled: true,
          formatter: function () {
            return this.point.value === null ? "\u2013" : compact(this.point.value);
          },
        },
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
