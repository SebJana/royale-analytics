/**
 * Rounds a number to the specified number of decimal places.
 *
 * @param num - The number to round
 * @param decimalPlaces - The number of decimal places to round to
 * @returns The rounded number
 */
export function round(num: number, decimalPlaces: number): number {
  const factor = Math.pow(10, decimalPlaces);
  return Math.round(num * factor) / factor;
}

/**
 * Formats a number into a human-readable string with appropriate suffixes.
 *
 * @param num - The number to format
 * @returns A formatted string with 'M' for millions (≥1,000,000), 'k' for thousands (≥10,000), or the original number as a string
 */
export function formatNumberWithSuffix(num: number): string {
  // Format millions with 'M' suffix
  if (num >= 1000000) {
    const millions = round(num / 1000000, 1);
    return `${millions}M`;
  }

  // Format thousands with 'k' suffix
  if (num >= 10000) {
    const thousands = round(num / 1000, 2);
    return `${thousands}k`;
  }

  // Return number as string for values below 10k
  return String(num);
}

// The site is English, so numbers use English separators everywhere
// ("54,552", "54.6%"), whatever the browser's locale. The browser's thousands
// separator next to the dot decimals of the percentages would make e.g. a
// German "54.552" wins read like a decimal number.
const NUMBER_LOCALE = "en-US";

/**
 * Formats a number with English thousands separators, e.g. 54552 as "54,552".
 *
 * @param num - The number to format
 * @returns The formatted number
 */
export function formatNumber(num: number): string {
  return num.toLocaleString(NUMBER_LOCALE);
}
