// Helpers for the import tests (F28-02, #871): the recorded examples of contracts/sales/, changed
// only in the values a test needs to move.

/** A recorded XML example with some of its leaf values replaced. */
export function importXml(base: string, over: Record<string, string | number>): string {
  return Object.entries(over).reduce((xml, [name, value]) => {
    const leaf = new RegExp(`<${name}>[^<]*</${name}>`);
    if (!leaf.test(xml)) throw new Error(`the example has no <${name}>`);
    return xml.replace(leaf, `<${name}>${value}</${name}>`);
  }, base);
}

/** The lines of the rows of a SaleImportChunk body, in order. */
export function chunkLines(body: string | undefined): number[] {
  return [...(body ?? '').matchAll(/<rows><line>(\d+)<\/line>/g)].map((m) => Number(m[1]));
}

const HEADER = 'transaction_id,customer_id,store_id,channel_id,occurred_at,product_id,quantity,unit_price';

/** A CSV of the contract's shape with n data rows (line 2 on). */
export function salesCsv(rows: number, change?: (line: number, cells: string[]) => string[]): string {
  const lines = Array.from({ length: rows }, (_, i) => {
    const line = i + 2;
    const cells = [
      `T-${1000 + line}`,
      '11111111-1111-1111-1111-000000000001',
      '3',
      '2',
      '2026-10-02T14:03:00-06:00',
      '501',
      '2',
      '19.90',
    ];
    return (change ? change(line, cells) : cells).join(',');
  });
  return `${[HEADER, ...lines].join('\n')}\n`;
}
