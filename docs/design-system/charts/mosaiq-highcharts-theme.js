/* MOSAIQ Highcharts theme.
   Every value is read from the CSS custom properties in tokens/, so the theme
   follows the light/dark override automatically. Load Highcharts, then this file,
   then call MOSAIQCharts.apply() — or just let it self-apply on DOMContentLoaded.
   No colour literals live in this file. */
(function (global) {
  "use strict";

  function token(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v && v.trim()) || fallback;
  }
  function tokens(prefix, count, pad) {
    var out = [];
    for (var i = 1; i <= count; i++) {
      out.push(token(prefix + (pad ? String(i).padStart(2, "0") : i)));
    }
    return out;
  }

  var API = {
    /** 8 cluster colours — stacked area, scatter, sankey, any nominal series.
        Eight is the ceiling of the colour-vision-safe scheme; past that, split the
        chart or encode by shape and label. */
    categorical: function () { return tokens("--mq-cat-", 8, true); },
    /** Fill for missing, excluded or "other" buckets. Never a real series. */
    badData: function () { return token("--mq-cat-bad"); },
    /** 6 ordinal steps — RFM heatmaps, quintile choropleths. */
    sequential: function () { return tokens("--mq-seq-", 6, false); },
    /** 9 steps with a neutral midpoint — uplift, net migration. */
    diverging: function () { return tokens("--mq-div-", 9, false); },
    /** Stops in Highcharts colorAxis form. */
    sequentialStops: function () {
      return API.sequential().map(function (c, i, a) { return [i / (a.length - 1), c]; });
    },
    divergingStops: function () {
      return API.diverging().map(function (c, i, a) { return [i / (a.length - 1), c]; });
    },

    options: function () {
      var sans = token("--mq-font-sans");
      var mono = token("--mq-font-mono");
      var text = token("--mq-text-primary");
      var secondary = token("--mq-text-secondary");
      var grid = token("--mq-chart-gridline");
      var axisLine = token("--mq-chart-axis-line");
      var axisLabel = token("--mq-chart-axis-label");
      var surface = token("--mq-chart-tooltip-bg");
      var tipBorder = token("--mq-chart-tooltip-border");
      var radius = parseInt(token("--mq-radius-2"), 10) || 4;

      var axis = {
        lineColor: axisLine,
        tickColor: axisLine,
        gridLineColor: grid,
        gridLineWidth: 1,
        gridLineDashStyle: "Solid",
        labels: { style: { color: axisLabel, fontFamily: mono, fontSize: "11px" } },
        title: { style: { color: secondary, fontFamily: sans, fontSize: "11px", fontWeight: "500" } },
        crosshair: { color: axisLine, width: 1, dashStyle: "Dot" }
      };

      return {
        colors: API.categorical(),
        chart: {
          backgroundColor: token("--mq-chart-plot-bg"),
          plotBorderWidth: 0,
          spacing: [8, 4, 4, 4],
          style: { fontFamily: sans, fontSize: "12px" },
          animation: { duration: parseInt(token("--mq-duration-base"), 10) || 140 }
        },
        title: { text: undefined },
        subtitle: { text: undefined },
        credits: { enabled: false },
        xAxis: axis,
        yAxis: Object.assign({}, axis, { lineWidth: 0, tickWidth: 0 }),
        colorAxis: { gridLineColor: grid, labels: { style: { color: axisLabel, fontFamily: mono, fontSize: "11px" } } },
        tooltip: {
          backgroundColor: surface,
          borderColor: tipBorder,
          borderWidth: 1,
          borderRadius: radius,
          shadow: false,
          padding: 8,
          useHTML: true,
          style: { color: text, fontFamily: sans, fontSize: "12px" },
          headerFormat: '<span style="font-family:' + mono + ';font-size:11px;color:' + secondary + '">{point.key}</span><br>'
        },
        legend: {
          align: "left",
          verticalAlign: "bottom",
          margin: 12,
          padding: 0,
          symbolHeight: 8,
          symbolWidth: 8,
          symbolRadius: 1,
          itemDistance: 14,
          itemStyle: { color: secondary, fontFamily: sans, fontSize: "12px", fontWeight: "400" },
          itemHoverStyle: { color: text },
          itemHiddenStyle: { color: token("--mq-text-disabled") }
        },
        plotOptions: {
          series: {
            animation: false,
            borderWidth: 0,
            states: { hover: { brightness: -0.08 }, inactive: { opacity: 0.25 } },
            dataLabels: { style: { fontFamily: mono, fontSize: "11px", fontWeight: "500", textOutline: "none", color: text } }
          },
          /* Marker defaults are set per type: a global series.marker leaks into
             heatmap cells and renders them as circles. */
          line: { marker: { lineWidth: 1, lineColor: surface, radius: 3, symbol: "circle" } },
          spline: { marker: { lineWidth: 1, lineColor: surface, radius: 3, symbol: "circle" } },
          area: { fillOpacity: 0.9, lineWidth: 1, marker: { enabled: false } },
          areaspline: { fillOpacity: 0.9, lineWidth: 1, marker: { enabled: false } },
          column: { pointPadding: 0.05, groupPadding: 0.12, borderRadius: 1 },
          bar: { pointPadding: 0.05, groupPadding: 0.12, borderRadius: 1 },
          heatmap: { borderWidth: 1, borderColor: surface, dataLabels: { enabled: true } },
          scatter: { marker: { radius: 3, symbol: "circle", lineWidth: 1, lineColor: surface, states: { hover: { radiusPlus: 2 } } } },
          sankey: { nodePadding: 8, nodeWidth: 10, linkOpacity: 0.42, curveFactor: 0.4, dataLabels: { style: { fontFamily: sans, fontSize: "11px", fontWeight: "500" } } },
          errorbar: { color: token("--mq-text-secondary"), whiskerLength: "40%", lineWidth: 1 }
        },
        /* 320px-wide rules: axis titles and legends are the first things to go. */
        responsive: {
          rules: [{
            condition: { maxWidth: 480 },
            chartOptions: {
              chart: { spacing: [6, 2, 2, 2] },
              legend: { itemDistance: 8, itemStyle: { fontSize: "11px" } },
              xAxis: { title: { text: null }, labels: { step: 2, style: { fontSize: "10px" } } },
              yAxis: { title: { text: null }, labels: { style: { fontSize: "10px" } } },
              plotOptions: { series: { dataLabels: { enabled: false } } }
            }
          }]
        }
      };
    },

    apply: function () {
      if (!global.Highcharts) return false;
      global.Highcharts.setOptions(API.options());
      return true;
    }
  };

  global.MOSAIQCharts = API;
  /* Self-applies as soon as Highcharts is present. Call MOSAIQCharts.apply()
     explicitly if this file is loaded before Highcharts. */
  if (!API.apply()) document.addEventListener("DOMContentLoaded", API.apply);
})(window);
