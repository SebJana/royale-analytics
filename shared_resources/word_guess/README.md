# Word Guess Word Lists

This directory contains the word lists the Word Guess challenge uses to pick answers and validate guesses.

## Files

### `possible-solutions.txt`

Contains 2,344 common five-letter English words that can appear as the answer.

**Purpose**:

- Used for generating random Word Guess answers
- Keeps answers to common, recognizable English words

**Format**: One word per line, all lowercase, 5 letters each

### `valid-guesses.txt`

Contains the complete list of accepted guesses (14,853 words). This includes all possible solutions plus additional valid English words.

**Purpose**:

- Used for validating user input
- Ensures only real English words are accepted
- Includes obscure but valid words that won't be answers

**Format**: One word per line, all lowercase, 5 letters each

**Note**: This file includes all words from `possible-solutions.txt` plus 12,509 additional words that can be guessed but will never be the answer.

## Data Sources

### Valid Guesses

- **Source**: [GitHub Gist by kcwhite](https://gist.github.com/kcwhite/bb598f1b3017b5477cb818c9b086a5d9)
- **Contains**: 14,853 accepted five-letter words

### Possible Solution Words

- **Primary Source**: [Word Raiders Word Finder](https://wordraiders.com/wordfinder-results/)
- **Secondary Source**: [GitHub Gist by cfreshman](https://gist.github.com/cfreshman/a03ef2cba789d8cf00c08f767e0fad7b)
- **Contains**: 2,344 common five-letter words

## Usage

The application uses the lists to:

- **Generate random Word Guess answers** using `possible-solutions.txt`
- **Validate user guesses** using `valid-guesses.txt`
- **Separate the answer pool from the guess pool**, so rare words are accepted as guesses but never picked as answers
