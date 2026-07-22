"use client";

// FE-007 ROOT FIX (Teammate 14, HIGH): add "use client" directive.
//
// ROOT CAUSE: this hook calls React.useState and React.useEffect. Next.js 16
// App Router requires any module that uses React hooks to declare "use client"
// at the top — UNLESS the file is only imported by other client modules.
// use-mobile.ts had no directive. shadcn/ui components (and others) import
// useIsMobile; if any of them render in a Server Component context, the build
// fails with "useState is not a function" or silently breaks at runtime.
//
// ROOT FIX: declare "use client" at line 1. This makes the directive
// self-contained — the file is safe to import from any context (server or
// client). Next.js will bundle it as a client module automatically.

import * as React from "react"

const MOBILE_BREAKPOINT = 768

export function useIsMobile() {
  const [isMobile, setIsMobile] = React.useState<boolean | undefined>(undefined)

  React.useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`)
    const onChange = () => {
      setIsMobile(window.innerWidth < MOBILE_BREAKPOINT)
    }
    mql.addEventListener("change", onChange)
    setIsMobile(window.innerWidth < MOBILE_BREAKPOINT)
    return () => mql.removeEventListener("change", onChange)
  }, [])

  return !!isMobile
}
