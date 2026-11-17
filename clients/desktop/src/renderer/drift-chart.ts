import type { BarChartModel, GaugeModel } from '../shared/drift';
import { LEVEL_LABEL } from '../shared/drift';
import { el, testid } from './dom';

// The drift module's charts (F28-07, #876), drawn as SVG elements built one by one with
// createElementNS: no markup string, no innerHTML, no script and no stylesheet of their own, so
// they run under the console's CSP (`script-src 'self'`, `style-src 'self'`; presentation is by
// attribute and by the classes of styles.css). Every label is set with textContent. Two series
// only (the reference and the current), in the first two categorical slots, with a legend, a
// hairline grid, thin columns rounded at the data end and a readout that gives on hover or focus
// what the table beside the chart gives always.

const SVG = 'http://www.w3.org/2000/svg';

type Attrs = Record<string, string | number>;

function node<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attrs: Attrs = {},
  ...children: Node[]
): SVGElementTagNameMap[K] {
  const n = document.createElementNS(SVG, tag);
  for (const [name, value] of Object.entries(attrs)) n.setAttribute(name, String(value));
  n.append(...children);
  return n;
}

function label(content: string, attrs: Attrs, cls = 'viz-text'): SVGTextElement {
  const t = node('text', { ...attrs, class: cls });
  t.textContent = content;
  return t;
}

const WIDTH = 640;
const PLOT = { left: 48, right: 12, top: 12, height: 200, bottom: 58 };
const BAR_MAX = 24;
const RADIUS = 4;
const GAP = 2;

/** A column square at its baseline and rounded at its top, as a path. */
function columnPath(x: number, baseline: number, width: number, height: number): string {
  const r = Math.min(RADIUS, height / 2, width / 2);
  const top = baseline - height;
  return `M${x},${baseline} L${x},${top + r} Q${x},${top} ${x + r},${top} L${x + width - r},${top} Q${x + width},${top} ${x + width},${top + r} L${x + width},${baseline} Z`;
}

