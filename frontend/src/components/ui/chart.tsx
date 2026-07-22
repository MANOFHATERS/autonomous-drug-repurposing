"use client"

import * as React from "react"
import * as RechartsPrimitive from "recharts"
// FE-024/swim-lane ROOT FIX (Teammate 13, hostile-auditor): Recharts v3
// changed its TypeScript types. The previous code used
// `React.ComponentProps<typeof RechartsPrimitive.Tooltip>` which resolves to
// Recharts' `TooltipProps` — but `TooltipProps` deliberately OMITS `active`,
// `payload`, and `label` (they're listed in `PropertiesReadFromContext` and
// moved to `TooltipContentProps`). The result: `tsc --noEmit` reported 8
// errors (TS2339 Property 'payload'/'label' does not exist, TS7006 implicit
// any on item/index, TS2344 Pick constraint failure, TS2339 .length/.map on
// {}). The runtime code worked because Recharts injects those props at
// runtime — but the types were lying.
//
// ROOT FIX: import the proper v3 types and define explicit prop interfaces.
// `TooltipContentProps` (from recharts) DOES include `active`, `payload`,
// `label`. `TooltipPayload` is `ReadonlyArray<TooltipPayloadEntry>`. We Pick
// the three runtime-injected fields and merge with the div props + custom
// props. For the legend, `LegendPayload` is the proper item type; we extend
// it with `dataKey` (which Recharts passes at runtime but doesn't declare
// in the type — this is a known Recharts v3 type gap).
import type {
  TooltipContentProps,
  TooltipPayload,
  TooltipPayloadEntry,
  LegendPayload,
} from "recharts"

import { cn } from "@/lib/utils"

// Format: { THEME_NAME: CSS_SELECTOR }
const THEMES = { light: "", dark: ".dark" } as const

export type ChartConfig = {
  [k in string]: {
    label?: React.ReactNode
    icon?: React.ComponentType
  } & (
    | { color?: string; theme?: never }
    | { color?: never; theme: Record<keyof typeof THEMES, string> }
  )
}

type ChartContextProps = {
  config: ChartConfig
}

const ChartContext = React.createContext<ChartContextProps | null>(null)

function useChart() {
  const context = React.useContext(ChartContext)

  if (!context) {
    throw new Error("useChart must be used within a <ChartContainer />")
  }

  return context
}

function ChartContainer({
  id,
  className,
  children,
  config,
  ...props
}: React.ComponentProps<"div"> & {
  config: ChartConfig
  children: React.ComponentProps<
    typeof RechartsPrimitive.ResponsiveContainer
  >["children"]
}) {
  const uniqueId = React.useId()
  const chartId = `chart-${id || uniqueId.replace(/:/g, "")}`

  return (
    <ChartContext.Provider value={{ config }}>
      <div
        data-slot="chart"
        data-chart={chartId}
        className={cn(
          "[&_.recharts-cartesian-axis-tick_text]:fill-muted-foreground [&_.recharts-cartesian-grid_line[stroke='#ccc']]:stroke-border/50 [&_.recharts-curve.recharts-tooltip-cursor]:stroke-border [&_.recharts-polar-grid_[stroke='#ccc']]:stroke-border [&_.recharts-radial-bar-background-sector]:fill-muted [&_.recharts-rectangle.recharts-tooltip-cursor]:fill-muted [&_.recharts-reference-line_[stroke='#ccc']]:stroke-border flex aspect-video justify-center text-xs [&_.recharts-dot[stroke='#fff']]:stroke-transparent [&_.recharts-layer]:outline-hidden [&_.recharts-sector]:outline-hidden [&_.recharts-sector[stroke='#fff']]:stroke-transparent [&_.recharts-surface]:outline-hidden",
          className
        )}
        {...props}
      >
        <ChartStyle id={chartId} config={config} />
        <RechartsPrimitive.ResponsiveContainer>
          {children}
        </RechartsPrimitive.ResponsiveContainer>
      </div>
    </ChartContext.Provider>
  )
}

