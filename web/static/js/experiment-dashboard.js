/* The experiment dashboard's three charts (AN-03).
 *
 * Reads the JSON a same-origin <script type="application/json"> block carries
 * -- inert data, never executed -- and calls Highcharts against it. Nothing
 * here fetches anything: the page is server-rendered with its data already
 * embedded, as the segmentation dashboard is.
 *
 * When the experiment's data is not observed, every chart says Synthetic in its
 * own subtitle, so the mark survives a screenshot of the chart alone.
 */
(function () {
  "use strict";

  // Choosing an experiment shows it at once; the Show button remains for when
  // this script does not run. Attached here, not as an onchange attribute: an
  // inline handler is script the page's same-origin script-src refuses.
  var picker = document.getElementById("experiment");
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

  // Over time, one colour per arm. The rate chart colours by measure instead
  // (assigned, exposed), so its legend names what each bar is.
  function armColor(index) {
    return categorical ? categorical[index % categorical.length] : undefined;
  }

  var subtitle = data.experiment.synthetic
    ? { text: data.experiment.label, style: { fontWeight: "600" } }
    : { text: undefined };

  // The theme's phone-width rule shows every second axis label, which suits a
  // long axis and hides an arm's name here, where there are two or three. Keep
  // the theme's rule and add one after it that shows them all.
  var everyArmNamed = (function () {
    var themed = (Highcharts.getOptions().responsive || {}).rules || [];
    return {
      rules: themed.concat([
        { condition: { maxWidth: 480 }, chartOptions: { xAxis: { labels: { step: 1 } } } },
      ]),
    };
  })();

  function percent(value) {
    return value === null || value === undefined ? "–" : value.toFixed(2) + "%";
  }

  // 1. Conversion rate per arm: over everyone assigned, and over the exposed.
  Highcharts.chart("mq-chart-rates", {
    chart: { type: "column" },
    subtitle: subtitle,
    responsive: everyArmNamed,
    xAxis: {
      categories: data.rates.categories,
      tickLength: 0,
    },
    yAxis: { title: { text: "Conversion rate (%)" }, min: 0 },
    legend: { enabled: true },
    tooltip: {
      shared: true,
      formatter: function () {
        var lines = ["<b>" + this.x + "</b>"];
        this.points.forEach(function (p) {
          lines.push(p.series.name + ": <b>" + percent(p.y) + "</b>");
        });
        return lines.join("<br>");
      },
    },
    series: [
      {
        name: "Assigned (intent to treat)",
        color: armColor(0),
        data: data.rates.assigned,
      },
      {
        name: "Exposed",
        color: armColor(1),
        data: data.rates.exposed,
      },
    ],
  });

  // 2. Difference from the baseline arm, with its 95% interval.
  if (data.difference) {
    var what = data.difference.baseline.kind === "control" ? "Uplift" : "Difference";
    Highcharts.chart("mq-chart-difference", {
      chart: { type: "column" },
      subtitle: subtitle,
      responsive: everyArmNamed,
      xAxis: { categories: data.difference.categories, tickLength: 0 },
      yAxis: {
        title: { text: what + " from " + data.difference.baseline.name + " (points)" },
        plotLines: [{ value: 0, width: 1, zIndex: 3, color: "#808080" }],
      },
      legend: { enabled: false },
      tooltip: {
        shared: true,
        formatter: function () {
          var i = this.points[0].point.index;
          return (
            "<b>" + this.x + "</b><br>" + what + ": <b>" +
            data.difference.points[i].toFixed(2) + " points</b><br>95% interval: " +
            data.difference.low[i].toFixed(2) + " to " + data.difference.high[i].toFixed(2)
          );
        },
      },
      series: [
        {
          name: what,
          data: data.difference.points.map(function (y) {
            return { y: y, color: categorical ? categorical[1] : undefined };
          }),
        },
        {
          type: "errorbar",
          name: "95% interval",
          data: data.difference.low.map(function (low, i) {
            return [low, data.difference.high[i]];
          }),
        },
      ],
    });
  }

  // 3. Conversions over time, within the conversion window.
  if (data.over_time) {
    var days = [];
    for (var d = 0; d < data.over_time.drawn_days; d++) days.push(d + 1);
    Highcharts.chart("mq-chart-over-time", {
      chart: { type: "line" },
      subtitle: subtitle,
      xAxis: {
        categories: days.map(String),
        title: { text: "Days since assignment (window: " + data.over_time.window_days + ")" },
        tickInterval: Math.max(1, Math.ceil(days.length / 10)),
      },
      yAxis: { title: { text: "Converted so far (% of assigned)" }, min: 0 },
      legend: { enabled: true },
      tooltip: { shared: true, valueSuffix: "%", valueDecimals: 2 },
      series: data.over_time.series.map(function (s, i) {
        return { name: s.name, data: s.data, color: armColor(i) };
      }),
    });
  }
})();
