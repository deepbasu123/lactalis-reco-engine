// Maps a product to a real product photo shipped in /public/products.
// Resolution order: specific SUB-TYPE (by product name) first, then a per-CATEGORY
// fallback. Images are bundled by Vite and served by the app itself (no runtime
// hotlinking), so the deployed app has no image egress dependency.
// Attribution for every file lives in /public/products/ATTRIBUTIONS.md.

const BASE = '/products';

// Per-category fallback. Keys match the seven live categories in the gold layer.
const CATEGORY_IMAGE: Record<string, string> = {
  Milk: 'category-milk.jpg',
  'Flavored Milk': 'category-flavored-milk.jpg',
  Cheese: 'category-cheese.jpg',
  Butter: 'category-butter.jpg',
  Cream: 'category-cream.jpg',
  Yogurt: 'category-yogurt.jpg',
  'Hot Beverage': 'category-hot-beverage.jpg',
};

// Cheese sub-types. Words here are unambiguous cheese descriptors, so they are
// safe to match on the product name regardless of the reported category.
const CHEESE_RULES: { test: RegExp; file: string }[] = [
  { test: /\bfeta\b/, file: 'cheese-feta.jpg' },
  { test: /mozzarella|bocconcini/, file: 'cheese-mozzarella.jpg' },
  { test: /parmesan|parmigiano|grana\s*padano|grana/, file: 'cheese-parmesan.jpg' },
  { test: /brie|camembert/, file: 'cheese-soft.jpg' },
];

// Flavored-milk sub-types. Scoped to the Flavored Milk category so that, e.g.,
// "Drinking Chocolate" (Hot Beverage) is not mistaken for chocolate milk.
const FLAVORED_MILK_RULES: { test: RegExp; file: string }[] = [
  { test: /ice\s*break|iced\s*coffee|iced\s*latte|cold\s*brew|espresso|mocha|latte|cappuccino|\bcoffee\b/, file: 'flavored-milk-iced-coffee.jpg' },
  { test: /strawberry/, file: 'flavored-milk-strawberry.jpg' },
  { test: /chocolate|choc\b/, file: 'flavored-milk-chocolate.jpg' },
];

/**
 * Resolve the best product image path for a recommendation or favorite.
 * @param productName e.g. "Président Feta 200g" or "Pauls Ice Break Iced Coffee 500ml"
 * @param category one of the seven live categories
 */
export function productImage(productName?: string, category?: string): string {
  const name = (productName || '').toLowerCase();
  const cat = category || '';

  // 1) Cheese sub-types (name-driven, category-agnostic — cheese words are specific).
  if (cat === 'Cheese' || CHEESE_RULES.some((r) => r.test.test(name))) {
    for (const r of CHEESE_RULES) if (r.test.test(name)) return `${BASE}/${r.file}`;
    // fall through to category fallback for ricotta / pizza cheese / tasty block
  }

  // 2) Flavored-milk sub-types (scoped to the category to avoid false matches).
  if (cat === 'Flavored Milk') {
    for (const r of FLAVORED_MILK_RULES) if (r.test.test(name)) return `${BASE}/${r.file}`;
  }

  // 3) Per-category fallback.
  if (CATEGORY_IMAGE[cat]) return `${BASE}/${CATEGORY_IMAGE[cat]}`;

  // 4) Last resort: generic cheese-neutral milk image keeps the card from breaking.
  return `${BASE}/category-milk.jpg`;
}
