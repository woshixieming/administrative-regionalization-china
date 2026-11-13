"use client";

import { useEffect, useRef } from "react";
import { createInkFluid } from "@/lib/ink-fluid";

export default function InkBackground() {
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    element.classList.remove("is-ready");
    const fluid = createInkFluid(element);
    if (!fluid) return;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0, warmup = 135, previous = 0, disposed = false;

    function tick(now: number) {
      if (disposed || document.hidden) return;
      if (warmup > 0) {
        // 135 × 1/30 s = 4.5 s of simulation, computed before revealing the canvas.
        for (let i = 0; i < 5 && warmup > 0; i++, warmup--) fluid!.step();
        if (warmup === 0) {
          fluid!.render();
          element!.classList.add("is-ready");
        }
      } else if (now - previous >= 1000 / 24) {
        fluid!.step(Math.min((now - previous) / 1000 || 1 / 24, 1 / 20));
        fluid!.render();
        previous = now;
      }
      if (warmup > 0 || !reducedMotion.matches) frame = requestAnimationFrame(tick);
    }

    function restart() {
      cancelAnimationFrame(frame);
      if (!document.hidden && !disposed) { previous = 0; frame = requestAnimationFrame(tick); }
    }

    function resize() {
      try {
        element!.classList.remove("is-ready");
        fluid!.resize();
        fluid!.render();
        warmup = 135;
        restart();
      } catch {
        cancelAnimationFrame(frame);
        element!.classList.remove("is-ready");
      }
    }

    function contextLost(event: Event) {
      event.preventDefault();
      disposed = true;
      cancelAnimationFrame(frame);
      element!.classList.remove("is-ready");
    }

    const observer = new ResizeObserver(resize);
    observer.observe(element);
    document.addEventListener("visibilitychange", restart);
    reducedMotion.addEventListener("change", restart);
    element.addEventListener("webglcontextlost", contextLost);
    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      document.removeEventListener("visibilitychange", restart);
      reducedMotion.removeEventListener("change", restart);
      element.removeEventListener("webglcontextlost", contextLost);
      element.classList.remove("is-ready");
      fluid.dispose();
    };
  }, []);

  return <div className="ink-background" aria-hidden="true"><canvas ref={canvas} /></div>;
}
