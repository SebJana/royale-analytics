/**
 * Returns the appropriate singular or plural form of a word based on the given amount.
 *
 * @param amount - The number to determine singular/plural form for
 * @param singularOption - The singular form of the word to use when amount is 1
 * @param pluralOption - The plural form of the word to use when amount is not 1
 * @returns The appropriate singular or plural form based on the amount
 */
export function pluralize(
  amount: number,
  singularOption: string,
  pluralOption: string,
): string {
  if (amount === 1) {
    return singularOption;
  }
  if (amount === 0) {
    return pluralOption;
  }
  return pluralOption;
}
