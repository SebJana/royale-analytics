/**
 * The look of every tooltip on the site, shared by the MUI theme (main.tsx)
 * and the Chart.js tooltips (lineChart.tsx), which cannot read CSS.
 *
 * NOTE: fontFamily matches the font-family of :root and fontSizePx matches
 * --font-base (0.9rem at the 16px root size) in index.css. Update both
 * together.
 */
export const TOOLTIP_STYLE = {
  background: "rgba(24, 24, 24, 0.94)",
  color: "#ffffff",
  fontFamily:
    '"Rubik Variable", system-ui, Avenir, Helvetica, Arial, sans-serif',
  fontSizePx: 14.4,
  lineHeight: 1.45,
  paddingY: 8,
  paddingX: 12,
  radiusPx: 8,
  maxWidthPx: 300,
} as const;
