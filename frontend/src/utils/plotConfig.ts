import { Crown, Droplet, Swords, Trophy } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { ChartConfig, DateLevel } from "../types/chart";
import type { DailyStats, Days } from "../types/dailyStats";
import { formatNumber, round } from "./number";

type NumericDayField = Exclude<keyof Days, "date">;

// Sum counts, average values (optionally weighted), or divide summed totals for rates.
export type PlotMetric =
  | { aggregate: "sum"; field: NumericDayField }
  | { aggregate: "average"; field: NumericDayField; weight?: NumericDayField }
  | {
      aggregate: "ratio";
      numerator: NumericDayField;
      denominator: NumericDayField;
      multiplier?: number;
      dayField?: NumericDayField;
      zeroDenominator?: "zero" | "numerator";
    };

export type PlotSeries = {
  label: string;
  color: string;
  metric: PlotMetric;
};

export type PlotDefinition = {
  id: string;
  title: string;
  icon: LucideIcon;
  // Formats a series' value over the whole period for the title
  format: (value: number) => string;
  yAxisTitle: string;
  series: PlotSeries[];
  showLegend?: boolean;
};

// Add a dated plot here by choosing its fields and how each series aggregates.
export const PLOT_DEFINITIONS: PlotDefinition[] = [
  {
    id: "winRate",
    title: "Win Rate Performance",
    icon: Trophy,
    format: (value) => `${round(value, 1)}%`,
    yAxisTitle: "Win Rate (%)",
    series: [
      {
        label: "Win Rate",
        color: "#00C9FF",
        metric: {
          aggregate: "ratio",
          numerator: "victories",
          denominator: "battles",
          multiplier: 100,
          dayField: "winRate",
        },
      },
    ],
  },
  {
    id: "battles",
    title: "Battle Activity",
    icon: Swords,
    format: (value) => formatNumber(value),
    yAxisTitle: "Number of Battles",
    series: [
      {
        label: "Battles",
        color: "#FF6B6B",
        metric: { aggregate: "sum", field: "battles" },
      },
    ],
  },
  {
    id: "crowns",
    title: "Crowns For vs. Crowns Against",
    icon: Crown,
    format: (value) => formatNumber(value),
    yAxisTitle: "Crowns",
    showLegend: true,
    series: [
      {
        label: "Crowns For",
        color: "#00C9FF",
        metric: { aggregate: "sum", field: "crownsFor" },
      },
      {
        label: "Crowns Against",
        color: "#FF6B6B",
        metric: { aggregate: "sum", field: "crownsAgainst" },
      },
    ],
  },
  {
    id: "leakedElixir",
    title: "Average Leaked Elixir per Battle",
    icon: Droplet,
    format: (value) => `${round(value, 2)} per battle`,
    yAxisTitle: "Leaked Elixir",
    series: [
      {
        label: "Leaked Elixir",
        color: "#C547DB",
        metric: {
          aggregate: "ratio",
          numerator: "elixirLeaked",
          denominator: "battles",
          zeroDenominator: "numerator",
        },
      },
    ],
  },
];

function periodDate(date: string, level: DateLevel): string {
  // Use the first day as a stable key for each month or year.
  if (level === "month") return `${date.slice(0, 7)}-01`;
  if (level === "year") return `${date.slice(0, 4)}-01-01`;
  return date;
}

function sum(rows: Days[], field: NumericDayField): number {
  return rows.reduce((total, row) => total + row[field], 0);
}

function metricValue(
  rows: Days[],
  metric: PlotMetric,
  level: DateLevel,
): number {
  if (metric.aggregate === "sum") return sum(rows, metric.field);

  if (metric.aggregate === "average") {
    const weight = metric.weight;
    if (weight) {
      // Weight each day before averaging, e.g. by its battle count.
      const totalWeight = sum(rows, weight);
      return totalWeight > 0
        ? rows.reduce(
            (total, row) => total + row[metric.field] * row[weight],
            0,
          ) / totalWeight
        : 0;
    }
    return sum(rows, metric.field) / rows.length;
  }

  // Keep the API's daily value; drilled-up rates divide summed totals.
  if (level === "day" && rows.length === 1 && metric.dayField) {
    return rows[0][metric.dayField];
  }
  const numerator = sum(rows, metric.numerator);
  const denominator = sum(rows, metric.denominator);
  if (denominator === 0) {
    return metric.zeroDenominator === "numerator" ? numerator : 0;
  }
  return (numerator / denominator) * (metric.multiplier ?? 1);
}

/**
 * Each series' value over the whole period, e.g. the win rate of all shown
 * battles, for the plot's title.
 *
 * @param definition - The plot
 * @param stats - The daily statistics of the period
 * @returns The formatted value and color of each series
 */
export function summarizePlot(
  definition: PlotDefinition,
  stats: DailyStats,
): { label: string; color: string; value: string }[] {
  const rows = stats.daily_statistics.daily;
  return definition.series.map((series) => ({
    label: series.label,
    color: series.color,
    // Any level but "day" divides summed totals, which a period needs
    value: definition.format(metricValue(rows, series.metric, "year")),
  }));
}

export function buildPlotConfig(
  definition: PlotDefinition,
  stats: DailyStats,
  level: DateLevel,
): ChartConfig {
  // Each label represents one day, month, or year with all its source days.
  const groups = new Map<string, Days[]>();
  for (const row of stats.daily_statistics.daily) {
    const date = periodDate(row.date, level);
    const group = groups.get(date) ?? [];
    group.push(row);
    groups.set(date, group);
  }
  const periods = [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));

  return {
    labels: periods.map(([date]) => date),
    datasets: definition.series.map((series) => ({
      label: series.label,
      color: series.color,
      data: periods.map(([, rows]) => metricValue(rows, series.metric, level)),
    })),
    title: definition.title,
    yAxisTitle: definition.yAxisTitle,
    xAxisTitle: "Date",
    labelColor: "#e0e0e0",
    showLegend: definition.showLegend,
  };
}
