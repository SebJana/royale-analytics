let VALID_GUESSES: string[] = [];

export async function loadValidGuesses(): Promise<string[]> {
  // Return cached guesses if already loaded
  if (VALID_GUESSES.length > 0) {
    return VALID_GUESSES;
  }

  try {
    // Try to fetch from public directory (works in Docker after Dockerfile copies it)
    const response = await fetch("/valid-guesses.txt");

    if (response.ok) {
      const text = await response.text();
      VALID_GUESSES = text
        .trim()
        .split("\n")
        .map((word) => word.trim())
        .filter((word) => word.length > 0);
      return VALID_GUESSES;
    }
  } catch (error) {
    console.error("Error loading valid guesses:", error);
  }

  // Fallback: return empty array if file can't be loaded
  return [];
}

/**
 * Checks if a given word is a valid guess for Word Guess.
 *
 * This function validates whether the provided guess exists in the official
 * list of valid guesses. It automatically loads the valid guesses
 * list on first use and caches it for subsequent calls.
 *
 * @param guess - The word to validate (case-insensitive)
 * @returns Promise that resolves to true if the guess is valid, false otherwise

 */
export async function isValidGuess(guess: string): Promise<boolean> {
  if (VALID_GUESSES.length === 0) {
    await loadValidGuesses();
  }

  const normalized = guess.toLowerCase();

  // If valid guess array is still empty, return true and lean on backend to handle the validation
  if (VALID_GUESSES.length === 0) {
    return true;
  }

  if (VALID_GUESSES.includes(normalized)) {
    return true;
  }
  return false;
}
