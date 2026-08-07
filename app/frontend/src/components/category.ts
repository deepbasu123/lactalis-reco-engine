// Maps product categories to a color cap + tint used on packaging-style cards.
// Colors stay within the dairy/food palette; kept muted so brand blue dominates.

export interface CategoryTheme {
  cap: string; // strong cap color
  tint: string; // soft background wash
  label: string;
}

const THEMES: Record<string, CategoryTheme> = {
  Milk: { cap: '#004b85', tint: '#eaf4fb', label: 'Milk' },
  Cheese: { cap: '#b8912b', tint: '#faf3e0', label: 'Cheese' },
  Butter: { cap: '#d9a441', tint: '#fbf1de', label: 'Butter' },
  Cream: { cap: '#5b7fa6', tint: '#eef2f7', label: 'Cream' },
  Yogurt: { cap: '#7a6cae', tint: '#f0edf7', label: 'Yogurt' },
  'Flavored Milk': { cap: '#c0532b', tint: '#fbeee7', label: 'Flavored Milk' },
  'Hot Beverage': { cap: '#8a4b2f', tint: '#f5ebe4', label: 'Hot Beverage' },
};

const FALLBACK: CategoryTheme = { cap: '#3f4097', tint: '#eef0f8', label: 'Product' };

export function categoryTheme(category?: string): CategoryTheme {
  if (!category) return FALLBACK;
  return THEMES[category] || { ...FALLBACK, label: category };
}

// Short initials from a brand name for the packaging monogram.
export function brandMonogram(brand?: string): string {
  if (!brand) return 'L';
  const cleaned = brand.replace(/[^A-Za-zÀ-ſ ]/g, ' ').trim();
  const parts = cleaned.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return cleaned.slice(0, 2).toUpperCase();
}
