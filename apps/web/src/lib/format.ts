export const money = (minor: number) => new Intl.NumberFormat('en-HK', {
  style: 'currency', currency: 'HKD', maximumFractionDigits: 2,
}).format(minor / 100);
export const shortDate = (value: string) => new Intl.DateTimeFormat('en-HK', {
  day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Asia/Hong_Kong',
}).format(new Date(value));
