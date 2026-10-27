import random
from pathlib import Path
from collections import Counter


def load_valid_guesses():
    """Load the list of valid guesses from the text file.

    Returns:
        list[str]: List of all valid guesses.
    """
    # Path works ONLY in docker
    file_path = Path("/app/shared_resources/word_guess/valid-guesses.txt")

    with open(file_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def load_solution_words():
    """Load the list of possible Word Guess solutions from the text file.

    Returns:
        list[str]: List of all possible Word Guess answer words.
    """
    # Path works ONLY in Docker
    file_path = Path("/app/shared_resources/word_guess/possible-solutions.txt")

    with open(file_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


# Load all possible guesses and solutions once
SOLUTION_WORDS = load_solution_words()
ALLOWED_GUESSES = set(load_valid_guesses())  # Set for faster look-ups


def pick_random_solution():
    """Generate a random Word Guess solution.

    Returns:
        str: A random Word Guess solution.
    """
    solution = random.choice(SOLUTION_WORDS)

    return solution


def is_valid_guess(guess: str):
    """Test if a given string is a valid guess.

    Args:
        guess (str): Word that is being validated

    Returns:
        str: True if valid, False otherwise.
    """
    if guess in ALLOWED_GUESSES:
        return True

    return False


def is_guess_solution(solution: str, guess: str):
    """Check if the given guess is the solution.
    Args:
        solution (str): The target word to guess (5 letters, lowercase)
        guess (str): The player's guess (5 letters, lowercase)

    Returns: True if solution, false otherwise
    """

    if solution.lower() == guess.lower():
        return True
    return False


def evaluate_guess(solution: str, guess: str):
    """Evaluate a guess against the solution word.

    Compares each letter in the guess against the solution word and returns
    the result using green/yellow/grey style feedback:
    - "correct" (green): Letter is in the correct position
    - "in word" (yellow): Letter is in the word but wrong position
    - "wrong" (gray): Letter is not in the word at all

    The function handles duplicate letters correctly by tracking letter counts
    to ensure proper yellow/gray assignment when the same letter appears
    multiple times in either the guess or solution.

    Args:
        solution (str): The target word to guess (5 letters, lowercase)
        guess (str): The player's guess (5 letters, lowercase)

    Returns:
        dict[int, str]: Dictionary mapping position indices (0-4) to result strings:
            - 0-4 (int): Position in the word (0 = first letter, 4 = last letter)
            - "correct" | "in word" | "wrong" (str): Evaluation result

    Example:
        >>> evaluate_guess("crane", "cares")
        {0: "correct", 1: "in word", 2: "in word", 3: "wrong", 4: "wrong"}

        >>> evaluate_guess("hello", "llama")
        {0: "wrong", 1: "in word", 2: "wrong", 3: "wrong", 4: "wrong"}
    """
    result = {}

    # Count letters in solution
    letter_counts = Counter(solution)

    # Mark all correct letters (greens)
    for i in range(len(guess)):
        if guess[i] == solution[i]:
            result[i] = "correct"
            letter_counts[guess[i]] -= 1

    # Mark all the others (yellow/gray)
    for i in range(len(guess)):
        if i in result:  # skip positions if already marked green
            continue

        letter = guess[i]

        if letter in letter_counts and letter_counts[letter] > 0:
            result[i] = "in word"
            letter_counts[letter] -= 1
        else:
            result[i] = "wrong"

    return result
