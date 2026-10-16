import { useEffect, useRef } from "react";
import {
  AreaSeries,
  ColorType,
  createChart,
  type UTCTimestamp,
} from "lightweight-charts";

import type { PriceBar } from "./api";

interface PriceChartProps {
  bars: PriceBar[];
  label: string;
}

export default function PriceChart({ bars, label }: PriceChartProps) {
  const chartHost = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const host = chartHost.current;
    if (!host || bars.length === 0) return;

    const chart = createChart(host, {
      autoSize: true,
      height: 260,
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        textColor: "#69766f",
        fontFamily: "IBM Plex Mono, monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#edf1ed" },
        horzLines: { color: "#edf1ed" },
      },
      rightPriceScale: { borderColor: "#dce3dd" },
      timeScale: {
        borderColor: "#dce3dd",
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: { vertLine: { labelBackgroundColor: "#183d32" } },
    });

    const series = chart.addSeries(AreaSeries, {
      lineColor: "#15785b",
      topColor: "rgba(21, 120, 91, 0.2)",
      bottomColor: "rgba(21, 120, 91, 0.015)",
      lineWidth: 2,
      priceLineVisible: false,
    });

    series.setData(
      bars
        .filter((bar) => Number.isFinite(bar.close) && bar.close > 0)
        .map((bar) => ({
          time: Math.floor(Date.parse(bar.timestamp) / 1000) as UTCTimestamp,
          value: bar.close,
        })),
    );
    chart.timeScale().fitContent();

    return () => chart.remove();
  }, [bars]);

  return (
    <div
      ref={chartHost}
      className="price-chart"
      role="img"
      aria-label={`${label} price history from the market service`}
    />
  );
}