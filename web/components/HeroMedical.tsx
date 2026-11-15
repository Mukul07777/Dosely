"use client";

import { motion, useReducedMotion } from "framer-motion";

const ECG = "M0 60 H430 L455 60 L470 30 L490 96 L510 14 L530 84 L545 60 H1200";

// One quiet medical cue behind the hero word: a thin heartbeat line that draws itself once, then rests.
// Decorative only (hidden from screen readers), and drawn instantly for people who ask for reduced motion.
export function HeroMedical() {
  const still = useReducedMotion();
  return (
    <svg aria-hidden className="pointer-events-none absolute inset-x-0 top-1/2 h-20 w-full -translate-y-1/2 sm:h-28" viewBox="0 0 1200 120" preserveAspectRatio="none">
      <path d={ECG} fill="none" stroke="var(--brown)" strokeOpacity="0.08" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      <motion.path d={ECG} fill="none" stroke="var(--danger)" strokeOpacity="0.45" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
        vectorEffect="non-scaling-stroke" initial={{ pathLength: still ? 1 : 0 }} animate={{ pathLength: 1 }}
        transition={{ duration: still ? 0 : 2.2, ease: [0.22, 1, 0.36, 1], delay: 0.3 }} />
    </svg>
  );
}
