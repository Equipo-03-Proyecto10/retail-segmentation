import type { ModelComparisonView, ModelVersionView } from '../shared/api';
import type { ScreenContext } from './catalog';
import { button, el, errorBox, select, testid } from './dom';
import { latestRequest } from './latest-request';

// The model comparison is deliberately descriptive. F23-06's three criteria are shown with the
// values the immutable version actually records; agreement from segments is separate context and
// never supplies a verdict (RN-39, ADR-0064).

const NOT_RECORDED = 'Not recorded by model-registry';

function table(headers: string[], rows: string[][], id: string): HTMLTableElement {
  const head = el('tr');
  for (const text of headers) {
    const th = el('th', { text });
    th.scope = 'col';
    head.append(th);
  }
  const body = el('tbody');
  for (const row of rows) body.append(el('tr', {}, ...row.map((text) => el('td', { text }))));
  return testid(el('table', {}, el('thead', {}, head), body), id);
}

const title = (version: ModelVersionView): string =>
  `${version.name} (#${version.version_id}, ${version.method})`;

function populations(result: ModelComparisonView): HTMLElement {
  const labels = [
    ...new Set([...Object.keys(result.first_population), ...Object.keys(result.second_population)]),
  ].sort();
  return el(
    'div',
    { cls: 'scroll' },
    table(
      ['Label', result.first.name, result.second.name],
      labels.map((label) => [
        label,
        String(result.first_population[label] ?? 0),
        String(result.second_population[label] ?? 0),
      ]),
      'comparison-populations',
    ),
  );
}

function comparisonView(result: ModelComparisonView): HTMLElement {
  const criteria = table(
    ['Criterion', 'Question and measure', result.first.name, result.second.name, 'Tie margin'],
    result.criteria.map((criterion) => [
      criterion.name,
      `${criterion.question} ${criterion.measure}`,
      criterion.first_score ?? NOT_RECORDED,
      criterion.second_score ?? NOT_RECORDED,
      criterion.margin,
    ]),
    'comparison-criteria',
  );
  const agreement = testid(
    el(
      'section',
      {},
      el('h3', { text: 'Agreement context — not a verdict' }),
      testid(
        el('p', {
          cls: 'notice',
          text: 'Agreement only says where the two versions use the same label. It is not a quality criterion and gives no basis to say either version is better.',
        }),
        'agreement-warning',
      ),
      el('p', {
        text: `${result.compared} customers were scored by both: ${result.agreed} agreed and ${result.disagreed} disagreed. ${result.first_only} were scored only by ${result.first.name}; ${result.second_only} only by ${result.second.name}.`,
      }),
      el('h4', { text: 'Population by label' }),
      populations(result),
      el('h4', { text: 'Cross-tabulation of customers scored by both' }),
      el(
        'div',
        { cls: 'scroll' },
        table(
          [result.first.name, result.second.name, 'Customers'],
          result.cells.map((cell) => [cell.first_label, cell.second_label, String(cell.customers)]),
          'comparison-cross-tab',
        ),
      ),
    ),
    'agreement-context',
  );
  return testid(
    el(
      'section',
      {},
      el('h2', { text: `${title(result.first)} and ${title(result.second)}` }),
      testid(
        el('p', {
          cls: 'notice',
          text: 'This is a methodological comparison of immutable versions, not a change in customer behaviour. Differences do not rank the versions.',
        }),
        'methodological-note',
      ),
      el('h3', { text: 'F23-06 criteria' }),
      el('p', {
        text: 'Interpretability and usefulness scores are not fields in the model-registry contract. They are shown as not recorded instead of being inferred from agreement or other metrics.',
      }),
      el('div', { cls: 'scroll' }, criteria),
      agreement,
    ),
    'model-comparison-result',
  );
}

export function renderModelComparison(host: HTMLElement, ctx: ScreenContext): void {
  const message = el('div');
  const result = el('div');
  const first = testid(select([], ''), 'comparison-first');
  const second = testid(select([], ''), 'comparison-second');
  const compare = button('Compare versions', 'comparison-submit', () => void run());
  compare.disabled = true;
  const form = el(
    'form',
    {},
    el('label', { text: 'First version' }),
    first,
    el('label', { text: 'Second version' }),
    second,
    compare,
  );
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    void run();
  });
  host.replaceChildren(
    el('h1', { text: 'Model comparison' }),
    el('p', {
      text: 'Choose any two immutable model versions. The module reads model-registry and segments; it writes nothing.',
    }),
    testid(form, 'comparison-form'),
    message,
    result,
  );
  const latest = latestRequest();

  async function run(): Promise<void> {
    message.replaceChildren();
    result.replaceChildren();
    const a = Number(first.value);
    const b = Number(second.value);
    if (a === b) {
      message.replaceChildren(
        testid(
          el('p', { cls: 'error', text: 'Choose two different model versions.' }),
          'comparison-choice-error',
        ),
      );
      return;
    }
    await latest(
      () => ctx.api.compareModelVersions(a, b),
      (response) =>
        result.replaceChildren(response.ok ? comparisonView(response.value) : errorBox(response.error)),
    );
  }

  void ctx.api.listModelVersions({ page: 1, size: 100 }).then((response) => {
    if (!response.ok) return void message.replaceChildren(errorBox(response.error));
    const options: [string, string][] = response.value.items.map((version) => [
      String(version.version_id),
      title(version),
    ]);
    const fill = (control: HTMLSelectElement): void => {
      control.replaceChildren(
        ...options.map(([value, text]) => {
          const option = el('option', { text });
          option.value = value;
          return option;
        }),
      );
    };
    fill(first);
    fill(second);
    first.value = options[0]?.[0] ?? '';
    second.value = options[1]?.[0] ?? options[0]?.[0] ?? '';
    compare.disabled = options.length < 2;
    if (options.length < 2)
      message.replaceChildren(
        testid(
          el('p', { cls: 'notice', text: 'At least two model versions are needed.' }),
          'comparison-empty',
        ),
      );
  });
}