function cut(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, Math.max(1, max - 1))}…`;
}

function legendItem(cls: string, name: string): HTMLElement {
  return el('li', {}, el('span', { cls: `swatch ${cls}` }), el('span', { text: name }));
}

/**
 * Columns in pairs, the reference beside the current, on one axis from zero. The chart gives each
 * pair its values on hover and on keyboard focus; the figures are always in the table beside it.
 */
export function barChart(
  model: BarChartModel,
  names: { reference: string; current: string },
  id: string,
): HTMLElement {
  const height = PLOT.top + PLOT.height + PLOT.bottom;
  const plotWidth = WIDTH - PLOT.left - PLOT.right;
  const baseline = PLOT.top + PLOT.height;
  const slot = plotWidth / Math.max(1, model.groups.length);
  const width = Math.max(4, Math.min(BAR_MAX, (slot * 0.7 - GAP) / 2));
  const readout = testid(
    el('p', { cls: 'viz-readout', text: 'Hover or focus a pair to read its values.' }),
    `${id}-readout`,
  );
  readout.setAttribute('role', 'status');

  const svg = node('svg', {
    viewBox: `0 0 ${WIDTH} ${height}`,
    class: 'viz-svg',
    role: 'group',
    'aria-label': `${model.title}. ${names.reference} and ${names.current}, one pair for each of ${model.groups.length} groups.`,
  });
  svg.append(node('title', {}, document.createTextNode(model.title)));

  for (const tick of model.ticks) {
    const y = baseline - (tick / model.max) * PLOT.height;
    svg.append(
      node('line', {
        x1: PLOT.left,
        x2: WIDTH - PLOT.right,
        y1: y,
        y2: y,
        class: tick === 0 ? 'viz-axis' : 'viz-grid',
      }),
      label(
        `${Math.round(tick * 100)}%`,
        { x: PLOT.left - 6, y: y + 4, 'text-anchor': 'end' },
        'viz-text viz-tick',
      ),
    );
  }

  model.groups.forEach((g, i) => {
    const centre = PLOT.left + slot * (i + 0.5);
    const summary = `${g.label}${g.detail === '' ? '' : ` (${g.detail})`}: ${names.reference} ${g.referenceText}, ${names.current} ${g.currentText}`;
    const group = node('g', { class: 'viz-pair', tabindex: 0, role: 'img', 'aria-label': summary });
    group.dataset.group = g.key;
    const show = (): void => {
      readout.textContent = summary;
    };
    group.addEventListener('pointerenter', show);
    group.addEventListener('focus', show);
    group.append(
      node('title', {}, document.createTextNode(summary)),
      // The hit area is the whole column of the chart for this pair, never only the painted bars.
      node('rect', {
        x: centre - slot / 2,
        y: PLOT.top,
        width: slot,
        height: PLOT.height + PLOT.bottom - 6,
        class: 'viz-hit',
      }),
    );
    const bars: [number, string, number][] = [
      [centre - width - GAP / 2, 'viz-ref', g.reference],
      [centre + GAP / 2, 'viz-cur', g.current],
    ];
    for (const [x, cls, value] of bars) {
      const h = (value / model.max) * PLOT.height;
      // A share of zero draws nothing: the axis is the zero.
      if (h > 0) group.append(node('path', { d: columnPath(x, baseline, width, h), class: cls }));
    }
    group.append(
      label(cut(g.label, Math.floor(slot / 6.5)), { x: centre, y: baseline + 16, 'text-anchor': 'middle' }),
    );
    if (g.detail !== '' && slot >= 70)
      group.append(
        label(
          cut(g.detail, Math.floor(slot / 5.5)),
          { x: centre, y: baseline + 30, 'text-anchor': 'middle' },
          'viz-text viz-tick',
        ),
      );
    svg.append(group);
  });

  const legend = el(
    'ul',
    { cls: 'viz-legend' },
    legendItem('viz-ref', names.reference),
    legendItem('viz-cur', names.current),
  );
  const figure = el(
    'figure',
    { cls: 'viz' },
    el('figcaption', { text: model.title }),
    legend,
    el('div', { cls: 'scroll' }, svg),
    readout,
  );
  return testid(figure, id);
}

const METER_WIDTH = 320;
const METER_LEFT = 4;
const METER_RIGHT = 8;

/**
 * One PSI on its scale: a bar filled to the value, in the colour of its level, with a tick and a
 * word at the watch and the alarm thresholds. The level is also in the words beside it, so the
 * colour is never the only carrier.
 */
export function meter(model: GaugeModel, name: string, id: string): HTMLElement {
  const span = METER_WIDTH - METER_LEFT - METER_RIGHT;
  const at = (v: number): number => METER_LEFT + (Math.min(v, model.max) / model.max) * span;
  const crowded = at(model.alarm) - at(model.watch) < 84;
  const svg = node('svg', {
    viewBox: `0 0 ${METER_WIDTH} ${crowded ? 62 : 46}`,
    class: 'viz-meter',
    role: 'img',
    'aria-label': `${name}: ${model.value} on a scale with a watch threshold at ${model.watch} and an alarm threshold at ${model.alarm}. Level: ${LEVEL_LABEL[model.level]}.`,
  });
  svg.append(
    node('rect', { x: METER_LEFT, y: 6, width: span, height: 8, rx: 4, class: 'viz-track' }),
    node('rect', {
      x: METER_LEFT,
      y: 6,
      width: Math.max(2, at(model.value) - METER_LEFT),
      height: 8,
      rx: 4,
      class: `viz-fill viz-level-${model.level.toLowerCase()}`,
    }),
  );
  const marks: [number, string, number][] = [
    [model.watch, `watch ${model.watch}`, 30],
    [model.alarm, `alarm ${model.alarm}`, crowded ? 46 : 30],
  ];
  for (const [value, text, y] of marks)
    svg.append(
      node('line', { x1: at(value), x2: at(value), y1: 2, y2: 18, class: 'viz-mark' }),
      label(text, { x: at(value), y, 'text-anchor': 'start' }, 'viz-text viz-tick'),
    );
  return testid(el('div', { cls: 'viz-meter-box' }, svg), id);
}
