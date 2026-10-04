import React, { useState } from "react";

export interface DataPoint {
  x: number | string;
  y: number;
}

export interface Series {
  name: string;
  color?: string;
  data: DataPoint[];
}

interface LineChartProps {
  series: Series[];
  height?: number;
  xAxisLabel?: string;
  yAxisLabel?: string;
  yMin?: number;
  yMax?: number;
  className?: string;
}

export const LineChart: React.FC<LineChartProps> = ({
  series,
  height = 240,
  xAxisLabel = "Steps",
  yAxisLabel = "Value",
  yMin,
  yMax,
  className = "",
}) => {
  const [hoveredPoint, setHoveredPoint] = useState<{
    seriesName: string;
    x: number | string;
    y: number;
    screenX: number;
    screenY: number;
  } | null>(null);

  if (!series || series.length === 0 || series.every((s) => s.data.length === 0)) {
    return (
      <div
        style={{ height }}
        className={`flex items-center justify-center border border-dashed border-rex-border rounded-lg text-rex-muted text-xs ${className}`}
      >
        No metric progression data available.
      </div>
    );
  }

  // Calculate bounds
  let minY = yMin !== undefined ? yMin : Infinity;
  let maxY = yMax !== undefined ? yMax : -Infinity;
  let maxPoints = 0;

  series.forEach((s) => {
    maxPoints = Math.max(maxPoints, s.data.length);
    s.data.forEach((p) => {
      if (p.y < minY) minY = p.y;
      if (p.y > maxY) maxY = p.y;
    });
  });

  if (minY === Infinity) minY = 0;
  if (maxY === -Infinity) maxY = 1;
  if (minY === maxY) {
    minY -= 1;
    maxY += 1;
  }

  // Add 5% padding to Y axis
  const paddingY = (maxY - minY) * 0.05;
  const effMinY = Math.max(0, minY - paddingY);
  const effMaxY = maxY + paddingY;

  const width = 600;
  const padding = { top: 20, right: 30, bottom: 35, left: 45 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  const defaultColors = ["#60A5FA", "#34D399", "#A78BFA", "#FBBF24", "#F87171"];

  // Y-axis grid ticks (4 ticks)
  const yTicks = [
    effMinY,
    effMinY + (effMaxY - effMinY) * 0.33,
    effMinY + (effMaxY - effMinY) * 0.66,
    effMaxY,
  ];

  return (
    <div className={`relative w-full ${className}`}>
      {/* Legend */}
      <div className="flex flex-wrap items-center gap-4 mb-2 text-xs font-mono">
        {series.map((s, idx) => {
          const color = s.color || defaultColors[idx % defaultColors.length];
          return (
            <div key={s.name} className="flex items-center gap-1.5">
              <span className="w-2.5 h-0.5 rounded-full" style={{ backgroundColor: color }} />
              <span className="text-rex-secondary">{s.name}</span>
            </div>
          );
        })}
      </div>

      {/* SVG Canvas */}
      <div className="w-full overflow-hidden">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="w-full h-auto"
          style={{ maxHeight: height }}
        >
          {/* Y Grid lines */}
          {yTicks.map((val, idx) => {
            const y = padding.top + chartHeight - ((val - effMinY) / (effMaxY - effMinY)) * chartHeight;
            return (
              <g key={idx}>
                <line
                  x1={padding.left}
                  y1={y}
                  x2={padding.left + chartWidth}
                  y2={y}
                  stroke="currentColor"
                  className="text-rex-border/60"
                  strokeDasharray="3 3"
                  strokeWidth="1"
                />
                <text
                  x={padding.left - 8}
                  y={y + 3}
                  textAnchor="end"
                  className="text-[9px] fill-rex-muted font-mono"
                >
                  {val >= 10 ? val.toFixed(1) : val.toFixed(3)}
                </text>
              </g>
            );
          })}

          {/* Series paths */}
          {series.map((s, idx) => {
            if (s.data.length === 0) return null;
            const color = s.color || defaultColors[idx % defaultColors.length];

            const points = s.data.map((p, pIdx) => {
              const xRatio = s.data.length > 1 ? pIdx / (s.data.length - 1) : 0.5;
              const x = padding.left + xRatio * chartWidth;
              const y =
                padding.top +
                chartHeight -
                ((p.y - effMinY) / (effMaxY - effMinY)) * chartHeight;
              return { x, y, raw: p };
            });

            const pathD = points.reduce(
              (acc, pt, i) => `${acc} ${i === 0 ? "M" : "L"} ${pt.x},${pt.y}`,
              ""
            );

            return (
              <g key={s.name}>
                <path
                  d={pathD}
                  fill="none"
                  stroke={color}
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                {points.map((pt, pIdx) => (
                  <circle
                    key={pIdx}
                    cx={pt.x}
                    cy={pt.y}
                    r="3"
                    fill={color}
                    className="cursor-pointer hover:r-5 transition-all"
                    onMouseEnter={(e) => {
                      const rect = e.currentTarget.getBoundingClientRect();
                      setHoveredPoint({
                        seriesName: s.name,
                        x: pt.raw.x,
                        y: pt.raw.y,
                        screenX: rect.left,
                        screenY: rect.top,
                      });
                    }}
                    onMouseLeave={() => setHoveredPoint(null)}
                  />
                ))}
              </g>
            );
          })}

          {/* X Axis label */}
          <text
            x={padding.left + chartWidth / 2}
            y={height - 5}
            textAnchor="middle"
            className="text-[10px] fill-rex-muted"
          >
            {xAxisLabel}
          </text>
        </svg>
      </div>

      {/* Tooltip */}
      {hoveredPoint && (
        <div
          className="absolute z-20 pointer-events-none bg-rex-elevated border border-rex-border rounded px-2.5 py-1.5 shadow-lg text-[11px] font-mono text-rex-primary"
          style={{
            top: 20,
            right: 20,
          }}
        >
          <div className="font-semibold text-rex-primary">{hoveredPoint.seriesName}</div>
          <div className="text-rex-secondary">
            Step: {hoveredPoint.x} | Value: {hoveredPoint.y.toFixed(4)}
          </div>
        </div>
      )}
    </div>
  );
};
