export const money = (minor: number) => new Intl.NumberFormat('en-HK', {
  style: 'currency', currency: 'HKD', maximumFractionDigits: 2,
}).format(minor / 100);
export const shortDate = (value: string) => new Intl.DateTimeFormat('en-HK', {
  day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Asia/Hong_Kong',
}).format(new Date(value));

const CATEGORY_LABELS: Record<string, string> = {
  produce: 'Fruit & veg', beverage_non_alcoholic: 'Soft drinks', household: 'Household items',
};
export const categoryLabel = (category: string) =>
  CATEGORY_LABELS[category] ?? category.replace(/_/g, ' ').replace(/^\w/, (letter) => letter.toUpperCase());
export const periodWord = (period: string | undefined) => (period === 'calendar_month' ? 'month' : 'week');
