/**
 * Copies text to the clipboard.
 * The Clipboard API only exists in secure contexts (HTTPS or localhost), so
 * the site served over plain HTTP falls back to a hidden textarea and the
 * older copy command.
 *
 * @param text - The text to copy
 * @returns Whether the text was copied
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // A denied permission still leaves the fallback
    }
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  // Off screen and read-only, so the page neither jumps nor opens a keyboard
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  try {
    // Deprecated, but the only way to copy without the Clipboard API
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    textarea.remove();
  }
}
