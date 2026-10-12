import type { ChartConfig, DateLevel } from "../../types/chart";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Tooltip,
  Legend,
  Filler,
} from "chart.js";
import { Line } from "react-chartjs-2";
import type { ChartData, Plugin, TooltipItem } from "chart.js";
import { useLayoutEffect, useRef, useState } from "react";
import { TOOLTIP_STYLE } from "../tooltip/tooltipStyle";

const DAY_MS = 24 * 60 * 60 * 1000;
const toUtcDay = (date: string) => Date.parse(`${date}T00:00:00Z`) / DAY_MS;
const fromUtcDay = (day: number) =>
  new Date(day * DAY_MS).toISOString().slice(0, 10);
const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Tooltip,
  Legend,
  Filler,
);

const gradientCache = new WeakMap<
  CanvasRenderingContext2D,
  Map<string, CanvasGradient>
>();

function getGradient(
  ctx: CanvasRenderingContext2D,
  color: string,
): CanvasGradient {
  let colors = gradientCache.get(ctx);
  if (!colors) {
    colors = new Map();
    gradientCache.set(ctx, colors);
  }
  const cached = colors.get(color);
  if (cached) return cached;

  const hexToRgba = (alpha: number) => {
    const r = parseInt(color.slice(1, 3), 16);
    const g = parseInt(color.slice(3, 5), 16);
    const b = parseInt(color.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  };
  const gradient = ctx.createLinearGradient(0, 0, 0, 400);
  gradient.addColorStop(0, hexToRgba(0.25));
  gradient.addColorStop(1, hexToRgba(0.03));
  colors.set(color, gradient);
  return gradient;
}

function toPeriodIndex(date: string, level: DateLevel): number {
  // Consecutive periods get consecutive numbers, so a gap is always greater than one.
  if (level === "year") return Number(date.slice(0, 4));
  if (level === "month")
    return Number(date.slice(0, 4)) * 12 + Number(date.slice(5, 7)) - 1;
  return toUtcDay(date);
}

function fromPeriodIndex(index: number, level: DateLevel): string {
  if (level === "year") return `${index}-01-01`;
  if (level === "month") {
    const year = Math.floor(index / 12);
    const month = (index % 12) + 1;
    return `${year}-${String(month).padStart(2, "0")}-01`;
  }
  return fromUtcDay(index);
}

function formatPeriod(date: string, level: DateLevel): string {
  if (level === "year") return date.slice(0, 4);
  const month = MONTHS[Number(date.slice(5, 7)) - 1];
  if (level === "month") return `${month} ${date.slice(0, 4)}`;
  return date;
}

function tickLabel(date: string, level: DateLevel): string {
  if (level === "day") return String(Number(date.slice(8, 10)));
  if (level === "month") return MONTHS[Number(date.slice(5, 7)) - 1];
  return date.slice(0, 4);
}

type DateSpacing = "calendar" | "recorded";

type AxisTick = { date: string; pixel: number };

function drawGroupRow(
  ctx: CanvasRenderingContext2D,
  ticks: AxisTick[],
  group: "month" | "year",
  y: number,
  left: number,
  right: number,
  color: string,
) {
  const groups: { label: string; left: number; right: number }[] = [];
  for (let i = 0; i < ticks.length; i++) {
    const date = ticks[i].date;
    const key = date.slice(0, group === "month" ? 7 : 4);
    const previousKey = ticks[i - 1]?.date.slice(0, group === "month" ? 7 : 4);
    if (key !== previousKey) {
      // Put the divider halfway between labels from different groups.
      const boundary =
        i === 0 ? left : (ticks[i - 1].pixel + ticks[i].pixel) / 2;
      if (groups.length > 0) groups[groups.length - 1].right = boundary;
      groups.push({
        label:
          group === "month"
            ? MONTHS[Number(date.slice(5, 7)) - 1]
            : date.slice(0, 4),
        left: boundary,
        right,
      });
    }
  }

  ctx.save();
  ctx.font = "12px sans-serif";
  ctx.fillStyle = color;
  ctx.strokeStyle = "rgba(255, 255, 255, 0.35)";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (const segment of groups) {
    // Skip labels that would spill into the next group.
    if (
      segment.right - segment.left >=
      ctx.measureText(segment.label).width + 8
    ) {
      ctx.fillText(segment.label, (segment.left + segment.right) / 2, y);
    }
    if (segment.right < right) {
      // Dotted dividers show which day/month labels belong to each group.
      ctx.setLineDash([2, 3]);
      ctx.beginPath();
      ctx.moveTo(segment.right, y - 9);
      ctx.lineTo(segment.right, y + 9);
      ctx.stroke();
    }
  }
  ctx.restore();
}

export function LineChart({
  config,
  className,
  dateSpacing = "recorded",
  dateLevel = "day",
  startDate,
  endDate,
}: Readonly<{
  config: ChartConfig;
  className?: string;
  dateSpacing?: DateSpacing;
  dateLevel?: DateLevel;
  startDate?: string;
  endDate?: string;
}>) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [chartWidth, setChartWidth] = useState(0);

  useLayoutEffect(() => {
    // Tick density follows this plot's width, including when its container resizes.
    const element = containerRef.current;
    if (!element) return;
    const updateWidth = () => setChartWidth(element.clientWidth);
    updateWidth();
    const observer = new ResizeObserver(updateWidth);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const rangeStart = startDate ?? config.labels[0];
  const rangeEnd = endDate ?? config.labels[config.labels.length - 1];
  const showCalendarGaps =
    dateSpacing === "calendar" &&
    config.labels.length > 1 &&
    Boolean(
      rangeStart &&
      rangeEnd &&
      toPeriodIndex(rangeStart, dateLevel) < toPeriodIndex(rangeEnd, dateLevel),
    );
  const calendarDays = showCalendarGaps
    ? config.labels
        .map((date) => toPeriodIndex(date, dateLevel))
        .sort((a, b) => a - b)
    : [];
  // Show the full applied range, including quiet days at either end.
  const firstDay = startDate
    ? toPeriodIndex(startDate, dateLevel)
    : calendarDays[0];
  const lastDay = endDate
    ? toPeriodIndex(endDate, dateLevel)
    : calendarDays[calendarDays.length - 1];
  // Use the observed range for two points, then shrink the side buffer as more appear.
  const axisStart = config.labels.length <= 2 ? calendarDays[0] : firstDay;
  const axisEnd =
    config.labels.length <= 2 ? calendarDays[calendarDays.length - 1] : lastDay;
  const sidePadding = showCalendarGaps
    ? Math.max(1, axisEnd - axisStart) * (0.7 / config.labels.length)
    : 0;

  const data: ChartData<"line", number[] | { x: number; y: number }[], string> =
    {
      labels: showCalendarGaps ? undefined : config.labels,
      datasets: config.datasets.map((dataset) => ({
        label: dataset.label,
        // Calendar points already have sorted numeric x values.
        parsing: showCalendarGaps ? (false as const) : undefined,
        data: showCalendarGaps
          ? config.labels
              .map((date, index) => ({
                // Whole UTC periods keep date ticks and gaps independent of timezone.
                x: toPeriodIndex(date, dateLevel),
                y: dataset.data[index],
              }))
              .sort((a, b) => a.x - b.x)
          : dataset.data,
        backgroundColor: (context: {
          chart: { ctx: CanvasRenderingContext2D };
        }) => {
          const chart = context.chart;
          const { ctx } = chart;
          return getGradient(ctx, dataset.color);
        },
        borderColor: dataset.color,
        borderWidth: 3,
        fill: !showCalendarGaps,
        // Dotted connections show where one or more calendar periods have no data.
        segment: showCalendarGaps
          ? {
              borderDash: (context: {
                p0: { parsed: { x: number } };
                p1: { parsed: { x: number } };
              }) =>
                context.p1.parsed.x - context.p0.parsed.x > 1
                  ? [5, 5]
                  : undefined,
            }
          : undefined,
        tension: config.labels.length > 100 ? 0 : 0.2,
        // Dense plots keep the line and hover targets without drawing every marker.
        pointRadius:
          config.labels.length > 250 ? 0 : config.labels.length > 60 ? 3 : 6,
        pointHoverRadius: 7,
        pointHitRadius: 10,
        pointBackgroundColor: dataset.color,
        pointBorderColor: "#ffffff",
        pointBorderWidth: 2,
        pointHoverBackgroundColor: dataset.color,
        pointHoverBorderColor: "#ffffff",
        pointHoverBorderWidth: 3,
      })),
    };

  const hierarchyAxis: Plugin<"line"> = {
    id: "hierarchyAxis",
    afterDraw(chart) {
      if (dateLevel === "year") return;
      const x = chart.scales.x;
      const ticks: AxisTick[] = x.ticks
        .map((tick) => {
          const date = showCalendarGaps
            ? fromPeriodIndex(tick.value, dateLevel)
            : config.labels[tick.value];
          return { date, pixel: x.getPixelForValue(tick.value) };
        })
        .filter((tick): tick is AxisTick => Boolean(tick.date));

      const { left, right } = chart.chartArea;
      if (dateLevel === "day") {
        drawGroupRow(
          chart.ctx,
          ticks,
          "month",
          x.bottom + 13,
          left,
          right,
          config.labelColor,
        );
      }
      drawGroupRow(
        chart.ctx,
        ticks,
        "year",
        x.bottom + (dateLevel === "day" ? 33 : 13),
        left,
        right,
        config.labelColor,
      );
    },
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    // Keep Chart.js line and tooltip animations when the plot changes or is hovered.
    normalized: true,
    layout: {
      padding: {
        bottom: dateLevel === "day" ? 46 : dateLevel === "month" ? 24 : 0,
      },
    },
    interaction: {
      intersect: false,
      mode: "index" as const,
    },
    plugins: {
      legend: {
        display: config.showLegend ?? false,
        position: "top" as const,
        align: "center" as const,
        labels: {
          color: config.labelColor,
          font: {
            size: 14,
            weight: "normal" as const,
          },
          padding: 10,
          usePointStyle: true,
          pointStyle: "circle",
        },
      },
      // The site's tooltip look (TOOLTIP_STYLE), the border in the line color
      tooltip: {
        backgroundColor: TOOLTIP_STYLE.background,
        titleColor: TOOLTIP_STYLE.color,
        bodyColor: TOOLTIP_STYLE.color,
        titleFont: {
          family: TOOLTIP_STYLE.fontFamily,
          size: TOOLTIP_STYLE.fontSizePx,
          weight: "bold" as const,
        },
        bodyFont: {
          family: TOOLTIP_STYLE.fontFamily,
          size: TOOLTIP_STYLE.fontSizePx,
        },
        padding: { x: TOOLTIP_STYLE.paddingX, y: TOOLTIP_STYLE.paddingY },
        borderColor: config.datasets[0]?.color || TOOLTIP_STYLE.color,
        borderWidth: 1,
        cornerRadius: TOOLTIP_STYLE.radiusPx,
        displayColors: true,
        caretPadding: 10,
        callbacks: {
          title: (items: TooltipItem<"line">[]) => {
            if (items.length === 0) return "";
            const date = showCalendarGaps
              ? fromPeriodIndex(items[0].parsed.x, dateLevel)
              : config.labels[items[0].dataIndex];
            return formatPeriod(date, dateLevel);
          },
        },
      },
    },
    scales: {
      y: {
        beginAtZero: true,
        title: {
          display: true,
          text: config.yAxisTitle,
          color: config.labelColor,
          font: {
            size: 14,
            weight: "normal" as const,
          },
        },
        ticks: {
          color: config.labelColor,
          font: {
            size: 12,
          },
          padding: 10,
        },
        grid: {
          color: "rgba(255, 255, 255, 0.1)",
          drawBorder: false,
        },
      },
      x: {
        type: showCalendarGaps ? ("linear" as const) : ("category" as const),
        // Category offset puts one point in the center and eases others from the edges.
        offset: !showCalendarGaps,
        min: showCalendarGaps ? axisStart - sidePadding : undefined,
        max: showCalendarGaps ? axisEnd + sidePadding : undefined,
        title: {
          display: false,
          text: config.xAxisTitle,
          color: config.labelColor,
          font: {
            size: 14,
            weight: "normal" as const,
          },
        },
        ticks: {
          color: config.labelColor,
          font: {
            size: 12,
          },
          // Day numbers are short, so show as many as fit before thinning them out.
          maxTicksLimit:
            dateLevel === "day" && chartWidth > 0
              ? Math.max(2, Math.floor((chartWidth - 80) / 24))
              : 10,
          autoSkip: true,
          autoSkipPadding: 6,
          precision: showCalendarGaps ? 0 : undefined,
          includeBounds: !showCalendarGaps,
          callback: showCalendarGaps
            ? (value: number | string) =>
                tickLabel(fromPeriodIndex(Number(value), dateLevel), dateLevel)
            : (value: number | string) =>
                tickLabel(config.labels[Number(value)], dateLevel),
          maxRotation: dateLevel === "day" ? 0 : 45,
          padding: 10,
        },
        grid: {
          color: "rgba(255, 255, 255, 0.1)",
          drawBorder: false,
        },
      },
    },
  };

  // Generate stable unique key to prevent canvas reuse issues
  const firstDataPoint = config.datasets[0]?.data[0] || 0;
  const chartKey = `${dateSpacing}-${dateLevel}-${config.title.replace(
    " ",
    "",
  )}-${config.yAxisTitle.replace(" ", "")}-${firstDataPoint}`;

  return (
    <div ref={containerRef} className={className}>
      <Line
        key={chartKey}
        data={data}
        options={options}
        plugins={[hierarchyAxis]}
      />
    </div>
  );
}