const ChartStyle = ({ id, config }: { id: string; config: ChartConfig }) => {
  const colorConfig = Object.entries(config).filter(
    ([, config]) => config.theme || config.color
  )

  if (!colorConfig.length) {
    return null
  }

  // FE-021 ROOT FIX (Teammate 16, hostile-auditor): the previous code
  // interpolated `color` directly into a CSS `<style>` tag via
  // `dangerouslySetInnerHTML`. If `color` is attacker-controlled (e.g.,
  // from an API response that includes a user-specified chart color,
  // or from a future feature that lets users customize chart colors),
  // an attacker could inject:
  //   `red; } </style><script>alert(1)</script><style>body{color:`
  // to break out of the style tag and inject arbitrary HTML/JS. The
  // CSP allows 'unsafe-inline' (per FE-008) so the injected script
  // would execute. In practice, ChartConfig is currently hardcoded —
  // but if a future feature lets users customize chart colors, this
  // becomes a stored XSS.
  //
  // ROOT FIX (per FE-021 issue spec):
  //   1. Validate `color` against a strict whitelist regex BEFORE
  //      interpolation. The regex accepts:
  //        - Hex colors: #RGB, #RGBA, #RRGGBB, #RRGGBBAA (3-8 hex
  //          digits, case-insensitive, leading #).
  //        - CSS custom properties: var(--name) where name matches
  //          [-_a-zA-Z0-9]+.
  //        - Named CSS colors: a single token of letters and hyphens
  //          (e.g., "red", "dark-blue", "transparent", "inherit").
  //        - rgb()/rgba()/hsl()/hsl() function calls with strictly
  //          whitelisted characters (digits, commas, spaces, percent,
  //          decimal points, slashes for modern rgb() alpha syntax).
  //      Anything else is REJECTED and replaced with `transparent`
  //      (which is visually a no-op — the chart will show no color
  //      for that series, but no XSS payload executes).
  //   2. As a defense-in-depth, ALSO strip any `<`, `>`, `;`, `}`,
  //      `{`, `(`, `)`, `"`, `'`, `\\` characters from the color
  //      value before interpolation. Even if the regex is bypassed
  //      by a future change, the strip ensures no CSS-breaking or
  //      HTML-breaking characters can appear in the output.
  //
  // The regex is anchored (^...$) so the ENTIRE color string must
  // match the whitelist — no prefix/suffix injection.
  const SAFE_COLOR_RE = /^#[0-9a-fA-F]{3,8}$|^var\(--[-\w]+\)$|^[a-zA-Z-]+$|^rgba?\(\s*[0-9.%\s,/]+\)$|^hsla?\(\s*[0-9.%\s,/]+\)$/;
  const sanitizeColor = (raw: string | undefined): string => {
    if (!raw || typeof raw !== "string") return "transparent";
    // Defense-in-depth: strip HTML/CSS-breaking characters. Even if
    // the regex below accepts the value, we strip these to guarantee
    // no `<style>` breakout is possible.
    const stripped = raw.replace(/[<>;{}()"'\\]/g, "");
    if (SAFE_COLOR_RE.test(stripped)) {
      return stripped;
    }
    // Reject — return transparent (visually a no-op, no XSS).
    return "transparent";
  };
  // Also validate the `id` (used as a CSS attribute selector). Same
  // whitelist: letters, digits, hyphens. Anything else is rejected
  // and replaced with a safe placeholder.
  const SAFE_ID_RE = /^[-\w]+$/;
  const safeId = SAFE_ID_RE.test(id) ? id : "chart-invalid-id";

  return (
    <style
      dangerouslySetInnerHTML={{
        __html: Object.entries(THEMES)
          .map(
            ([theme, prefix]) => `
${prefix} [data-chart=${safeId}] {
${colorConfig
  .map(([key, itemConfig]) => {
    const rawColor =
      itemConfig.theme?.[theme as keyof typeof itemConfig.theme] ||
      itemConfig.color
    if (!rawColor) return null
    const color = sanitizeColor(rawColor)
    // FE-021: also validate the `key` — it's interpolated into a CSS
    // custom property name (--color-${key}). Same whitelist as id.
    const safeKey = SAFE_ID_RE.test(key) ? key : "invalid-key"
    return `  --color-${safeKey}: ${color};`
  })
  .join("\n")}
}
`
          )
          .join("\n"),
      }}
    />
  )
}

const ChartTooltip = RechartsPrimitive.Tooltip

// ChartTooltipContent prop type — ROOT FIX for Recharts v3 type gap.
// `active`, `payload`, `label` come from `TooltipContentProps` (the runtime
// props Recharts injects). `formatter`/`labelFormatter` signatures match
// Recharts' own `Formatter` type so consumer functions type-check.
type ChartTooltipContentProps = Pick<
  TooltipContentProps,
  "active" | "payload" | "label"
> &
  Omit<React.ComponentProps<"div">, "color"> & {
    hideLabel?: boolean
    hideIndicator?: boolean
    indicator?: "line" | "dot" | "dashed"
    nameKey?: string
    labelKey?: string
    labelFormatter?: (
      value: React.ReactNode,
      payload: TooltipPayload
    ) => React.ReactNode
    labelClassName?: string
    formatter?: (
      value: TooltipPayloadEntry["value"],
      name: TooltipPayloadEntry["name"],
      item: TooltipPayloadEntry,
      index: number,
      payload: TooltipPayloadEntry["payload"]
    ) => React.ReactNode
    color?: string
  }

function ChartTooltipContent({
  active,
  payload,
  className,
  indicator = "dot",
  hideLabel = false,
  hideIndicator = false,
  label,
  labelFormatter,
  labelClassName,
  formatter,
  color,
  nameKey,
  labelKey,
}: ChartTooltipContentProps) {
  const { config } = useChart()

  const tooltipLabel = React.useMemo(() => {
    if (hideLabel || !payload?.length) {
      return null
    }

    const [item] = payload
    const key = `${labelKey || item?.dataKey || item?.name || "value"}`
    const itemConfig = getPayloadConfigFromPayload(config, item, key)
    const value =
      !labelKey && typeof label === "string"
        ? config[label as keyof typeof config]?.label || label
        : itemConfig?.label

    if (labelFormatter) {
      return (
        <div className={cn("font-medium", labelClassName)}>
          {labelFormatter(value, payload)}
        </div>
      )
    }

    if (!value) {
      return null
    }

    return <div className={cn("font-medium", labelClassName)}>{value}</div>
  }, [
    label,
    labelFormatter,
    payload,
    hideLabel,
    labelClassName,
    config,
    labelKey,
  ])

  if (!active || !payload?.length) {
    return null
  }

  const nestLabel = payload.length === 1 && indicator !== "dot"

  return (
    <div
      className={cn(
        "border-border/50 bg-background grid min-w-[8rem] items-start gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs shadow-xl",
        className
      )}
    >
      {!nestLabel ? tooltipLabel : null}
      <div className="grid gap-1.5">
        {payload.map((item, index) => {
          const key = `${nameKey || item.name || item.dataKey || "value"}`
          const itemConfig = getPayloadConfigFromPayload(config, item, key)
          const indicatorColor = color || item.payload.fill || item.color

          return (
            <div
              key={String(item.dataKey ?? index)}
              className={cn(
                "[&>svg]:text-muted-foreground flex w-full flex-wrap items-stretch gap-2 [&>svg]:h-2.5 [&>svg]:w-2.5",
                indicator === "dot" && "items-center"
              )}
            >
              {formatter && item?.value !== undefined && item.name ? (
                formatter(item.value, item.name, item, index, item.payload)
              ) : (
                <>
                  {itemConfig?.icon ? (
                    <itemConfig.icon />
                  ) : (
                    !hideIndicator && (
                      <div
                        className={cn(
                          "shrink-0 rounded-[2px] border-(--color-border) bg-(--color-bg)",
                          {
                            "h-2.5 w-2.5": indicator === "dot",
                            "w-1": indicator === "line",
                            "w-0 border-[1.5px] border-dashed bg-transparent":
                              indicator === "dashed",
                            "my-0.5": nestLabel && indicator === "dashed",
                          }
                        )}
                        style={
                          {
                            "--color-bg": indicatorColor,
                            "--color-border": indicatorColor,
                          } as React.CSSProperties
                        }
                      />
                    )
                  )}
                  <div
                    className={cn(
                      "flex flex-1 justify-between leading-none",
                      nestLabel ? "items-end" : "items-center"
                    )}
                  >
                    <div className="grid gap-1.5">
                      {nestLabel ? tooltipLabel : null}
                      <span className="text-muted-foreground">
                        {itemConfig?.label || item.name}
                      </span>
                    </div>
                    {item.value && (
                      <span className="text-foreground font-mono font-medium tabular-nums">
                        {item.value.toLocaleString()}
                      </span>
                    )}
                  </div>
                </>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

const ChartLegend = RechartsPrimitive.Legend

// ChartLegendContent prop type — ROOT FIX for Recharts v3 type gap.
// `LegendPayload` is Recharts v3's proper legend item type. We extend it
// with `dataKey` because Recharts passes `dataKey` at runtime (the legend
// item is built from the series config) but the v3 type doesn't declare it.
// `verticalAlign` is typed as a union matching Recharts' runtime values.
type ChartLegendItem = LegendPayload & {
  dataKey?: string | number
}

type ChartLegendContentProps = Omit<React.ComponentProps<"div">, "color"> & {
  payload?: ChartLegendItem[]
  verticalAlign?: "top" | "bottom" | "middle"
  hideIcon?: boolean
  nameKey?: string
  color?: string
}

function ChartLegendContent({
  className,
  hideIcon = false,
  payload,
  verticalAlign = "bottom",
  nameKey,
}: ChartLegendContentProps) {
  const { config } = useChart()

  if (!payload?.length) {
    return null
  }

  return (
    <div
      className={cn(
        "flex items-center justify-center gap-4",
        verticalAlign === "top" ? "pb-3" : "pt-3",
        className
      )}
    >
      {payload.map((item) => {
        const key = `${nameKey || item.dataKey || "value"}`
        const itemConfig = getPayloadConfigFromPayload(config, item, key)

        return (
          <div
            key={item.value}
            className={cn(
              "[&>svg]:text-muted-foreground flex items-center gap-1.5 [&>svg]:h-3 [&>svg]:w-3"
            )}
          >
            {itemConfig?.icon && !hideIcon ? (
              <itemConfig.icon />
            ) : (
              <div
                className="h-2 w-2 shrink-0 rounded-[2px]"
                style={{
                  backgroundColor: item.color,
                }}
              />
            )}
            {itemConfig?.label}
          </div>
        )
      })}
    </div>
  )
}

// Helper to extract item config from a payload.
function getPayloadConfigFromPayload(
  config: ChartConfig,
  payload: unknown,
  key: string
) {
  if (typeof payload !== "object" || payload === null) {
    return undefined
  }

  const payloadPayload =
    "payload" in payload &&
    typeof payload.payload === "object" &&
    payload.payload !== null
      ? payload.payload
      : undefined

  let configLabelKey: string = key

  if (
    key in payload &&
    typeof payload[key as keyof typeof payload] === "string"
  ) {
    configLabelKey = payload[key as keyof typeof payload] as string
  } else if (
    payloadPayload &&
    key in payloadPayload &&
    typeof payloadPayload[key as keyof typeof payloadPayload] === "string"
  ) {
    configLabelKey = payloadPayload[
      key as keyof typeof payloadPayload
    ] as string
  }

  return configLabelKey in config
    ? config[configLabelKey]
    : config[key as keyof typeof config]
}

export {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  ChartLegend,
  ChartLegendContent,
  ChartStyle,
}
