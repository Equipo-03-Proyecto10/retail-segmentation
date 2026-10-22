import type { StoreDetailView, StoreLocationView, StoreQuery } from '../shared/api';
import type { ScreenContext } from './catalog';
import { button, el, errorBox, formField, markDetails, pager, select, show, testid, textInput } from './dom';

// Store locations and geofences (F16-07, #485). Every staff role reads the
// directory; an ADMIN sets and removes a location. Coordinates are plain
// numbers: no map, no tile and no remote script is ever loaded (the CSP of
// F16-01 forbids them). A refusal is shown as stores-service returned it.

const LOCATION_FIELDS = ['latitude', 'longitude', 'geofence_radius_m'];

/** What a store without a location means for the phone's check-ins. */
export const NO_LOCATION = 'no location: check-ins here are recorded as store_location_unknown';

const SIZES: [string, string][] = [
  ['20', '20 a page'],
  ['50', '50 a page'],
  ['100', '100 a page'],
];

function describe(l: StoreLocationView): string {
  return `${l.latitude}, ${l.longitude} (geofence ${l.geofence_radius_m} m)`;
}

export function renderStores(host: HTMLElement, ctx: ScreenContext): void {
  const { api } = ctx;
  const message = testid(el('div'), 'stores-message');
  const tableArea = el('div');
  const detail = testid(el('section', { cls: 'panel' }), 'store-detail');
  let query: StoreQuery = { page: 1, size: 20 };

  const city = textInput('');
  const active = select(
    [
      ['', 'Active or not'],
      ['true', 'Active'],
      ['false', 'Inactive'],
    ],
    '',
  );
  const size = select(SIZES, '20');
  const search = el('button', { text: 'Search' });
  search.type = 'submit';
  const filters = testid(
    el(
      'form',
      { cls: 'filters' },
      formField('city', 'City', city, [], 'filter'),
      formField('active', 'Status', active, [], 'filter'),
      formField('size', 'Page size', size, [], 'filter'),
      search,
    ),
    'store-filters',
  );
  filters.noValidate = true;
  filters.addEventListener('submit', (event) => {
    event.preventDefault();
    query = { page: 1, size: Number(size.value) };
    if (city.value.trim()) query.city = city.value.trim();
    if (active.value) query.active = active.value === 'true';
    void load();
  });

  async function load(): Promise<void> {
    const res = await api.listStores(query);
    if (!res.ok) return void tableArea.replaceChildren(errorBox(res.error));
    const head = el('tr');
    for (const h of ['ID', 'Name', 'City', 'State', 'Status', 'Location', ''])
      head.append(el('th', { text: h }));
    const body = el('tbody');
    for (const s of res.value.items) {
      const place = testid(
        el('td', { text: s.location === null ? NO_LOCATION : describe(s.location) }),
        `store-location-${s.store_id}`,
      );
      const row = el(
        'tr',
        {},
        el('td', { text: String(s.store_id) }),
        el('td', { text: s.name }),
        el('td', { text: s.city }),
        el('td', { text: s.state }),
        el('td', { text: s.is_active ? 'Active' : 'Inactive' }),
        place,
        el(
          'td',
          {},
          button('View', `view-store-${s.store_id}`, () => void open(s.store_id)),
        ),
      );
      row.dataset.storeId = String(s.store_id);
      body.append(row);
    }
    tableArea.replaceChildren(
      testid(el('table', {}, el('thead', {}, head), body), 'stores-table'),
      pager(res.value.page, 'stores', (page) => {
        query = { ...query, page };
        void load();
      }),
    );
  }

  async function open(storeId: number): Promise<void> {
    const res = await api.getStore(storeId);
    if (!res.ok) return void detail.replaceChildren(errorBox(res.error));
    showStore(res.value);
  }

  function showStore(s: StoreDetailView): void {
    const facts = el('dl');
    const fact = (name: string, value: string, id: string): void => {
      facts.append(el('dt', { text: name }), testid(el('dd', { text: value }), id));
    };
    fact('Name', s.name, 'store-name');
    fact('City', `${s.city}, ${s.state}`, 'store-city');
    fact('Status', s.is_active ? 'Active' : 'Inactive', 'store-status');
    if (s.location === null) {
      fact('Location', NO_LOCATION, 'store-no-location');
    } else {
      fact('Latitude', s.location.latitude, 'store-latitude');
      fact('Longitude', s.location.longitude, 'store-longitude');
      fact('Geofence radius', `${s.location.geofence_radius_m} m`, 'store-radius');
      fact('Set at', s.location.set_at, 'store-set-at');
    }
    const parts: Node[] = [el('h3', { text: `${s.name} (#${s.store_id})` }), facts];
    // Other roles read only: no control is drawn for them.
    if (ctx.can('putStoreLocation')) parts.push(locationForm(s));
    if (s.location !== null && ctx.can('deleteStoreLocation')) parts.push(removeControl(s));
    detail.replaceChildren(...parts);
  }

  function locationForm(s: StoreDetailView): HTMLElement {
    const lat = textInput(s.location?.latitude ?? '');
    const lon = textInput(s.location?.longitude ?? '');
    const radius = textInput(s.location === null ? '' : String(s.location.geofence_radius_m));
    lat.inputMode = lon.inputMode = 'decimal';
    radius.inputMode = 'numeric';
    const save = el('button', { text: 'Save location' });
    save.type = 'submit';
    const form = testid(
      el(
        'form',
        {},
        el('h4', { text: s.location === null ? 'Set the location' : 'Change the location' }),
        formField('latitude', 'Latitude (-90 to 90)', lat, []),
        formField('longitude', 'Longitude (-180 to 180)', lon, []),
        formField('geofence_radius_m', 'Geofence radius in metres (25 to 2000)', radius, []),
        save,
      ),
      'location-form',
    );
    form.noValidate = true;
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      void api
        .setStoreLocation(s.store_id, {
          latitude: lat.value,
          longitude: lon.value,
          geofence_radius_m: radius.value,
        })
        .then(async (res) => {
          show(message, res, `Location of ${s.name} saved.`, LOCATION_FIELDS);
          if (res.ok) showStore(res.value);
          else markDetails(form, res.error.details);
          await load();
        });
    });
    return form;
  }

  function removeControl(s: StoreDetailView): HTMLElement {
    const area = el('div');
    const confirm = button('Confirm removal', 'confirm-remove-location', () => {
      void api.removeStoreLocation(s.store_id).then(async (res) => {
        show(message, res, `Location of ${s.name} removed.`);
        if (res.ok) await open(s.store_id);
        await load();
      });
    });
    area.append(
      button('Remove location', 'remove-location', () => {
        area.replaceChildren(
          el('p', { text: `Check-ins at ${s.name} will be recorded as store_location_unknown.` }),
          confirm,
        );
      }),
    );
    return area;
  }

  host.replaceChildren(el('h2', { text: 'Stores' }), filters, message, tableArea, detail);
  void load();
}
